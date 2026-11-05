[CmdletBinding()]
param(
    [string] $Devices = '',
    [switch] $Rebuild,
    [int] $StartupTimeoutSeconds = 90,
    # 0 = use NORAXON_STARTUP_TIMEOUT_SECONDS from .env, or 180 s. Includes the
    # time the operator needs to confirm the Noraxon hardware setup dialog.
    [int] $NoraxonTimeoutSeconds = 0,
    # Tear everything down (all Compose profiles, stale containers, Noraxon
    # sender) and exit. Called by Unity when the application quits.
    [switch] $Shutdown
)

# Windows entry point for both the Unity system screen and direct PowerShell use.
# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less files as ANSI.
$ErrorActionPreference = 'Stop'
$script:ComposeFile = Join-Path $PSScriptRoot '..\docker-compose.windows.yml'
$script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script:NoraxonLog = Join-Path $script:RepositoryRoot 'logs\noraxon_sender.log'
# Fallbacks only; the live values are read from the Compose file when possible.
$script:KnownProfiles = @('guidao', 'pulmonary', 'emotiv', 'noraxon')
$script:KnownContainers = @('device_manager', 'signal_processing', 'noraxon_tcp_receiver', 'emotiv_eeg',
    'tcp_endpoint', 'guidao_encoder', 'guidao_controller', 'pulmonary_device', 'pulmonary_device_sim')
$script:RehabPorts = @(10000, 8765)
$script:SelectedDevices = @($Devices -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
foreach ($device in $script:SelectedDevices) {
    if ($device -notin @('guidao', 'pulmonary', 'emotiv', 'noraxon')) {
        throw "Dispositivo desconhecido: $device"
    }
}

try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }

# --- Output channel ---------------------------------------------------------
# [Console]::Out is used instead of Write-Output so status lines never leak
# into the return value of the function that emits them.

function Write-RehabStatus {
    param([int] $Percent, [string] $Message)
    # Stable, machine-readable channel consumed by SystemLauncherController.
    [Console]::Out.WriteLine(("##REHAB_STATUS##|{0}|{1}" -f $Percent, $Message.Replace("`r", ' ').Replace("`n", ' ')))
}

function Write-RehabLog {
    param([string] $Message)
    [Console]::Out.WriteLine($Message)
}

function Write-RehabFailure {
    param([string] $Message)
    $singleLine = $Message.Replace("`r", ' ').Replace("`n", ' ')
    [Console]::Out.WriteLine("##REHAB_ERROR##|$singleLine")
    [Console]::Error.WriteLine($Message)
}

# --- Environment ------------------------------------------------------------

function Update-ProcessPath {
    # Unity (or Unity Hub) keeps the PATH it had when it was opened. Merge the
    # current Machine/User PATH so tools installed afterwards (uv, usbipd,
    # docker) are still found.
    $entries = @(
        [Environment]::GetEnvironmentVariable('Path', 'Process'),
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User')
    ) -join ';' -split ';' | Where-Object { $_ } | Select-Object -Unique
    $env:Path = $entries -join ';'
}

