param(
    [int]$ApiPort = 8020,
    [int]$WebPort = 8021
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$TrackRoot = Join-Path $Root "ict-track8"

python (Join-Path $TrackRoot "scripts\create_demo_db.py")
$api = Start-Process -FilePath "python" -ArgumentList @("-m", "uvicorn", "backend.app:app", "--app-dir", $TrackRoot, "--host", "127.0.0.1", "--port", "$ApiPort") -WorkingDirectory $Root -PassThru
$web = Start-Process -FilePath "python" -ArgumentList @("-m", "http.server", "$WebPort", "--bind", "127.0.0.1") -WorkingDirectory (Join-Path $TrackRoot "frontend") -PassThru
Write-Host "ICT8 API: http://127.0.0.1:$ApiPort"
Write-Host "ICT8 Demo: http://127.0.0.1:$WebPort"
Write-Host "API PID: $($api.Id); Web PID: $($web.Id)"
