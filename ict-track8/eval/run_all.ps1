param(
    [string]$BaseRef,
    [string]$BaseDir,
    [switch]$Bench,
    [switch]$Ocr,
    [switch]$Strict,
    [string]$GenSeed
)

$ErrorActionPreference = "Stop"
$scriptPath = Join-Path $PSScriptRoot "run_all.sh"
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "评测脚本不存在: $scriptPath"
}
$bash = Get-Command bash -ErrorAction SilentlyContinue
if (-not $bash) {
    throw "未找到 bash。请安装 Git for Windows 或 WSL 后再运行 eval/run_all.ps1；不会静默跳过评测。"
}
if (-not $env:MSYSTEM -and -not $env:WSL_DISTRO_NAME) {
    throw "当前 PowerShell 的 bash 路径转换不可验证。请在 Git Bash/WSL 中运行：bash ict-track8/eval/run_all.sh；不会静默跳过评测。"
}

$old = @{}
foreach ($name in @("BASE_REF", "BASE_DIR", "BENCH", "OCR", "STRICT", "GEN_SEED")) {
    $old[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
try {
    if ($BaseRef) { $env:BASE_REF = $BaseRef }
    if ($BaseDir) { $env:BASE_DIR = $BaseDir }
    if ($Bench) { $env:BENCH = "1" }
    if ($Ocr) { $env:OCR = "1" }
    if ($Strict) { $env:STRICT = "1" }
    if ($GenSeed) { $env:GEN_SEED = $GenSeed }
    # 从仓库根目录以相对路径调用，避免 Git Bash/MSYS/WSL 对 D:\ 路径的
    # 不同转换规则。run_all.sh 自己通过 git rev-parse 定位仓库根目录。
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
    Push-Location $repoRoot
    try {
        # 显式用 env 前缀传给 Bash，兼容 Git Bash 对 PowerShell 环境变量的隔离。
        $envPairs = @()
        foreach ($name in @("BASE_REF", "BASE_DIR", "BENCH", "OCR", "STRICT", "GEN_SEED")) {
            $value = [Environment]::GetEnvironmentVariable($name, "Process")
            if ($value) {
                $escaped = $value -replace "'", "'\\''"
                $envPairs += "$name='$escaped'"
            }
        }
        $prefix = if ($envPairs) { ($envPairs -join " ") + " " } else { "" }
        & $bash.Source "-lc" ($prefix + "bash ict-track8/eval/run_all.sh")
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
}
finally {
    foreach ($name in $old.Keys) {
        [Environment]::SetEnvironmentVariable($name, $old[$name], "Process")
    }
}