function Import-RehabEnvironment {
    # Values are taken literally: no escape processing. Write Windows paths and
    # Noraxon channel IDs with SINGLE backslashes, optionally inside quotes.
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

# --- Native command helpers -------------------------------------------------

function Invoke-Checked {
    param([string] $FilePath, [string[]] $Arguments, [string] $FailureMessage)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$FailureMessage (exit code $LASTEXITCODE)." }
}

function Invoke-NativeCapture {
    param([string] $FilePath, [string[]] $Arguments, [switch] $StdoutOnly)
    # Windows PowerShell 5.1 turns redirected native stderr into a terminating
    # error when ErrorActionPreference is 'Stop'. Relax it for this call only.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($StdoutOnly) { $output = @(& $FilePath @Arguments 2>$null | ForEach-Object { "$_" }) }
        else { $output = @(& $FilePath @Arguments 2>&1 | ForEach-Object { "$_" }) }
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $output }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Get-ContainerLog {
    param([string] $Container)
    $result = Invoke-NativeCapture 'docker' @('logs', $Container)
    if ($result.ExitCode -ne 0) { return '' }
    return ($result.Output -join "`n")
}

function Get-ContainerStatus {
    param([string] $Container)
    $result = Invoke-NativeCapture 'docker' @('inspect', '-f', '{{.State.Status}}', $Container)
    if ($result.ExitCode -ne 0) { return 'missing' }
    return ($result.Output -join '').Trim()
}

# --- Cleanup of previous runs -----------------------------------------------

function Get-ComposeProfiles {
    $result = Invoke-NativeCapture 'docker' @('compose', '-f', $script:ComposeFile, 'config', '--profiles') -StdoutOnly
    $names = @($result.Output | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($result.ExitCode -ne 0 -or $names.Count -eq 0) { $names = $script:KnownProfiles }
    return $names
}

function Get-AllProfilesComposeArguments {
    # `docker compose down` only removes services whose profiles are enabled.
    # A plain `down` therefore leaves every optional device container (Noraxon
    # receiver, Emotiv, Guidao, pulmonary) running, so enable all of them.
    # (Never name a variable $profile: it is PowerShell's automatic $PROFILE.)
    $arguments = @('compose', '-f', $script:ComposeFile)
    foreach ($profileName in Get-ComposeProfiles) { $arguments += @('--profile', $profileName) }
    return $arguments
}

function Get-ComposeContainerNames {
    $names = New-Object System.Collections.Generic.List[string]
    foreach ($name in $script:KnownContainers) { $names.Add($name) }
    $result = Invoke-NativeCapture 'docker' (@(Get-AllProfilesComposeArguments) + @('config', '--format', 'json')) -StdoutOnly
    if ($result.ExitCode -eq 0) {
        try {
            $config = ($result.Output -join "`n") | ConvertFrom-Json
            foreach ($service in $config.services.PSObject.Properties) {
                $name = $service.Value.container_name
                if ($name -and -not $names.Contains($name)) { $names.Add($name) }
            }
        } catch { }
    }
    return $names.ToArray()
}

function Remove-StaleRehabContainers {
    # Containers with our fixed container_name values, or publishing our ports,
    # that `down` did not remove belong to another Compose project: a manual
    # run from a WSL clone, the Linux docker-compose.yml, or the project name
    # used before `name:` was pinned. They would make `up` fail with a name or
    # port conflict, so remove them regardless of project.
    $wanted = @(Get-ComposeContainerNames)
    $stale = New-Object System.Collections.Generic.List[string]
    $listing = Invoke-NativeCapture 'docker' @('ps', '-a', '--format', '{{.ID}} {{.Names}}') -StdoutOnly
    foreach ($line in $listing.Output) {
        $parts = "$line".Trim() -split '\s+', 2
        if ($parts.Count -eq 2 -and $wanted -contains $parts[1] -and -not $stale.Contains($parts[0])) { $stale.Add($parts[0]) }
    }
    foreach ($port in $script:RehabPorts) {
        $published = Invoke-NativeCapture 'docker' @('ps', '-q', '--filter', "publish=$port") -StdoutOnly
        foreach ($containerId in $published.Output) {
            $containerId = "$containerId".Trim()
            if ($containerId -and -not $stale.Contains($containerId)) { $stale.Add($containerId) }
        }
    }
    if ($stale.Count -eq 0) { return }
    Write-RehabLog "Removendo $($stale.Count) container(s) remanescente(s) de outra execucao..."
    $removed = Invoke-NativeCapture 'docker' (@('rm', '-f') + $stale.ToArray())
    if ($removed.ExitCode -ne 0) { throw "Nao foi possivel remover containers antigos: $($removed.Output -join ' ')" }
}

function Assert-RehabPortsFree {
    if (-not (Get-Command 'Get-NetTCPConnection' -ErrorAction SilentlyContinue)) { return }
    # Docker Desktop may take a moment to release a published port.
    $deadline = (Get-Date).AddSeconds(10)
    while ($true) {
        $busy = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $script:RehabPorts -contains $_.LocalPort })
        if ($busy.Count -eq 0) { return }
        $owners = @(foreach ($connection in $busy) {
            $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
            [pscustomobject]@{
                Port = $connection.LocalPort
                Name = if ($process) { $process.ProcessName } else { '?' }
                Id   = $connection.OwningProcess
            }
        })
        $dockerOwned = @($owners | Where-Object { $_.Name -like 'com.docker*' -or $_.Name -like 'docker*' -or $_.Name -like 'vpnkit*' })
        if ($dockerOwned.Count -eq $owners.Count -and (Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 500
            continue
        }
        $description = ($owners | Sort-Object Port, Id -Unique | ForEach-Object { "porta $($_.Port): $($_.Name) (PID $($_.Id))" }) -join '; '
        throw "Portas da plataforma ainda em uso apos a limpeza: $description. Se for wslrelay, ha um ROS rodando direto no WSL (encerre-o ou rode wsl --shutdown); caso contrario, feche o programa indicado."
    }
}

function Invoke-RehabCleanup {
    param([switch] $SkipPortCheck)
    Write-RehabStatus 5 'Encerrando execucoes anteriores da plataforma...'
    Invoke-Checked 'docker' (@(Get-AllProfilesComposeArguments) + @('down', '--remove-orphans', '--timeout', '5')) 'Falha ao parar a execucao anterior'
    Remove-StaleRehabContainers
    # The old receiver is gone now, so an old sender stops itself; then ensure it.
    Stop-NoraxonSender
    if (-not $SkipPortCheck) { Assert-RehabPortsFree }
}

# --- USB --------------------------------------------------------------------

function Get-UsbipdDevices {
    $lines = & usbipd list
    if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel consultar usbipd. Instale usbipd-win e abra o Docker Desktop.' }
    return @($lines | Where-Object { $_ -match '^\s*\d+-\d+(\.\d+)*\s+' })
}

function Resolve-UsbDevice {
    param([string] $Label, [string] $MatchPattern)
    # Note: never name a variable $matches - it is PowerShell's automatic
    # $Matches and is overwritten by every -match inside Where-Object.
    # The Noraxon Ultium receiver (16d0:069e) is used by Acquire on Windows and
    # must never be shared with WSL, whatever the configured pattern matches.
    $candidates = @(Get-UsbipdDevices | Where-Object { $_ -match $MatchPattern -and $_ -notmatch '16d0:069e' })
    if ($candidates.Count -eq 0) {
        throw "Nao encontrei o USB do $Label. Conecte-o e confira o padrao REHAB_$($Label.ToUpper())_USB_MATCH no .env."
    }

    # An already attached matching device wins, so re-launches remain stable even
    # if another serial adapter is connected later.
    $attached = @($candidates | Where-Object { $_ -match '\bAttached\b' })
    if ($attached.Count -eq 1) { $candidates = $attached }
    if ($candidates.Count -ne 1) {
        $found = $candidates -join '; '
        throw "Encontrei mais de um USB compativel com ${Label}: $found. Torne o padrao REHAB_$($Label.ToUpper())_USB_MATCH mais especifico no .env."
    }

    $line = $candidates[0]
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

# --- ROS endpoint -----------------------------------------------------------

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

function Wait-RosEndpoint {
    Write-RehabStatus 70 'Aguardando o endpoint ROS para a Unity...'
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-RehabTcpEndpoint) { return }
        Start-Sleep -Seconds 1
    }
    throw "O endpoint ROS na porta 10000 nao ficou pronto em $StartupTimeoutSeconds segundos. Veja os logs com docker compose -f docker-compose.windows.yml logs."
}

