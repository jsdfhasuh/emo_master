param(
    [string]$ReportDirectory = 'runtime-evidence/source-check',
    [string]$PythonExecutable = 'python'
)
$ErrorActionPreference = 'Stop'
$reportRoot = [System.IO.Path]::GetFullPath($ReportDirectory)
New-Item -ItemType Directory -Path $reportRoot -Force | Out-Null
$tests = @(
    'tests/core/test_runtime_directory.py', 'tests/core/test_runtime_package.py',
    'tests/runtime/test_production_continuous.py', 'tests/runtime/test_production_boundaries.py',
    'tests/ui/operator_view/test_production_window.py', 'tests/runtime/test_operator_lifecycle.py',
    'tests/runtime/test_worker_heartbeat_finalization.py', 'tests/runtime/test_heartbeat_liveness.py',
    'tests/test_windows_package_entry.py', 'tests/test_package_bootstrap.py',
    'tests/test_operator_runtime_validation.py', 'tests/test_runtime_package_verifier.py'
)
$previousPlatform = $env:QT_QPA_PLATFORM
$env:QT_QPA_PLATFORM = 'offscreen'
Push-Location -LiteralPath (Join-Path $PSScriptRoot '..')
try {
    & $PythonExecutable -m pytest @tests -q "--junitxml=$(Join-Path $reportRoot 'source-regression.xml')" 2>&1 |
        Tee-Object -FilePath (Join-Path $reportRoot 'source-regression.log')
    if ($LASTEXITCODE -ne 0) { throw "Runtime source regression failed: $LASTEXITCODE" }
} finally {
    Pop-Location
    $env:QT_QPA_PLATFORM = $previousPlatform
}
