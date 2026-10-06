param(
    [string]$Executable,
    [string]$Archive,
    [string]$ReportPath,
    [int]$TimeoutSeconds = 240
)
$ErrorActionPreference = 'Stop'
if ([bool]$Executable -eq [bool]$Archive) { throw 'Supply exactly one of Executable or Archive.' }
$checkRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('emo-runtime-check-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $checkRoot | Out-Null
if ($Archive) {
    Expand-Archive -LiteralPath (Resolve-Path -LiteralPath $Archive).Path -DestinationPath (Join-Path $checkRoot 'package')
    $executables = @(Get-ChildItem -LiteralPath (Join-Path $checkRoot 'package') -Recurse -File -Filter 'EmoMasterRuntime.exe')
    if ($executables.Count -ne 1) { throw 'Expected exactly one EmoMasterRuntime.exe.' }
    $Executable = $executables[0].FullName
}
$Executable = (Resolve-Path -LiteralPath $Executable).Path
$result = Join-Path $checkRoot 'self-test.json'
$previousPlatform = $env:QT_QPA_PLATFORM
$previousPath = $env:PYTHONPATH
$previousPythonHome = $env:PYTHONHOME
$previousExecutablePath = $env:PATH
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    $env:PATH = [Environment]::GetFolderPath('System') + ';' + $env:WINDIR
    $process = Start-Process -FilePath $Executable -WorkingDirectory $checkRoot -WindowStyle Hidden -PassThru `
        -ArgumentList @('--self-test', '--result-json', ('"{0}"' -f $result))
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        & taskkill /PID $process.Id /T /F | Out-Null
        throw "Runtime self-test timed out: $checkRoot"
    }
    $process.WaitForExit()
    if (-not (Test-Path -LiteralPath $result)) { throw "Runtime report missing: $checkRoot" }
    $report = Get-Content -LiteralPath $result -Raw | ConvertFrom-Json
    if ($ReportPath) {
        $destination = [System.IO.Path]::GetFullPath($ReportPath)
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $result -Destination $destination
        $screenshot = $report.checks.operatorRuntime.screenshot
        if ($screenshot -and (Test-Path -LiteralPath $screenshot)) {
            Copy-Item -LiteralPath $screenshot -Destination ($destination + '.png')
        }
    }
    if ($process.ExitCode -ne 0 -or $report.status -ne 'ok' -or -not $report.frozen -or
        $report.designerModules.Count -ne 0 -or $report.checks.operatorRuntime.sessions.Count -ne 3) {
        throw "Frozen Runtime acceptance failed: exit=$($process.ExitCode), report=$result"
    }
    Write-Output "Frozen Runtime acceptance passed: $result"
} finally {
    $env:QT_QPA_PLATFORM = $previousPlatform
    $env:PYTHONPATH = $previousPath
    $env:PYTHONHOME = $previousPythonHome
    $env:PATH = $previousExecutablePath
}
