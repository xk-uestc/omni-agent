param(
    [Parameter(Mandatory=$true)][string]$OutputPath,
    [Parameter(Mandatory=$true)][string]$UntilUtc,
    [int]$Port = 8031,
    [int]$IntervalSeconds = 15
)

$deadline = [DateTimeOffset]::Parse($UntilUtc).ToUniversalTime()
$parent = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Force -Path $parent | Out-Null

while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $timestamp = [DateTimeOffset]::UtcNow.ToString('o')
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    $sample = [ordered]@{
        timestamp_utc = $timestamp
        listener_pid = $null
        cpu_seconds = $null
        working_set_mb = $null
        private_memory_mb = $null
        handles = $null
        thread_count = $null
        health_status = $null
    }
    if ($listener) {
        $sample.listener_pid = $listener.OwningProcess
        $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        if ($process) {
            $sample.cpu_seconds = [Math]::Round($process.CPU, 3)
            $sample.working_set_mb = [Math]::Round($process.WorkingSet64 / 1MB, 2)
            $sample.private_memory_mb = [Math]::Round($process.PrivateMemorySize64 / 1MB, 2)
            $sample.handles = $process.HandleCount
            $sample.thread_count = $process.Threads.Count
        }
    }
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
        $sample.health_status = [int]$response.StatusCode
    } catch {
        $sample.health_status = 'unavailable'
    }
    $sample | ConvertTo-Json -Compress | Add-Content -LiteralPath $OutputPath -Encoding utf8
    Start-Sleep -Seconds $IntervalSeconds
}
