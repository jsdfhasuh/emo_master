# Run only inside the credential-free Windows build worker.
param(
  [Parameter(Mandatory)][ValidateSet('emo-master','emo-master-runtime')][string]$Target,
  [Parameter(Mandatory)][string]$SourceRoot,
  [Parameter(Mandatory)][ValidatePattern('\A[0-9a-f]{40}\z')][string]$SourceSha,
  [Parameter(Mandatory)][string]$PackagerRoot,
  [ValidateSet('91766f6d18ca9a333f64cf1c6030202457ec4a8a','23b6774c4f99c6ad7fffbf13b3ebbc8a453d7984')][string]$PackagerSha = '91766f6d18ca9a333f64cf1c6030202457ec4a8a',
  [ValidatePattern('\A[0-9a-f]{40}\z')][string]$PreviousSourceSha,
  [Parameter(Mandatory)][ValidatePattern('\A[A-Za-z0-9][A-Za-z0-9._-]{0,79}\z')][string]$ReleaseTag,
  [Parameter(Mandatory)][string]$OutputDirectory,
  [ValidatePattern('\A[0-9a-f]{40}\z')][string]$WorkflowSha
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
if (-not $IsWindows) { throw 'Windows PowerShell 7 worker required' }
foreach ($key in @('GH_TOKEN','GITHUB_TOKEN','EMO_CI_TOKEN','SOURCE_REPO_TOKEN','RELEASE_REPO_TOKEN','ACTIONS_RUNTIME_TOKEN','ACTIONS_ID_TOKEN_REQUEST_TOKEN')) {
  if ([Environment]::GetEnvironmentVariable($key)) { throw 'Build worker must not contain runner credentials' }
}
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$PackagerRoot = (Resolve-Path -LiteralPath $PackagerRoot).Path
foreach ($pair in @(@{ Path=$SourceRoot; Sha=$SourceSha }, @{ Path=$PackagerRoot; Sha=$packagerSha })) {
  if ((git -C $pair.Path rev-parse HEAD).Trim() -cne $pair.Sha) { throw 'Checkout SHA mismatch' }
  if (git -C $pair.Path status --porcelain --untracked-files=all) { throw 'Checkout is not pristine' }
}
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Output directory must not exist' }
if ((python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))").Trim() -ne '3.10') { throw 'Python 3.10 required' }
if (-not $env:ISCC_PATH -or -not (Test-Path -LiteralPath $env:ISCC_PATH -PathType Leaf)) { throw 'Preverified Inno Setup 6.7.3 compiler is required' }
$OutputDirectory = (New-Item -ItemType Directory -Path $OutputDirectory).FullName
$package = (New-Item -ItemType Directory -Path (Join-Path $OutputDirectory 'package')).FullName
$evidence = (New-Item -ItemType Directory -Path (Join-Path $OutputDirectory 'evidence')).FullName
$phasePath = Join-Path $OutputDirectory 'stage.txt'
function Set-ValidationStage {
  param([ValidateSet('runtime-source','runtime-identity','build','runtime-archive-native','designer-icons','designer-contract','designer-frozen','complete')][string]$Stage)
  Set-Content -LiteralPath $phasePath -Value $Stage -Encoding ascii -NoNewline
}
$env:PYTHONUTF8 = '1'
if ($Target -eq 'emo-master-runtime') {
  Set-ValidationStage 'runtime-source'
  Push-Location $SourceRoot
  try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    & ./scripts/validate_runtime_source.ps1 -ReportDirectory (Join-Path $package 'source-check')
    Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
  } finally { Pop-Location }
  Set-ValidationStage 'runtime-identity'
  [ordered]@{
    source_repo = 'jsdfhasuh/emo_master'
    source_commit = $SourceSha
    packager_repo = 'jsdfhasuh/python_build_scripts'
    packager_commit = $packagerSha
    requested_source_ref = $SourceSha
    requested_packager_ref = $packagerSha
    workflow_sha = if ($WorkflowSha) { $WorkflowSha } else { $null }
    publish_requested = $false
    fieldAcceptance = 'NOT_RUN'
  } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $package 'runtime-build.json') -Encoding utf8
}
# A validation build has no changelog range. An approved release can supply a vetted local ancestor.
if (-not $PreviousSourceSha) { $PreviousSourceSha = $SourceSha }
git -C $SourceRoot merge-base --is-ancestor $PreviousSourceSha $SourceSha
Set-ValidationStage 'build'
Push-Location $PackagerRoot
try {
  & ./scripts/publish-local-release.ps1 -BuildOnly -Target $Target -ReleaseTag $ReleaseTag `
    -ReleaseRepo 'jsdfhasuh/emo_master' -SourceRoot $SourceRoot -SourceRef $SourceSha -PreviousSourceRef $PreviousSourceSha -OutputDirectory $package
} finally { Pop-Location }
Push-Location $SourceRoot
try {
  if ($Target -eq 'emo-master-runtime') {
    Set-ValidationStage 'runtime-archive-native'
    & ./scripts/verify_runtime_artifacts.ps1 -ArtifactDirectory $package -ReleaseTag $ReleaseTag -SourceCommit $SourceSha -PackagerCommit $packagerSha
  } else {
    Set-ValidationStage 'designer-icons'
    & ./scripts/verify_windows_package.ps1 -Archive (Join-Path $package "emo-master-windows-$ReleaseTag.zip") -OutputDirectory (Join-Path $evidence 'icons')
    Set-ValidationStage 'designer-contract'
    python -m pytest tests/test_release_workflow_contract.py tests/test_frozen_runtime_validation.py -q
    $env:PYTHONPATH = Join-Path $SourceRoot 'src'
    Set-ValidationStage 'designer-frozen'
    python scripts/verify_frozen_runtime.py --artifact-directory $package --output (Join-Path $evidence 'frozen-validation.json') --expected-source-sha $SourceSha
  }
} finally { Pop-Location }

Set-ValidationStage 'complete'
