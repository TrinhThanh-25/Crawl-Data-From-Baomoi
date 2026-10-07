param(
    [ValidateRange(1,8)][int]$Workers = 3,
    [ValidateRange(1,3600)][int]$MaxRuntime = 900
)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Create .venv and install requirements.txt first; see README.md.'
}
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PSScriptRoot '.browsers'
$env:PYTHONUTF8 = '1'
$outputDirectory = Join-Path $PSScriptRoot ('results_' + (Get-Date -Format 'yyyy-MM-dd_HHmmss'))
New-Item -ItemType Directory -Path $outputDirectory | Out-Null
foreach ($source in @('baomoi', 'vnexpress')) {
    $scriptPath = Join-Path $PSScriptRoot ('crawl_' + $source + '.py')
    $outputPath = Join-Path $outputDirectory ($source + '_24h.json')
    $logPath = Join-Path $outputDirectory ($source + '.log')
    & $pythonPath -X utf8 $scriptPath --workers $Workers --max-runtime $MaxRuntime --output $outputPath 2>&1 | Tee-Object -FilePath $logPath
    if ($LASTEXITCODE -ne 0) { throw "Crawler $source exited with code $LASTEXITCODE; inspect $logPath" }
}
Write-Host "Results: $outputDirectory"
