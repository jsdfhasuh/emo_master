param(
    [string]$Executable,
    [string]$Archive,
    [string]$ReportPath,
    [int]$TimeoutSeconds = 240,
    [double]$DurationSeconds = 1,
    [int]$Restarts = 2,
    [ValidateSet('offscreen', 'windows')][string]$QtPlatform = 'offscreen'
)
$ErrorActionPreference = 'Stop'
if ([bool]$Executable -eq [bool]$Archive) { throw 'Supply exactly one of Executable or Archive.' }
$checkRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('emo-runtime-check-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $checkRoot | Out-Null
$result = Join-Path $checkRoot 'self-test.json'
$stdout = Join-Path $checkRoot 'stdout.log'
$stderr = Join-Path $checkRoot 'stderr.log'
$execution = [ordered]@{ status = 'FAIL'; phase = 'prepare'; executable = $Executable;
    archive = $Archive; workDirectory = $checkRoot; exitCode = $null;
    durationSeconds = $DurationSeconds; restarts = $Restarts; qtPlatform = $QtPlatform;
    fieldAcceptance = 'NOT_RUN' }
$report = $null
$previousPlatform = $env:QT_QPA_PLATFORM
$previousPath = $env:PYTHONPATH
$previousPythonHome = $env:PYTHONHOME
$previousExecutablePath = $env:PATH
try {
    if ($Archive) {
        Expand-Archive -LiteralPath (Resolve-Path -LiteralPath $Archive).Path -DestinationPath (Join-Path $checkRoot 'package')
        $executables = @(Get-ChildItem -LiteralPath (Join-Path $checkRoot 'package') -Recurse -File -Filter 'EmoMasterRuntime.exe')
        if ($executables.Count -ne 1) { throw 'Expected exactly one EmoMasterRuntime.exe.' }
        $Executable = $executables[0].FullName
    }
    $Executable = (Resolve-Path -LiteralPath $Executable).Path
    $execution.executable = $Executable
    $execution.executableSha256 = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToLowerInvariant()
    $env:QT_QPA_PLATFORM = $QtPlatform
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
    $env:PATH = [Environment]::GetFolderPath('System') + ';' + $env:WINDIR
    $execution.phase = 'self-test'
    $process = Start-Process -FilePath $Executable -WorkingDirectory $checkRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
        -ArgumentList @('--self-test', '--result-json', ('"{0}"' -f $result),
            '--duration', $DurationSeconds.ToString([cultureinfo]::InvariantCulture), '--restarts', $Restarts)
    $execution.pid = $process.Id
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        & taskkill /PID $process.Id /T /F | Out-Null
        throw "Runtime self-test timed out: $checkRoot"
    }
    $process.WaitForExit()
    $execution.exitCode = $process.ExitCode
    if (-not (Test-Path -LiteralPath $result)) { throw "Runtime report missing: $checkRoot" }
    $report = Get-Content -LiteralPath $result -Raw | ConvertFrom-Json
    if ($process.ExitCode -ne 0 -or $report.status -ne 'ok' -or -not $report.frozen -or
        $report.designerModules.Count -ne 0 -or $report.checks.operatorRuntime.sessions.Count -ne ($Restarts + 1)) {
        throw "Frozen Runtime acceptance failed: exit=$($process.ExitCode), report=$result"
    }
    $invalidSessions = @($report.checks.operatorRuntime.sessions | Where-Object {
        $_.pid -le 0 -or $_.cycles -lt 4 -or $_.stopCode -ne 'E_CANCELLED'
    })
    if ($invalidSessions.Count) { throw 'Runtime worker/cycle/normal-stop acceptance failed' }
    $execution.status = 'PASS'
    $execution.phase = 'complete'
    Write-Output "Frozen Runtime acceptance passed: $result"
} catch {
    $execution.error = $_.Exception.ToString()
    throw
} finally {
    $env:QT_QPA_PLATFORM = $previousPlatform
    $env:PYTHONPATH = $previousPath
    $env:PYTHONHOME = $previousPythonHome
    $env:PATH = $previousExecutablePath
    if ($ReportPath) {
        $destination = [System.IO.Path]::GetFullPath($ReportPath)
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        $execution | ConvertTo-Json | Set-Content -LiteralPath ($destination + '.execution.json') -Encoding utf8
        foreach ($file in @(@($result, $destination), @($stdout, ($destination + '.stdout.log')),
                            @($stderr, ($destination + '.stderr.log')))) {
            if (Test-Path -LiteralPath $file[0]) { Copy-Item -LiteralPath $file[0] -Destination $file[1] }
        }
        $screenshot = $report.checks.operatorRuntime.screenshot
        if ($screenshot -and (Test-Path -LiteralPath $screenshot)) {
            Copy-Item -LiteralPath $screenshot -Destination ($destination + '.png')
        }
    }
}
