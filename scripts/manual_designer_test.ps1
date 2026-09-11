#requires -Version 5.1

[CmdletBinding()]
param(
    [ValidateSet("Prepare", "Embedded", "ExternalRuntime", "ExternalDesigner", "Diagnose", "CleanWorkspace")]
    [string]$Mode = "Prepare",
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$CondaEnvironment = "emo_master"
$Workspace = Join-Path $RepoRoot "manual_test_workspace"
$InputDirectory = Join-Path $Workspace "input"
$OutputDirectory = Join-Path $Workspace "output"
$ScreenshotDirectory = Join-Path $Workspace "screenshots"
$LogDirectory = Join-Path $Workspace "logs"
$EvidenceDirectory = Join-Path $Workspace "evidence"
$ProjectDirectory = Join-Path $Workspace "projects"
$RuntimeDataDirectory = Join-Path $Workspace "runtime-data"
$InputImage = Join-Path $InputDirectory "test_input.png"
$EnvironmentEvidence = Join-Path $EvidenceDirectory "environment.txt"
$AutomatedSummary = Join-Path $EvidenceDirectory "automated-gate-summary.txt"
$IssueTemplate = Join-Path $Workspace "issue-template.md"
$ManualResult = Join-Path $Workspace "manual-result.md"

function Write-Utf8File {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Content
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Write-Utf8FileIfMissing {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Content
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Utf8File -Path $Path -Content $Content
    }
}

function Invoke-CondaCapture {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $lines = @(& conda @Arguments 2>&1 | ForEach-Object { $_.ToString() })
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = ($lines -join [Environment]::NewLine).Trim()
    }
}

function Assert-CondaEnvironment {
    $conda = Get-Command conda -ErrorAction SilentlyContinue
    if ($null -eq $conda) {
        throw "conda command was not found in PATH. Open a Conda-enabled PowerShell and retry."
    }

    $envList = Invoke-CondaCapture -Arguments @("env", "list")
    if ($envList.ExitCode -ne 0) {
        throw "conda env list failed with exit code $($envList.ExitCode).`n$($envList.Output)"
    }
    if ($envList.Output -notmatch "(?m)^\s*${CondaEnvironment}(\s|\*)") {
        throw "Conda environment '$CondaEnvironment' was not found. The script will not create another environment."
    }

    $pythonProbe = Invoke-CondaCapture -Arguments @("run", "-n", $CondaEnvironment, "python", "-c", "import sys; print(sys.executable)")
    if ($pythonProbe.ExitCode -ne 0) {
        throw "conda run -n $CondaEnvironment python failed with exit code $($pythonProbe.ExitCode).`n$($pythonProbe.Output)"
    }

    return [pscustomobject]@{
        CondaPath = $conda.Source
        EnvironmentList = $envList.Output
        PythonExecutable = $pythonProbe.Output
    }
}

function Get-CondaPythonProbe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Code
    )

    return Invoke-CondaCapture -Arguments @("run", "-n", $CondaEnvironment, "python", "-c", $Code)
}

function Assert-EnvironmentPackages {
    $pythonVersionResult = Get-CondaPythonProbe -Code "import platform; print(platform.python_version())"
    if ($pythonVersionResult.ExitCode -ne 0) {
        throw "Unable to read Python version.`n$($pythonVersionResult.Output)"
    }
    $pythonVersion = $pythonVersionResult.Output
    if ($pythonVersion -notmatch "^3\.10\.") {
        throw "Expected Python 3.10.x, got '$pythonVersion'."
    }

    $packageCode = "import cv2, grpc, numpy, PySide2, google.protobuf; print('PySide2=' + PySide2.__version__); print('grpcio=' + grpc.__version__); print('protobuf=' + google.protobuf.__version__); print('OpenCV=' + cv2.__version__); print('NumPy=' + numpy.__version__)"
    $packageResult = Get-CondaPythonProbe -Code $packageCode
    if ($packageResult.ExitCode -ne 0) {
        throw "Required package imports failed.`n$($packageResult.Output)"
    }
    $packages = ($packageResult.Output -replace "`r", "").Trim()
    if ($packages -notmatch "(?m)^PySide2=5\.15\.") {
        throw "Expected PySide2 5.15.x, got:`n$packages"
    }
    if ($packages -notmatch "(?m)^grpcio=1\.78\.0$") {
        throw "Expected grpcio 1.78.0, got:`n$packages"
    }
    if ($packages -notmatch "(?m)^protobuf=6\.33\.6$") {
        throw "Expected protobuf 6.33.6, got:`n$packages"
    }
    if ($packages -notmatch "(?m)^NumPy=([01])\.") {
        throw "Expected NumPy < 2, got:`n$packages"
    }

    $pipCheck = Invoke-CondaCapture -Arguments @("run", "-n", $CondaEnvironment, "python", "-m", "pip", "check")
    if ($pipCheck.ExitCode -ne 0) {
        throw "pip check failed with exit code $($pipCheck.ExitCode).`n$($pipCheck.Output)"
    }

    return [pscustomobject]@{
        PythonVersion = $pythonVersion
        Packages = $packages
        PipCheck = $pipCheck.Output
    }
}

