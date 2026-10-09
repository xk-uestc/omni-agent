[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$supervisor = Join-Path $PSScriptRoot 'maintain_public_demo.ps1'
$taskName = 'ICT8 Public Demo Watchdog'
$userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source

$action = New-ScheduledTaskAction -Execute $powershell `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$supervisor`"" `
    -WorkingDirectory $demoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'Restarts the ICT8 public demo origin, /demo proxy, and its Cloudflare tunnel when unhealthy.' `
    -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output "Installed and started: $taskName"
