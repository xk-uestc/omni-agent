param([int]$Port = 8030)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Write-Host "Independent ICT8 project: $ProjectRoot"
Write-Host "Web and API: http://127.0.0.1:$Port"
& python (Join-Path $ProjectRoot 'tools\run_server.py') --port $Port
if ($LASTEXITCODE -ne 0) { throw 'Independent ICT8 server failed' }