function Get-GitValue {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $lines = @(& git -C $RepoRoot @Arguments 2>$null | ForEach-Object { $_.ToString() })
    if ($LASTEXITCODE -ne 0) {
        return "UNAVAILABLE"
    }
    return ($lines -join [Environment]::NewLine).Trim()
}

function Get-WorktreeState {
    $status = Get-GitValue -Arguments @("status", "--short")
    if ($status -eq "") {
        return "clean"
    }
    return "dirty"
}

function Get-WindowsVersionText {
    try {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
        return "$($os.Caption); Version=$($os.Version); Build=$($os.BuildNumber)"
    }
    catch {
        return "UNAVAILABLE: $($_.Exception.Message)"
    }
}

function Get-ScreenScaleText {
    try {
        $desktop = Get-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name LogPixels -ErrorAction Stop
        $dpi = [int]$desktop.LogPixels
        $percent = [math]::Round(($dpi / 96.0) * 100)
        return "Primary LogPixels=$dpi (~$percent%)"
    }
    catch {
        return "UNAVAILABLE: $($_.Exception.Message)"
    }
}

function Ensure-WorkspaceDirectories {
    foreach ($directory in @(
        $Workspace,
        $InputDirectory,
        $OutputDirectory,
        $ScreenshotDirectory,
        $LogDirectory,
        $EvidenceDirectory,
        $ProjectDirectory,
        $RuntimeDataDirectory
    )) {
        if (-not (Test-Path -LiteralPath $directory)) {
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
        }
    }
}

function New-FixedTestImage {
    $oldImagePath = [Environment]::GetEnvironmentVariable("EMO_MANUAL_TEST_IMAGE_PATH", "Process")
    $env:EMO_MANUAL_TEST_IMAGE_PATH = $InputImage
    try {
        $code = "import os; from pathlib import Path; import cv2; import numpy as np; image=np.zeros((720,1280,3),dtype=np.uint8); cv2.rectangle(image,(120,120),(560,420),(255,255,255),thickness=-1); cv2.circle(image,(900,300),150,(255,255,255),thickness=-1); cv2.putText(image,'EMO MASTER',(385,620),cv2.FONT_HERSHEY_SIMPLEX,2.0,(255,255,255),4,cv2.LINE_AA); path=Path(os.environ['EMO_MANUAL_TEST_IMAGE_PATH']); path.parent.mkdir(parents=True,exist_ok=True); assert image.shape == (720,1280,3) and cv2.imwrite(str(path),image); print(str(path))"
        $result = Get-CondaPythonProbe -Code $code
        if ($result.ExitCode -ne 0) {
            throw "OpenCV test image generation failed.`n$($result.Output)"
        }
    }
    finally {
        if ($null -eq $oldImagePath) {
            Remove-Item Env:EMO_MANUAL_TEST_IMAGE_PATH -ErrorAction SilentlyContinue
        }
        else {
            $env:EMO_MANUAL_TEST_IMAGE_PATH = $oldImagePath
        }
    }
}