# --- Noraxon ----------------------------------------------------------------

function Get-NoraxonConfiguration {
    # Validated before touching Docker so configuration mistakes fail fast.
    $channelIds = New-Object System.Collections.Generic.List[string]
    for ($index = 1; $index -le 32; $index++) {
        $value = [Environment]::GetEnvironmentVariable("NORAXON_CHANNEL_ID_$index", 'Process')
        if ([string]::IsNullOrWhiteSpace($value)) { break }
        $channelIds.Add($value.Trim())
    }
    if ($channelIds.Count -eq 0) {
        if (-not [string]::IsNullOrWhiteSpace($env:NORAXON_SENDER_COMMAND)) {
            throw 'NORAXON_SENDER_COMMAND nao e mais usado. No .env, defina NORAXON_SDK_PATH e um NORAXON_CHANNEL_ID_1, NORAXON_CHANNEL_ID_2, ... por canal EMG (veja .env.example).'
        }
        throw 'Noraxon foi selecionado, mas nenhum NORAXON_CHANNEL_ID_1 esta definido no .env (veja .env.example).'
    }
    foreach ($id in $channelIds) {
        if ($id -notlike '*type.input.analog.emg*') {
            throw "O canal Noraxon '$id' nao e um canal EMG (type.input.analog.emg)."
        }
        if ($id.StartsWith('"') -or $id.StartsWith("'") -or $id.EndsWith('"') -or $id.EndsWith("'")) {
            throw "O canal Noraxon '$id' tem aspas sobrando. Use uma unica par de aspas simples em volta do valor no .env."
        }
    }

    if ([string]::IsNullOrWhiteSpace($env:NORAXON_SDK_PATH)) {
        throw 'Noraxon foi selecionado, mas NORAXON_SDK_PATH nao esta definido no .env.'
    }
    # GetFullPath also collapses doubled backslashes written by older .env files.
    $sdkPath = [IO.Path]::GetFullPath($env:NORAXON_SDK_PATH)
    if (-not (Test-Path (Join-Path $sdkPath 'pyproject.toml'))) {
        throw "NORAXON_SDK_PATH nao aponta para o projeto noraxon-sdk (pyproject.toml ausente): $sdkPath"
    }

    $useSetup = $true
    if (-not [string]::IsNullOrWhiteSpace($env:NORAXON_USE_SETUP)) {
        $useSetup = $env:NORAXON_USE_SETUP.Trim() -notmatch '^(0|false|no|nao)$'
    }
    $timeout = 180
    if ($NoraxonTimeoutSeconds -gt 0) { $timeout = $NoraxonTimeoutSeconds }
    elseif ($env:NORAXON_STARTUP_TIMEOUT_SECONDS -match '^\d+$') { $timeout = [int] $env:NORAXON_STARTUP_TIMEOUT_SECONDS }

    return [pscustomobject]@{
        SdkPath    = $sdkPath
        ChannelIds = $channelIds.ToArray()
        UseSetup   = $useSetup
        Uv         = Resolve-UvPath
        Timeout    = $timeout
    }
}

