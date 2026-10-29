[CmdletBinding()]
param(
    [string] $Devices = '',
    [switch] $Rebuild,
    [int] $StartupTimeoutSeconds = 90
)

# Windows entry point for both the Unity system screen and direct PowerShell use.
$ErrorActionPreference = 'Stop'
$script:ComposeFile = Join-Path $PSScriptRoot '..\docker-compose.windows.yml'
$script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:SelectedDevices = @($Devices -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
foreach ($device in $script:SelectedDevices) {
    if ($device -notin @('guidao', 'pulmonary', 'emotiv', 'noraxon')) {
        throw "Dispositivo desconhecido: $device"
    }
}

function Write-RehabStatus {
    param([int] $Percent, [string] $Message)
    # Stable, machine-readable channel consumed by SystemLauncherController.
    Write-Output ("##REHAB_STATUS##|{0}|{1}" -f $Percent, $Message.Replace("`r", ' ').Replace("`n", ' '))
}

function Import-RehabEnvironment {
    $envFile = Join-Path $script:RepositoryRoot '.env'
    if (-not (Test-Path $envFile)) { return }
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $key, $value = $Matches[1], $Matches[2]
            if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($key, 'Process'))) {
                if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[$value.Length - 1] -eq '"') -or ($value[0] -eq "'" -and $value[$value.Length - 1] -eq "'"))) {
                    $value = $value.Substring(1, $value.Length - 2)
                }
                [Environment]::SetEnvironmentVariable($key, $value, 'Process')
            }
        }
    }
}

function Invoke-Checked {
    param([string] $FilePath, [string[]] $Arguments, [string] $FailureMessage)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$FailureMessage (exit code $LASTEXITCODE)." }
}

function Get-UsbipdDevices {
    $lines = & usbipd list
    if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel consultar usbipd. Instale usbipd-win e abra o Docker Desktop.' }
    return @($lines | Where-Object { $_ -match '^\s*\d+-\d+(\.\d+)*\s+' })
}

function Resolve-UsbDevice {
    param([string] $Label, [string] $MatchPattern)
    $matches = @(Get-UsbipdDevices | Where-Object { $_ -match $MatchPattern })
    if ($matches.Count -eq 0) {
        throw "Nao encontrei o USB do $Label. Conecte-o e confira o padrao REHAB_$($Label.ToUpper())_USB_MATCH no .env."
    }

    # An already attached matching device wins, so re-launches remain stable even
    # if another serial adapter is connected later.
    $attached = @($matches | Where-Object { $_ -match '\bAttached\b' })
    if ($attached.Count -eq 1) { $matches = $attached }
    if ($matches.Count -ne 1) {
        $found = $matches -join '; '
        throw "Encontrei mais de um USB compativel com ${Label}: $found. Torne o padrao REHAB_$($Label.ToUpper())_USB_MATCH mais especifico no .env."
    }

    $line = $matches[0]
    if ($line -notmatch '^\s*(?<busid>\d+-\d+(\.\d+)*)\s+') {
        throw "Nao foi possivel ler o BUSID do USB do $Label."
    }
    return @{ BusId = $Matches.busid; Line = $line }
}

function Get-UsbipdLine {
    param([string] $BusId)
    $lines = & usbipd list
    if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel consultar usbipd. Instale usbipd-win e abra o Docker Desktop.' }
    $line = $lines | Where-Object { $_ -match ("^\s*" + [regex]::Escape($BusId) + "\s+") } | Select-Object -First 1
    if (-not $line) { throw "USB com BUSID $BusId nao foi encontrado. Confira o cabo ou o BUSID exibido por usbipd list." }
    return $line
}

function Ensure-UsbAttached {
    param([string] $Label, [string] $MatchPattern)
    $device = Resolve-UsbDevice $Label $MatchPattern
    $BusId = $device.BusId
    Write-RehabStatus 15 "Preparando USB do $Label ($BusId)..."
    $line = $device.Line
    if ($line -notmatch '\b(Shared|Attached)\b') {
        # usbipd bind changes Windows USB sharing and therefore requires elevation.
        # The UAC prompt only appears on the first binding/rebinding.
        Write-RehabStatus 18 "Solicitando permissao do Windows para compartilhar o USB do $Label..."
        $process = Start-Process -FilePath 'usbipd' -ArgumentList @('bind', '--busid', $BusId, '--force') -Verb RunAs -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "O compartilhamento USB do $Label foi cancelado ou falhou." }
        $line = Get-UsbipdLine $BusId
    }
    if ($line -notmatch '\bAttached\b') {
        Invoke-Checked 'usbipd' @('attach', '--wsl', '--busid', $BusId) "Nao foi possivel conectar o USB do $Label ao WSL2"
    }
}