function Write-ManualTemplates {
    $issueContent = @'
# Designer manual acceptance issue log

Record one issue per row and keep the related screenshot, log, or reproduction steps.

| ID | Case | Severity | Result | Reproduction | Expected | Actual | Screenshot/Log |
|---|---|---|---|---|---|---|---|
| 1 |  | Blocker/Major/Minor | OPEN |  |  |  |  |

## Environment

- Branch:
- HEAD:
- Windows:
- DPI:
- Workspace:

## Notes

'@
    $resultContent = @'
# EmoMaster Designer manual acceptance result

FRONTEND_MANUAL_TEST=PENDING
BRANCH=
HEAD=
TEST_DATE=
TESTER=

AUTOMATED_GATE=
WINDOWS_DESKTOP_SMOKE=
PROJECT_CREATE_SAVE_LOAD=
WORKFLOW_BOUNDARY_NODES=
SUBFLOW_DATA_FLOW=
REPEAT=
FOREACH_CONFIG=
WHILE_CONFIG=
LIVE_EVENTS=
RUNTIME_VISUAL_STATE=
GRACEFUL_STOP=
FORCE_STOP=
CLOSE_WHILE_RUNNING=
EMBEDDED_RUNTIME=
EXTERNAL_RUNTIME=
DPI_100=
DPI_125=
DPI_150=
UNHANDLED_EXCEPTION=
RESIDUAL_PROCESS=
RUNTIME_LOCK_RESTART=
FINAL_RESULT=

## Issues

| ID | Case | Severity | Result | Screenshot/Log |
|---|---|---|---|---|

## Evidence

- Screenshots: manual_test_workspace/screenshots/
- Logs: manual_test_workspace/logs/
- Environment: manual_test_workspace/evidence/environment.txt
'@
    Write-Utf8FileIfMissing -Path $IssueTemplate -Content $issueContent
    Write-Utf8FileIfMissing -Path $ManualResult -Content $resultContent
}

function Get-AutomatedSummaryText {
    if (Test-Path -LiteralPath $AutomatedSummary) {
        $summary = Get-Content -LiteralPath $AutomatedSummary -Raw
        if (-not [string]::IsNullOrWhiteSpace($summary)) {
            return $summary.Trim()
        }
    }
    return "NOT_CAPTURED: Prepare does not run the full automated gate. Record the gate result in evidence/automated-gate-summary.txt."
}

function Get-PackageVersion {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Packages,
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $line = @($Packages -split "`n" | Where-Object { $_ -like "$Name=*" }) | Select-Object -First 1
    if ($null -eq $line) {
        return "UNAVAILABLE"
    }
    return $line.Substring($Name.Length + 1)
}

function Write-EnvironmentEvidence {
    param(
        [Parameter(Mandatory = $true)]
        [pscustomobject]$CondaInfo,
        [Parameter(Mandatory = $true)]
        [pscustomobject]$PackageInfo
    )

    $imageHash = (Get-FileHash -LiteralPath $InputImage -Algorithm SHA256).Hash
    $lines = @(
        "GeneratedAt=$((Get-Date).ToString("o"))",
        "Repository=$RepoRoot",
        "Branch=$(Get-GitValue -Arguments @("rev-parse", "--abbrev-ref", "HEAD"))",
        "HEAD=$(Get-GitValue -Arguments @("rev-parse", "HEAD"))",
        "OriginHead=$(Get-GitValue -Arguments @("rev-parse", "origin/agent/runtime-workflow-architecture-v1"))",
        "Worktree=$(Get-WorktreeState)",
        "Conda=$($CondaInfo.CondaPath)",
        "CondaEnvironment=$CondaEnvironment",
        "Python=$($PackageInfo.PythonVersion)",
        "PySide2=$(Get-PackageVersion -Packages $PackageInfo.Packages -Name "PySide2")",
        "grpcio=$(Get-PackageVersion -Packages $PackageInfo.Packages -Name "grpcio")",
        "protobuf=$(Get-PackageVersion -Packages $PackageInfo.Packages -Name "protobuf")",
        "OpenCV=$(Get-PackageVersion -Packages $PackageInfo.Packages -Name "OpenCV")",
        "NumPy=$(Get-PackageVersion -Packages $PackageInfo.Packages -Name "NumPy")",
        "pip check=$($PackageInfo.PipCheck)",
        "Windows=$(Get-WindowsVersionText)",
        "ScreenScale=$(Get-ScreenScaleText)",
        "TestImage=$InputImage",
        "TestImageSHA256=$imageHash",
        "AutomatedGateSummary:",
        (Get-AutomatedSummaryText)
    )
    Write-Utf8File -Path $EnvironmentEvidence -Content ($lines -join [Environment]::NewLine)
}

