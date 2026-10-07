param(
    [Parameter(Mandatory = $true)][string]$ArtifactDirectory,
    [Parameter(Mandatory = $true)][string]$ReleaseTag,
    [Parameter(Mandatory = $true)][string]$SourceCommit,
    [Parameter(Mandatory = $true)][string]$PackagerCommit,
    [double]$DurationSeconds = 60,
    [int]$Restarts = 10
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $ArtifactDirectory).Path
$evidence = Join-Path $root 'archive-check'
New-Item -ItemType Directory -Path $evidence -Force | Out-Null
$validation = [ordered]@{ status = 'FAIL'; source_commit = $SourceCommit;
    packager_commit = $PackagerCommit; archiveAcceptance = 'NOT_RUN';
    nativeAcceptance = 'NOT_RUN'; cleanWindows = 'NOT_RUN'; fieldAcceptance = 'NOT_RUN' }
try {
    $manifest = Get-Content -LiteralPath (Join-Path $root 'manifest.json') -Raw | ConvertFrom-Json
    $identity = Get-Content -LiteralPath (Join-Path $root 'runtime-build.json') -Raw | ConvertFrom-Json
    if ($manifest.source_repo -ne 'jsdfhasuh/emo_master' -or $manifest.source_commit -ne $SourceCommit -or
        $identity.source_commit -ne $SourceCommit -or $identity.packager_commit -ne $PackagerCommit -or
        $manifest.version -ne $ReleaseTag.TrimStart('v') -or $identity.publish_requested) {
        throw 'Runtime manifest or checkout identity mismatch'
    }
    $archivePath = Join-Path $root "emo-master-runtime-windows-$ReleaseTag.zip"
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($manifest.sha256 -ne $archiveHash) { throw 'Runtime ZIP SHA256 mismatch' }
    $validation.archiveSha256 = $archiveHash
    $validation.archiveSize = (Get-Item -LiteralPath $archivePath).Length
    $validation.workflow_sha = $identity.workflow_sha
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $entries = @($zip.Entries | Where-Object { $_.FullName -match '(^|/)EmoMasterRuntime\.exe$' })
        if ($entries.Count -ne 1) { throw 'Expected exactly one Runtime EXE in the ZIP' }
        $stream = $entries[0].Open()
        $hasher = [System.Security.Cryptography.SHA256]::Create()
        try { $exeHash = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
        finally { $stream.Dispose(); $hasher.Dispose() }
        $validation.executableSha256 = $exeHash
        $validation.executableMember = $entries[0].FullName
    } finally { $zip.Dispose() }

    $archiveReport = Join-Path $evidence 'archive.json'
    & (Join-Path $PSScriptRoot 'verify_runtime_package.ps1') -Archive $archivePath -ReportPath $archiveReport
    $execution = Get-Content -LiteralPath ($archiveReport + '.execution.json') -Raw | ConvertFrom-Json
    if ($execution.executableSha256 -ne $exeHash) { throw 'Extracted Runtime EXE hash mismatch' }
    $validation.archiveAcceptance = 'PASS'
    & (Join-Path $PSScriptRoot 'verify_runtime_package.ps1') -Executable $execution.executable `
        -ReportPath (Join-Path $evidence 'native.json') -DurationSeconds $DurationSeconds `
        -Restarts $Restarts -QtPlatform windows -TimeoutSeconds 600
    $validation.nativeAcceptance = 'PASS'
    $validation.status = 'PASS'
    Write-Output "Runtime archive and native acceptance passed: $evidence"
} catch {
    $validation.error = $_.Exception.Message
    throw
} finally {
    $validation | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'validation.json') -Encoding utf8
}
