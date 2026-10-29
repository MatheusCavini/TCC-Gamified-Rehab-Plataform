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

function Start-NoraxonSender {
    $command = $env:NORAXON_SENDER_COMMAND
    if ([string]::IsNullOrWhiteSpace($command)) {
        throw 'Noraxon foi selecionado, mas NORAXON_SENDER_COMMAND nao esta definido. Configure o comando do SDK com os channel-id reais antes de iniciar.'
    }
    Write-RehabStatus 80 'Iniciando o emissor Noraxon configurado...'
    Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $command) -WorkingDirectory $script:RepositoryRoot -WindowStyle Hidden | Out-Null
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
    while ((Get-Date) -lt $deadline) {
        if (Test-RehabTcpEndpoint) {
            Write-RehabStatus 100 'Sistema pronto.'
            exit 0
        }
        Start-Sleep -Seconds 1
    }
    throw "O endpoint ROS na porta 10000 nao ficou pronto em $StartupTimeoutSeconds segundos. Veja os logs com docker compose -f docker-compose.windows.yml logs."
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