function Invoke-Prepare {
    $condaInfo = Assert-CondaEnvironment
    $packageInfo = Assert-EnvironmentPackages
    Ensure-WorkspaceDirectories
    New-FixedTestImage
    Write-ManualTemplates
    Write-EnvironmentEvidence -CondaInfo $condaInfo -PackageInfo $packageInfo

    Write-Host "Prepared manual test workspace: $Workspace"
    Write-Host "Generated deterministic image: $InputImage"
    Write-Host "Environment evidence: $EnvironmentEvidence"
    Write-Host "Prepare does not start a GUI or Runtime process."
    return 0
}

function Remove-ManualRuntimeOverrides {
    Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    Remove-Item Env:EMO_RUNTIME_TARGET -ErrorAction SilentlyContinue
    Remove-Item Env:EMO_RUNTIME_DB_PATH -ErrorAction SilentlyContinue
    Remove-Item Env:EMO_MASTER_RUNTIME_DB_PATH -ErrorAction SilentlyContinue
    $env:EMO_RUNTIME_DATA_DIR = $RuntimeDataDirectory
}

function Set-ExternalDesignerEnvironment {
    Remove-ManualRuntimeOverrides
    $env:EMO_RUNTIME_TARGET = "127.0.0.1:50051"
}

function Get-ListeningPortRecords {
    try {
        return @(Get-NetTCPConnection -LocalPort 50051 -State Listen -ErrorAction Stop)
    }
    catch {
        return @()
    }
}

function Get-PortStatusText {
    $connections = @(Get-ListeningPortRecords)
    if ($connections.Count -gt 0) {
        $lines = @("LISTENING")
        foreach ($connection in $connections) {
            $processName = "UNKNOWN"
            try {
                $processName = (Get-Process -Id $connection.OwningProcess -ErrorAction Stop).ProcessName
            }
            catch {
                $processName = "UNKNOWN"
            }
            $lines += "Local=$($connection.LocalAddress):$($connection.LocalPort); PID=$($connection.OwningProcess); Process=$processName"
        }
        return ($lines -join [Environment]::NewLine)
    }

    $netstatLines = @(& netstat -ano -p TCP 2>$null | Select-String -Pattern ":50051\s+.*LISTENING")
    if ($netstatLines.Count -gt 0) {
        return "LISTENING (netstat fallback)`n$($netstatLines -join [Environment]::NewLine)"
    }
    return "AVAILABLE (no TCP LISTEN entry for 127.0.0.1:50051)"
}

function Invoke-InteractiveDev {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("run-designer", "run-runtime")]
        [string]$Action,
        [Parameter(Mandatory = $true)]
        [string]$LogName
    )

    Ensure-WorkspaceDirectories
    $logPath = Join-Path $LogDirectory ("{0}-{1}.log" -f $LogName, (Get-Date -Format "yyyyMMdd-HHmmss"))
    Write-Host "Starting foreground command: conda run -n $CondaEnvironment python scripts/dev.py $Action"
    Write-Host "Runtime data directory: $RuntimeDataDirectory"
    Write-Host "Console output is also written to: $logPath"
    & conda run --no-capture-output -n $CondaEnvironment python scripts/dev.py $Action 2>&1 | Tee-Object -FilePath $logPath | Out-Host
    return $LASTEXITCODE
}

function Invoke-Embedded {
    $null = Assert-CondaEnvironment
    Remove-ManualRuntimeOverrides
    if ([Environment]::GetEnvironmentVariable("QT_QPA_PLATFORM", "Process")) {
        throw "QT_QPA_PLATFORM is still set; embedded mode refuses to launch offscreen."
    }
    return Invoke-InteractiveDev -Action "run-designer" -LogName "embedded-designer"
}

function Invoke-ExternalRuntime {
    $null = Assert-CondaEnvironment
    Remove-ManualRuntimeOverrides
    $portStatus = Get-PortStatusText
    if ($portStatus -notlike "AVAILABLE*") {
        Write-Warning "127.0.0.1:50051 is already occupied. No process was terminated."
        Write-Host $portStatus
        return 2
    }
    Write-Host "Starting external Runtime. Keep this terminal open and inspect its logs."
    return Invoke-InteractiveDev -Action "run-runtime" -LogName "external-runtime"
}

