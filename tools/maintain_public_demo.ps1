[CmdletBinding()]
param(
    [switch]$Once,
    [ValidateRange(5, 300)]
    [int]$IntervalSeconds = 20
)

$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $demoRoot 'runtime/public-tunnel'
$logPath = Join-Path $runtimeRoot 'supervisor.log'
$tunnelExe = 'D:\lanqun-site\cloudflared\cloudflared.exe'
$tunnelConfig = Join-Path $runtimeRoot 'config.yml'
$tunnelLog = Join-Path $runtimeRoot 'cloudflared.log'
$pythonCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312/python.exe'),
    (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
$script:pythonExe = $pythonCandidates | Select-Object -First 1
$script:publicFailures = 0
$script:lastPublicProbe = [DateTime]::MinValue
$script:lastTunnelRestart = [DateTime]::MinValue
$script:lastPortConflict = @{}

New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null

function Write-SupervisorLog([string]$Message) {
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "$(Get-Date -Format o) $Message"
}

function Test-HttpOk([string]$Uri) {
    try {
        # Cloudflare edge reconnects can briefly exceed the old 8 second window.
        $response = Invoke-WebRequest -Uri $Uri -TimeoutSec 15 -UseBasicParsing
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    }
    catch {
        return $false
    }
}

function Get-PythonService([string]$ScriptName, [string]$Port) {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -match "(?i)$([regex]::Escape($ScriptName))" -and
            $_.CommandLine -match "(?i)--port\s+$Port"
        } |
        Select-Object -First 1
}

function Get-TunnelProcess {
    $escapedConfig = [regex]::Escape($tunnelConfig)
    Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match '(?i)\btunnel\b' -and $_.CommandLine -match "(?i)$escapedConfig" } |
        Select-Object -First 1
}

function Test-LocalPortAvailable([int]$Port, [int]$ExpectedProcessId) {
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    $otherOwners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -ne $ExpectedProcessId })
    if ($otherOwners.Count -eq 0) {
        return $true
    }

    $last = $script:lastPortConflict[$Port]
    if (-not $last -or ((Get-Date) - $last).TotalMinutes -ge 5) {
        Write-SupervisorLog "port $Port is occupied by an unrelated process; left it untouched"
        $script:lastPortConflict[$Port] = Get-Date
    }
    return $false
}

function Ensure-PythonService(
    [string]$Label,
    [string]$ScriptName,
    [string]$RelativeScript,
    [string]$Port,
    [string]$HealthUri,
    [string[]]$ExtraArguments
) {
    $process = Get-PythonService -ScriptName $ScriptName -Port $Port
    if (Test-HttpOk $HealthUri) {
        return $true
    }

    if ($process) {
        if ($process.CreationDate -is [DateTime]) {
            $startedAt = $process.CreationDate
        }
        else {
            $startedAt = [Management.ManagementDateTimeConverter]::ToDateTime([string]$process.CreationDate)
        }
        if (((Get-Date) - $startedAt).TotalSeconds -lt 180) {
            return $false
        }

        Write-SupervisorLog "$Label health check failed; restarting owned process $($process.ProcessId)"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }

    if (-not $script:pythonExe) {
        Write-SupervisorLog "$Label could not start; python.exe was not found"
        return $false
    }
    if (-not (Test-LocalPortAvailable -Port ([int]$Port) -ExpectedProcessId 0)) {
        return $false
    }

    $stdout = Join-Path $runtimeRoot "$Label.stdout.log"
    $stderr = Join-Path $runtimeRoot "$Label.stderr.log"
    $arguments = @($RelativeScript) + $ExtraArguments
    try {
        Start-Process -FilePath $script:pythonExe -ArgumentList $arguments `
            -WorkingDirectory $demoRoot -WindowStyle Hidden `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
        Write-SupervisorLog "$Label start requested on port $Port"
    }
    catch {
        Write-SupervisorLog "$Label start failed: $($_.Exception.Message)"
    }
    return $false
}

function Ensure-Tunnel {
    $process = Get-TunnelProcess
    if (-not $process) {
        if (-not (Test-Path -LiteralPath $tunnelExe -PathType Leaf) -or
            -not (Test-Path -LiteralPath $tunnelConfig -PathType Leaf)) {
            Write-SupervisorLog 'Cloudflare tunnel could not start; executable or runtime config is missing'
            return $false
        }

        try {
            Start-Process -FilePath $tunnelExe -ArgumentList @(
                'tunnel', '--config', $tunnelConfig, '--loglevel', 'info', '--logfile', $tunnelLog, 'run'
            ) -WorkingDirectory (Split-Path -Parent $tunnelExe) -WindowStyle Hidden | Out-Null
            Write-SupervisorLog 'Cloudflare tunnel start requested'
        }
        catch {
            Write-SupervisorLog "Cloudflare tunnel start failed: $($_.Exception.Message)"
        }
        $script:publicFailures = 0
        return $false
    }

    $now = Get-Date
    if (($now - $script:lastPublicProbe).TotalSeconds -lt 45) {
        return $true
    }
    $script:lastPublicProbe = $now
    if (Test-HttpOk 'https://raysource.cloud/demo/health') {
        if ($script:publicFailures -gt 0) {
            Write-SupervisorLog 'public health check recovered'
        }
        $script:publicFailures = 0
        return $true
    }

    $script:publicFailures++
    if ($script:publicFailures -ge 5) {
        $restartCooldown = ((Get-Date) - $script:lastTunnelRestart).TotalSeconds
        if ($restartCooldown -lt 300) {
            # Keep the existing tunnel alive while it is reconnecting on its own.
            if ($script:publicFailures -eq 5) {
                Write-SupervisorLog "public health check still failing during tunnel restart cooldown (${restartCooldown:N0}s); keeping process alive"
            }
            return $true
        }

        Write-SupervisorLog 'public health check failed five times while local services are healthy; restarting this tunnel process'
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        $script:publicFailures = 0
        $script:lastTunnelRestart = Get-Date
        Start-Sleep -Seconds 2
        return $false
    }
    return $true
}

Write-SupervisorLog 'public demo supervisor started'
do {
    try {
        $originOk = Ensure-PythonService -Label 'origin-8031' -ScriptName 'run_server.py' `
            -RelativeScript 'tools/run_server.py' -Port '8031' `
            -HealthUri 'http://127.0.0.1:8031/health' -ExtraArguments @('--port', '8031', '--with-model')
        $proxyOk = Ensure-PythonService -Label 'prefix-proxy-8032' -ScriptName 'demo_prefix_proxy.py' `
            -RelativeScript 'tools/demo_prefix_proxy.py' -Port '8032' `
            -HealthUri 'http://127.0.0.1:8032/demo/health' -ExtraArguments @()
        if ($originOk -and $proxyOk) {
            [void](Ensure-Tunnel)
        }
        else {
            [void](Ensure-Tunnel)
            $script:publicFailures = 0
        }
    }
    catch {
        Write-SupervisorLog "supervision cycle failed: $($_.Exception.Message)"
    }

    if (-not $Once) {
        Start-Sleep -Seconds $IntervalSeconds
    }
} while (-not $Once)

Write-SupervisorLog 'public demo supervisor stopped'