function Test-RehabTcpEndpoint {
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $connect = $client.BeginConnect('127.0.0.1', 10000, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(1000)) { $client.Close(); return $false }
        $client.EndConnect($connect)
        $client.Close()
        return $true
    } catch { return $false }
}

function Stop-PreviousNoraxonSender {
    # Only stop our TCP sender, never the user's Noraxon Acquire application.
    $pidFile = Join-Path $script:RepositoryRoot 'logs\noraxon-sender.pid'
    $senderPids = @()
    if (Test-Path $pidFile) {
        $savedPid = Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($savedPid -match '^\d+$') { $senderPids += [int]$savedPid }
    }

    # Also find senders left by launcher versions that did not persist a PID.
    try {
        $processes = Get-CimInstance Win32_Process -ErrorAction Stop
        $legacySenders = @($processes | Where-Object { $_.CommandLine -and $_.CommandLine -like '*noraxon_tcp_sender.py*' } | ForEach-Object { [int]$_.ProcessId })
        $senderPids += $legacySenders
    } catch {
        Write-RehabStatus 4 'Nao foi possivel consultar processos Noraxon antigos; usando o PID salvo, se houver.'
    }

    foreach ($senderPid in @($senderPids | Sort-Object -Unique)) {
        if ($senderPid -ne $PID) {
            & taskkill.exe /PID $senderPid /T /F *> $null
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

function Start-NoraxonSender {
    $command = $env:NORAXON_SENDER_COMMAND
    if ([string]::IsNullOrWhiteSpace($command)) {
        throw 'Noraxon foi selecionado, mas NORAXON_SENDER_COMMAND nao esta definido. Configure o comando do SDK antes de iniciar.'
    }
    if (-not [string]::IsNullOrWhiteSpace($env:NORAXON_SDK_PATH)) {
        # The SDK uses imports from its own repository. Preserve a pre-existing
        # PYTHONPATH while ensuring the sender child process can import it.
        $pythonPath = $env:NORAXON_SDK_PATH
        if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) { $pythonPath += ";$env:PYTHONPATH" }
        [Environment]::SetEnvironmentVariable('PYTHONPATH', $pythonPath, 'Process')
    }
    # The Noraxon SDK writes Unicode symbols (for example, arrows) to stdout.
    # The default Windows console code page may be cp1252 and crash Python while
    # printing SDK diagnostics, before the stream starts.
    [Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONIOENCODING', 'utf-8', 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONUNBUFFERED', '1', 'Process')
    Write-RehabStatus 80 'Iniciando o emissor Noraxon configurado...'
    $logDirectory = Join-Path $script:RepositoryRoot 'logs'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $stdout = Join-Path $logDirectory 'noraxon-sender.log'
    $stderr = Join-Path $logDirectory 'noraxon-sender-error.log'
    $script:NoraxonSenderProcess = Start-Process -FilePath 'cmd.exe' `
        -ArgumentList @('/d', '/c', $command) `
        -WorkingDirectory $script:RepositoryRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    Set-Content -LiteralPath (Join-Path $logDirectory 'noraxon-sender.pid') `
        -Value $script:NoraxonSenderProcess.Id -Encoding Ascii
}

function Test-NoraxonStreamReady {
    $senderLog = Join-Path $script:RepositoryRoot 'logs\noraxon-sender.log'
    if (-not (Test-Path $senderLog)) { return $false }
    return [bool](Select-String -Path $senderLog -Pattern 'NORAXON_RAW_STREAM_ACTIVE' -SimpleMatch -Quiet)
}

function Invoke-EmotivCleanup {
    if ([string]::IsNullOrWhiteSpace($env:EMOTIV_CLIENT_ID) -or [string]::IsNullOrWhiteSpace($env:EMOTIV_CLIENT_SECRET)) {
        throw 'Emotiv foi selecionado, mas EMOTIV_CLIENT_ID e EMOTIV_CLIENT_SECRET nao estao definidos.'
    }
    $python = if ($env:EMOTIV_PYTHON) { $env:EMOTIV_PYTHON } else { 'py' }
    $arguments = if ($env:EMOTIV_PYTHON) {
        @((Join-Path $script:RepositoryRoot 'emotiv_cleanup.py'), '--client-id', $env:EMOTIV_CLIENT_ID, '--client-secret', $env:EMOTIV_CLIENT_SECRET)
    } else {
        @('-3', (Join-Path $script:RepositoryRoot 'emotiv_cleanup.py'), '--client-id', $env:EMOTIV_CLIENT_ID, '--client-secret', $env:EMOTIV_CLIENT_SECRET)
    }
    Write-RehabStatus 25 'Limpando sessoes antigas do Emotiv...'
    Invoke-Checked $python $arguments 'A limpeza de sessoes do Emotiv falhou. Confirme que o EMOTIV Launcher/Cortex esta aberto'
}

try {
    Import-RehabEnvironment
    if ($script:SelectedDevices -contains 'noraxon') { Stop-PreviousNoraxonSender }
    Write-RehabStatus 2 'Verificando Docker Desktop...'
    Invoke-Checked 'docker' @('version', '--format', '{{.Server.Version}}') 'Docker Desktop nao esta disponivel'

    if ($script:SelectedDevices -contains 'guidao') {
        $guidaoPattern = if ($env:REHAB_GUIDAO_USB_MATCH) { $env:REHAB_GUIDAO_USB_MATCH } else { 'Guidao|Guidão|Arduino.*(M0|Zero|Uno)' }
        Ensure-UsbAttached 'guidao' $guidaoPattern
    }
    if ($script:SelectedDevices -contains 'pulmonary') {
        $pulmonaryPattern = if ($env:REHAB_PULMONARY_USB_MATCH) { $env:REHAB_PULMONARY_USB_MATCH } else { 'Pulmonary|Pulmonar|Respir|ESP32|CP210|CH340' }
        Ensure-UsbAttached 'pulmonary' $pulmonaryPattern
    }
    if ($script:SelectedDevices -contains 'emotiv') { Invoke-EmotivCleanup }

    & docker image inspect rehab-platform:humble *> $null
    $imageExists = $LASTEXITCODE -eq 0
    if ($Rebuild -or -not $imageExists) {
        Write-RehabStatus 35 'Construindo a imagem ROS (a primeira execucao pode demorar)...'
        Push-Location $script:RepositoryRoot
        try { Invoke-Checked 'docker' @('build', '-t', 'rehab-platform:humble', '.') 'Falha ao construir a imagem ROS' }
        finally { Pop-Location }
    }

    Write-RehabStatus 60 'Reiniciando a plataforma ROS com os dispositivos selecionados...'
    $compose = @('compose', '-f', $script:ComposeFile)
    Invoke-Checked 'docker' ($compose + @('down', '--remove-orphans')) 'Falha ao parar a execucao anterior'
    foreach ($device in $script:SelectedDevices) { $compose += @('--profile', $device) }
    Invoke-Checked 'docker' ($compose + @('up', '-d', '--remove-orphans')) 'Falha ao iniciar os containers'

    if ($script:SelectedDevices -contains 'noraxon') { Start-NoraxonSender }

    Write-RehabStatus 85 'Aguardando o endpoint ROS para a Unity...'
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $endpointReady = $false
    $noraxonReady = -not ($script:SelectedDevices -contains 'noraxon')
    while ((Get-Date) -lt $deadline) {
        if (-not $endpointReady) { $endpointReady = Test-RehabTcpEndpoint }
        if (-not $noraxonReady) {
            if ($script:NoraxonSenderProcess.HasExited) {
                $senderError = Get-Content (Join-Path $script:RepositoryRoot 'logs\noraxon-sender-error.log') -Raw -ErrorAction SilentlyContinue
                $senderOutput = Get-Content (Join-Path $script:RepositoryRoot 'logs\noraxon-sender.log') -Raw -ErrorAction SilentlyContinue
                throw "O emissor Noraxon encerrou antes de iniciar o streaming. Veja logs\noraxon-sender-error.log e logs\noraxon-sender.log. $senderError $senderOutput"
            }
            $noraxonReady = Test-NoraxonStreamReady
            if (-not $noraxonReady) {
                Write-RehabStatus 90 'Aguardando o Noraxon Acquire iniciar o streaming dos canais...'
            }
        }
        if ($endpointReady -and $noraxonReady) {
            Write-RehabStatus 100 'Sistema pronto.'
            exit 0
        }
        Start-Sleep -Seconds 1
    }
    if (-not $endpointReady) {
        throw "O endpoint ROS na porta 10000 nao ficou pronto em $StartupTimeoutSeconds segundos. Veja os logs com docker compose -f docker-compose.windows.yml logs."
    }
    if (-not $noraxonReady) {
        $senderError = Get-Content (Join-Path $script:RepositoryRoot 'logs\noraxon-sender-error.log') -Raw -ErrorAction SilentlyContinue
        $senderOutput = Get-Content (Join-Path $script:RepositoryRoot 'logs\noraxon-sender.log') -Raw -ErrorAction SilentlyContinue
        throw "O Noraxon Acquire nao iniciou o streaming dos sensores em $StartupTimeoutSeconds segundos. Confira o perfil/canais e os channel-id configurados no NORAXON_SENDER_COMMAND. logs\noraxon-sender-error.log: $senderError logs\noraxon-sender.log: $senderOutput"
    }
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