function Invoke-ExternalDesigner {
    $null = Assert-CondaEnvironment
    Set-ExternalDesignerEnvironment
    if ([Environment]::GetEnvironmentVariable("QT_QPA_PLATFORM", "Process")) {
        throw "QT_QPA_PLATFORM is still set; external Designer mode refuses to launch offscreen."
    }
    Write-Host "Connecting Designer to external Runtime at $env:EMO_RUNTIME_TARGET"
    Write-Host "Closing Designer will not terminate the external Runtime process."
    return Invoke-InteractiveDev -Action "run-designer" -LogName "external-designer"
}

function Get-ManualTestProcesses {
    $escapedRoot = [regex]::Escape($RepoRoot)
    $pattern = "(?i)($escapedRoot|emo_master[\\/]+apps[\\/]+(designer|runtime)[\\/]+main\.py|scripts[\\/]+dev\.py.*run-(designer|runtime))"
    try {
        $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    }
    catch {
        return @()
    }
    return @($processes | Where-Object {
        $id = [int]$_.ProcessId
        $commandLine = [string]$_.CommandLine
        $id -ne $PID -and
            -not ($commandLine -match "manual_designer_test\.ps1.*CleanWorkspace") -and
            -not [string]::IsNullOrWhiteSpace($commandLine) -and
            $commandLine -match $pattern
    })
}

function Write-ProcessList {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Processes
    )

    foreach ($process in $Processes) {
        Write-Output "PID=$($process.ProcessId) Name=$($process.Name) CommandLine=$($process.CommandLine)"
    }
}

function Invoke-Diagnose {
    Write-Output "=== EmoMaster manual test diagnosis ==="
    Write-Output "Repository=$RepoRoot"
    Write-Output "Branch=$(Get-GitValue -Arguments @("rev-parse", "--abbrev-ref", "HEAD"))"
    Write-Output "HEAD=$(Get-GitValue -Arguments @("rev-parse", "HEAD"))"
    Write-Output "OriginHead=$(Get-GitValue -Arguments @("rev-parse", "origin/agent/runtime-workflow-architecture-v1"))"
    Write-Output "WorktreeStatus=$(Get-WorktreeState)"

    $conda = Get-Command conda -ErrorAction SilentlyContinue
    if ($null -eq $conda) {
        Write-Output "Conda=NOT_FOUND"
    }
    else {
        Write-Output "Conda=$($conda.Source)"
        $envList = Invoke-CondaCapture -Arguments @("env", "list")
        Write-Output "CondaEnvListExitCode=$($envList.ExitCode)"
        Write-Output $envList.Output
    }

    foreach ($name in @("QT_QPA_PLATFORM", "EMO_RUNTIME_TARGET", "EMO_RUNTIME_DATA_DIR", "EMO_RUNTIME_DB_PATH", "EMO_MASTER_RUNTIME_DB_PATH")) {
        $value = [Environment]::GetEnvironmentVariable($name, "Process")
        if ([string]::IsNullOrEmpty($value)) {
            $value = "<unset>"
        }
        Write-Output "$name=$value"
    }

    Write-Output "Port50051=$(Get-PortStatusText)"
    Write-Output "--- Python processes containing EmoMaster ---"
    $processes = @(Get-ManualTestProcesses)
    if ($processes.Count -eq 0) {
        Write-Output "NONE"
    }
    else {
        Write-ProcessList -Processes $processes | Out-Host
    }

    Write-Output "--- manual_test_workspace ---"
    if (-not (Test-Path -LiteralPath $Workspace)) {
        Write-Output "MISSING: $Workspace"
    }
    else {
        Get-ChildItem -LiteralPath $Workspace -Force | ForEach-Object {
            $kind = if ($_.PSIsContainer) { "DIR" } else { "FILE" }
            $size = if ($_.PSIsContainer) { "-" } else { $_.Length }
            Write-Output "$kind $size $($_.LastWriteTime.ToString("o")) $($_.FullName)"
        }
    }

    Write-Output "--- Runtime SQLite files ---"
    if (Test-Path -LiteralPath $RuntimeDataDirectory) {
        $databaseFiles = @(Get-ChildItem -LiteralPath $RuntimeDataDirectory -File -Recurse -Force -ErrorAction SilentlyContinue | Where-Object {
            $_.Extension -in @(".db", ".sqlite", ".sqlite3")
        })
        if ($databaseFiles.Count -eq 0) {
            Write-Output "NONE"
        }
        else {
            $databaseFiles | ForEach-Object {
                Write-Output "$($_.Length) bytes $($_.LastWriteTime.ToString("o")) $($_.FullName)"
            }
        }
    }
    else {
        Write-Output "RUNTIME_DATA_DIR_MISSING: $RuntimeDataDirectory"
    }

    Write-Output "--- Runtime lock files ---"
    $lockParent = Split-Path -Parent $RuntimeDataDirectory
    $lockFiles = @()
    if (Test-Path -LiteralPath $lockParent) {
        $lockFiles = @(Get-ChildItem -LiteralPath $lockParent -File -Force -Filter "*.runtime.lock" -ErrorAction SilentlyContinue)
    }
    if ($lockFiles.Count -eq 0) {
        Write-Output "NONE"
    }
    else {
        $lockFiles | ForEach-Object {
            Write-Output "$($_.Length) bytes $($_.LastWriteTime.ToString("o")) $($_.FullName)"
        }
    }

    Write-Output "--- Output files ---"
    if (-not (Test-Path -LiteralPath $OutputDirectory)) {
        Write-Output "OUTPUT_DIR_MISSING: $OutputDirectory"
    }
    else {
        $outputFiles = @(Get-ChildItem -LiteralPath $OutputDirectory -File -Recurse -Force -ErrorAction SilentlyContinue)
        if ($outputFiles.Count -eq 0) {
            Write-Output "NONE"
        }
        else {
            $outputFiles | ForEach-Object {
                Write-Output "$($_.Length) bytes $($_.LastWriteTime.ToString("o")) $($_.FullName)"
            }
        }
    }
    return
}

