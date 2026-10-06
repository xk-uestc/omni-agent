$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$demoPython = (Get-Command python.exe -ErrorAction Stop).Source
New-Item -ItemType Directory -Path (Join-Path $demoRoot 'runtime') -Force | Out-Null
foreach ($service in @(
    @{ Port = 8030; Script = 'tools/run_server.py'; Args = '--with-model --port 8030'; Log = 'public-origin' },
    @{ Port = 3011; Script = 'tools/run_public_demo.py'; Args = ''; Log = 'public-demo' }
)) {
    $listener = Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener[0].OwningProcess)"
        if ($process.CommandLine -notmatch [regex]::Escape((Split-Path -Leaf $service.Script))) {
            throw "Port $($service.Port) belongs to another service; no process was stopped."
        }
        continue
    }
    Start-Process -FilePath $demoPython -ArgumentList "$($service.Script) $($service.Args)" `
        -WorkingDirectory $demoRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $demoRoot "runtime/$($service.Log).stdout.log") `
        -RedirectStandardError (Join-Path $demoRoot "runtime/$($service.Log).stderr.log") | Out-Null
}