function Resolve-UvPath {
    if (-not [string]::IsNullOrWhiteSpace($env:NORAXON_UV_PATH)) {
        if (Test-Path $env:NORAXON_UV_PATH) { return (Resolve-Path $env:NORAXON_UV_PATH).Path }
        throw "NORAXON_UV_PATH nao existe: $env:NORAXON_UV_PATH"
    }
    $command = Get-Command 'uv' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    foreach ($candidate in @(
            (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
            (Join-Path $env:USERPROFILE '.cargo\bin\uv.exe'),
            (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\uv.exe'))) {
        if (Test-Path $candidate) { return $candidate }
    }
    throw 'Nao encontrei o uv. Instale-o ou defina NORAXON_UV_PATH no .env com o caminho do uv.exe.'
}

function ConvertTo-CommandLineArgument {
    param([string] $Value)
    # Standard Windows (CommandLineToArgvW) quoting. Always quoting also keeps
    # cmd.exe from interpreting characters such as & in the channel IDs.
    $escaped = [regex]::Replace($Value, '(\\*)"', { param($m) ($m.Groups[1].Value * 2) + '\"' })
    $escaped = [regex]::Replace($escaped, '(\\+)$', { param($m) $m.Groups[1].Value * 2 })
    return '"' + $escaped + '"'
}

function Get-NoraxonSenderProcesses {
    # Matches the cmd.exe wrapper, uv.exe and python.exe of any sender, including
    # one left running from a manual test, which would hold the Acquire device.
    return @(Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%noraxon_tcp_sender.py%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessId -ne $PID })
}

function Stop-NoraxonSender {
    param([int] $GraceSeconds = 8)
    if ((Get-NoraxonSenderProcesses).Count -eq 0) { return }
    Write-RehabStatus 8 'Encerrando o emissor Noraxon anterior...'
    # After `docker compose down` the sender notices the closed TCP connection
    # and stops the Acquire recording itself; give it time to do so cleanly.
    $deadline = (Get-Date).AddSeconds($GraceSeconds)
    while ((Get-NoraxonSenderProcesses).Count -gt 0 -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    foreach ($process in Get-NoraxonSenderProcesses) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Wait-NoraxonReceiver {
    Write-RehabStatus 74 'Aguardando o receptor Noraxon no Docker...'
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        $state = Get-ContainerStatus 'noraxon_tcp_receiver'
        if ($state -in @('exited', 'dead', 'restarting')) {
            throw "O container noraxon_tcp_receiver esta '$state'. Veja: docker logs noraxon_tcp_receiver"
        }
        $log = Get-ContainerLog 'noraxon_tcp_receiver'
        if ($log -match 'TCP receiver error') {
            throw 'O receptor Noraxon nao conseguiu abrir a porta 8765 dentro do Docker. Veja: docker logs noraxon_tcp_receiver'
        }
        if ($log -match 'Noraxon TCP receiver listening on') { return }
        Start-Sleep -Seconds 1
    }
    throw 'O receptor Noraxon (noraxon_tcp_receiver) nao iniciou em 60 segundos. Veja: docker logs noraxon_tcp_receiver'
}

function Start-NoraxonSender {
    param($Config)
    # Same environment as the manual procedure: the SDK repository on PYTHONPATH.
    $pythonPath = $Config.SdkPath
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) { $pythonPath += ";$env:PYTHONPATH" }
    $env:PYTHONPATH = $pythonPath
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONIOENCODING = 'utf-8'

    $senderScript = Join-Path $script:RepositoryRoot 'src\noraxon_emg\scripts\noraxon_tcp_sender.py'
    $arguments = @('run', '--project', $Config.SdkPath, 'python', '-u', $senderScript)
    if ($Config.UseSetup) { $arguments += '--use-setup' }
    foreach ($id in $Config.ChannelIds) { $arguments += @('--channel-id', $id) }
    $arguments += @('--host', '127.0.0.1', '--port', '8765', '--connect-timeout-s', '30')

    New-Item -ItemType Directory -Force -Path (Split-Path $script:NoraxonLog) | Out-Null
    Remove-Item $script:NoraxonLog -Force -ErrorAction SilentlyContinue

    # Every argument is quoted explicitly and the whole line is wrapped for
    # `cmd /s /c`, so the channel IDs reach Python byte-for-byte (the previous
    # NORAXON_SENDER_COMMAND string was re-parsed by cmd.exe, where single
    # quotes are not quotes and & splits the command). cmd.exe is kept only to
    # redirect all output, including uv's own errors, to logs\noraxon_sender.log.
    # Start-Process without -Redirect* uses ShellExecute, so the long-lived
    # sender does not inherit Unity's stdout/stderr pipes.
    $commandLine = (@($Config.Uv) + $arguments | ForEach-Object { ConvertTo-CommandLineArgument $_ }) -join ' '
    $cmdArguments = '/d /s /c "' + $commandLine + ' > ' + (ConvertTo-CommandLineArgument $script:NoraxonLog) + ' 2>&1"'

    Write-RehabStatus 78 'Iniciando o emissor Noraxon...'
    Write-RehabLog "Noraxon: $($Config.ChannelIds.Count) canal(is), --use-setup=$($Config.UseSetup), log em logs\noraxon_sender.log"
    return Start-Process -FilePath $env:ComSpec -ArgumentList $cmdArguments -WorkingDirectory $script:RepositoryRoot -WindowStyle Hidden -PassThru
}

function Read-NoraxonLog {
    if (-not (Test-Path $script:NoraxonLog)) { return '' }
    try {
        # Shared read: the sender keeps the file open for writing.
        $stream = [IO.File]::Open($script:NoraxonLog, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        try { return (New-Object IO.StreamReader($stream)).ReadToEnd() } finally { $stream.Dispose() }
    } catch { return '' }
}

function Get-NoraxonFailureSummary {
    $lines = @((Read-NoraxonLog) -split "`r?`n" | Where-Object { $_.Trim() })
    $errorLine = $lines | Where-Object { $_ -like 'NORAXON_ERROR *' } | Select-Object -Last 1
    foreach ($line in ($lines | Select-Object -Last 6)) { Write-RehabLog "  noraxon> $line" }
    if ($errorLine) { return $errorLine.Substring('NORAXON_ERROR '.Length) }
    if ($lines.Count -gt 0) { return $lines[-1] }
    return 'sem saida; rode o comando manual do README para ver o erro.'
}

function Wait-NoraxonStream {
    param($Process, $Config)
    $deadline = (Get-Date).AddSeconds($Config.Timeout)
    $reported = @{}
    while ((Get-Date) -lt $deadline) {
        $log = Read-NoraxonLog
        if (-not $reported.setup -and $log -match 'NORAXON_STATUS setup_waiting') {
            Write-RehabStatus 80 'Confirme a janela de configuracao de hardware do Noraxon (ela pode estar atras desta tela).'
            $reported.setup = $true
        }
        if (-not $reported.configured -and $log -match 'NORAXON_STATUS configured') {
            Write-RehabStatus 84 'Noraxon configurado. Conectando ao Docker...'
            $reported.configured = $true
        }
        if (-not $reported.streaming -and $log -match 'NORAXON_STATUS streaming') {
            Write-RehabStatus 90 'Aguardando os primeiros dados EMG...'
            $reported.streaming = $true
        }
        if ($log -match 'NORAXON_STATUS first_batch') {
            # End-to-end confirmation: the schema reached ROS inside Docker.
            if ((Get-ContainerLog 'noraxon_tcp_receiver') -match 'Noraxon stream metadata received') { return }
        }
        if ($Process.HasExited) {
            $summary = Get-NoraxonFailureSummary
            if ($summary -match 'Could not connect to Ultium receiver') {
                $summary += ' -> O receptor Ultium esta ocupado ou inacessivel: feche o Noraxon Acquire e emissores antigos, confira em usbipd list que o 16d0:069e esta Not shared e reconecte o receptor USB.'
            }
            throw "O emissor Noraxon encerrou: $summary"
        }
        Start-Sleep -Milliseconds 500
    }
    $summary = Get-NoraxonFailureSummary
    Stop-NoraxonSender -GraceSeconds 0
    if ($reported.streaming) {
        throw "O Noraxon iniciou a gravacao, mas nenhum dado EMG chegou em $($Config.Timeout) s. Confira se os sensores estao ligados. Ultima saida: $summary"
    }
    if ($reported.setup -and -not $reported.configured) {
        throw "A janela de configuracao do Noraxon nao foi confirmada em $($Config.Timeout) s."
    }
    throw "O Noraxon nao comecou a transmitir em $($Config.Timeout) s. Ultima saida: $summary"
}

# --- Main -------------------------------------------------------------------

try {
    Update-ProcessPath
    Import-RehabEnvironment
    $noraxonConfig = $null
    if (-not $Shutdown -and $script:SelectedDevices -contains 'noraxon') { $noraxonConfig = Get-NoraxonConfiguration }

    Write-RehabStatus 2 'Verificando Docker Desktop...'
    Invoke-Checked 'docker' @('version', '--format', '{{.Server.Version}}') 'Docker Desktop nao esta disponivel'

    if ($Shutdown) {
        Invoke-RehabCleanup -SkipPortCheck
        Write-RehabStatus 100 'Plataforma encerrada.'
        exit 0
    }

    # Everything left from a previous attempt goes away BEFORE devices are
    # prepared: containers of every profile (which also restart on their own
    # after a reboot because of `restart: unless-stopped`), containers of other
    # Compose projects, and Noraxon senders still holding the Acquire device.
    Invoke-RehabCleanup

    if ($script:SelectedDevices -contains 'guidao') {
        $guidaoPattern = if ($env:REHAB_GUIDAO_USB_MATCH) { $env:REHAB_GUIDAO_USB_MATCH } else { 'Guidao|Guid.o|Arduino.*(M0|Zero|Uno)' }
        Ensure-UsbAttached 'guidao' $guidaoPattern
    }
    if ($script:SelectedDevices -contains 'pulmonary') {
        $pulmonaryPattern = if ($env:REHAB_PULMONARY_USB_MATCH) { $env:REHAB_PULMONARY_USB_MATCH } else { 'Pulmonary|Pulmonar|Respir|ESP32|CP210|CH340' }
        Ensure-UsbAttached 'pulmonary' $pulmonaryPattern
    }
    if ($script:SelectedDevices -contains 'emotiv') {
        if ([string]::IsNullOrWhiteSpace($env:EMOTIV_CLIENT_ID) -or [string]::IsNullOrWhiteSpace($env:EMOTIV_CLIENT_SECRET)) {
            throw 'Emotiv foi selecionado, mas EMOTIV_CLIENT_ID e EMOTIV_CLIENT_SECRET nao estao definidos.'
        }
        $pythonScript = Join-Path $script:RepositoryRoot 'emotiv_cleanup.py'
        if (-not [string]::IsNullOrWhiteSpace($env:EMOTIV_PYTHON)) {
            $python = $env:EMOTIV_PYTHON
            if (-not (Test-Path $python)) { throw "EMOTIV_PYTHON nao existe: $python" }
            $emotivArguments = @($pythonScript, '--client-id', $env:EMOTIV_CLIENT_ID, '--client-secret', $env:EMOTIV_CLIENT_SECRET)
        } elseif (Get-Command 'py' -CommandType Application -ErrorAction SilentlyContinue) {
            $python = 'py'
            $emotivArguments = @('-3', $pythonScript, '--client-id', $env:EMOTIV_CLIENT_ID, '--client-secret', $env:EMOTIV_CLIENT_SECRET)
        } elseif (Get-Command 'python' -CommandType Application -ErrorAction SilentlyContinue) {
            $python = 'python'
            $emotivArguments = @($pythonScript, '--client-id', $env:EMOTIV_CLIENT_ID, '--client-secret', $env:EMOTIV_CLIENT_SECRET)
        } else {
            throw 'Python nao encontrado. Instale Python 3 ou defina EMOTIV_PYTHON no .env com o caminho completo do python.exe.'
        }
        Write-RehabStatus 25 'Limpando sessoes antigas do Emotiv...'
        Invoke-Checked $python $emotivArguments 'A limpeza de sessoes do Emotiv falhou. Confirme que o EMOTIV Launcher/Cortex esta aberto'
    }

    $imageExists = (Invoke-NativeCapture 'docker' @('image', 'inspect', 'rehab-platform:humble')).ExitCode -eq 0
    if ($Rebuild -or -not $imageExists) {
        Write-RehabStatus 35 'Construindo a imagem ROS (a primeira execucao pode demorar)...'
        Push-Location $script:RepositoryRoot
        try { Invoke-Checked 'docker' @('build', '-t', 'rehab-platform:humble', '.') 'Falha ao construir a imagem ROS' }
        finally { Pop-Location }
    }

    Write-RehabStatus 55 'Iniciando a plataforma ROS com os dispositivos selecionados...'
    $compose = @('compose', '-f', $script:ComposeFile)
    foreach ($device in $script:SelectedDevices) { $compose += @('--profile', $device) }
    Invoke-Checked 'docker' ($compose + @('up', '-d', '--remove-orphans')) 'Falha ao iniciar os containers'

    Wait-RosEndpoint

    if ($noraxonConfig) {
        # The sender is started only once the receiver is really listening, and
        # the menu opens only after EMG data has reached ROS.
        Wait-NoraxonReceiver
        $sender = Start-NoraxonSender $noraxonConfig
        Wait-NoraxonStream $sender $noraxonConfig
        Write-RehabStatus 97 'Noraxon transmitindo EMG.'
    }

    Write-RehabStatus 100 'Sistema pronto.'
    exit 0
} catch {
    Write-RehabFailure $_.Exception.Message
    exit 1
}