function Assert-SafeWorkspaceTarget {
    $expected = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot "manual_test_workspace"))
    $actual = [System.IO.Path]::GetFullPath($Workspace)
    $parent = [System.IO.Path]::GetFullPath($RepoRoot)
    if ($actual -ne $expected -or [System.IO.Path]::GetDirectoryName($actual) -ne $parent) {
        throw "Refusing to clean unexpected path: $actual"
    }
}

function Invoke-CleanWorkspace {
    $processes = @(Get-ManualTestProcesses)
    if ($processes.Count -gt 0) {
        Write-Warning "Manual Designer/Runtime processes are still running. Cleanup was stopped; no process will be terminated."
        Write-ProcessList -Processes $processes | Out-Host
        return 2
    }
    if (-not (Test-Path -LiteralPath $Workspace)) {
        Write-Host "Workspace does not exist: $Workspace"
        return 0
    }

    Assert-SafeWorkspaceTarget
    if (-not $Force) {
        $answer = Read-Host "Type CLEAN to remove only $Workspace"
        if ($answer -ne "CLEAN") {
            Write-Host "Cleanup cancelled."
            return 0
        }
    }
    Remove-Item -LiteralPath $Workspace -Recurse -Force
    Write-Host "Removed only: $Workspace"
    $normalRuntime = [Environment]::ExpandEnvironmentVariables("%USERPROFILE%\.emo_master\runtime")
    Write-Host "The normal user runtime directory was not touched: $normalRuntime"
    return 0
}

try {
    if ($Mode -eq "Diagnose") {
        Invoke-Diagnose | Out-Host
        exit 0
    }

    $exitCode = switch ($Mode) {
        "Prepare" { Invoke-Prepare }
        "Embedded" { Invoke-Embedded }
        "ExternalRuntime" { Invoke-ExternalRuntime }
        "ExternalDesigner" { Invoke-ExternalDesigner }
        "CleanWorkspace" { Invoke-CleanWorkspace }
        default { throw "Unsupported mode: $Mode" }
    }
    exit ([int]$exitCode)
}
catch {
    $message = $_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($message)) {
        $message = $_.ToString()
    }
    Write-Host "Manual test tool failed: $message"
    exit 1
}
