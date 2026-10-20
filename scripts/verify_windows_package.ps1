param(
    [Parameter(Mandatory = $true)][string]$Archive,
    [string]$OutputDirectory = (Join-Path ([System.IO.Path]::GetTempPath()) ('emo-icons-' + [guid]::NewGuid().ToString('N')))
)

$ErrorActionPreference = 'Stop'
$archivePath = (Resolve-Path -LiteralPath $Archive).Path
$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $outputPath) {
    throw 'Package verification needs a new output directory; existing files are never overwritten.'
}
Expand-Archive -LiteralPath $archivePath -DestinationPath $outputPath
$executables = @(Get-ChildItem -LiteralPath $outputPath -Recurse -File -Filter 'EmoMaster.exe')
if ($executables.Count -ne 1) {
    throw "Expected one EmoMaster.exe, found $($executables.Count)"
}
$resultPath = Join-Path $outputPath 'icon-self-test.json'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:HUARAY_CAMERA_SMOKE = '0'
$env:PYTHONPATH = ''
$process = Start-Process -FilePath $executables[0].FullName -WorkingDirectory $outputPath -WindowStyle Hidden -PassThru `
    -ArgumentList @('--self-test', '--gui-icons', '--result-json', ('"{0}"' -f $resultPath))
if (-not $process.WaitForExit(120000)) {
    $process.Kill()
    $process.WaitForExit()
    throw "Package icon self-test exceeded 120 seconds; inspect report if present: $resultPath"
}
$process.WaitForExit()
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $resultPath)) {
    throw "Package icon self-test failed: exit=$($process.ExitCode), report=$resultPath"
}
$result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
if ($result.status -ne 'ok' -or $result.checks.guiIcons.status -ne 'ok' -or $result.checks.guiIcons.samples.Count -ne 9) {
    throw "Package did not pass every required icon rendering check: $resultPath"
}
Write-Output "Package checks passed: $resultPath"
