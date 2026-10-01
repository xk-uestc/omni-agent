param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = 'Stop'
$results = @()
foreach ($source in Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'delivery') -Filter '*.docx') {
    # This host routes Word automation to WPS; closing a last document can stop
    # its COM server. A separate application per source avoids a stale proxy.
    $word = New-Object -ComObject kwps.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    try {
        $document = $word.Documents.Open($source.FullName, $false, $true)
        try {
            $document.Repaginate()
            $pages = $document.ComputeStatistics(2)
            $pdfPath = [System.IO.Path]::ChangeExtension($source.FullName, '.pdf')
            $document.ExportAsFixedFormat($pdfPath, 17)
            $results += [PSCustomObject]@{ document = $source.Name; pages = $pages; pdf = [System.IO.Path]::GetFileName($pdfPath) }
        } finally {
            $document.Close(0)
            [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($document)
        }
    } finally {
        try { $word.Quit() } catch { Write-Warning '文档转换应用已经退出' }
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
    }
}
$results | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ProjectRoot 'delivery\PDF_EXPORT.json') -Encoding utf8
$results | Format-Table
