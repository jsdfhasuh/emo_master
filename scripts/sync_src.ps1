<#
.SYNOPSIS
    Copy new and modified working-tree source files to the Windows Designer PC.
.EXAMPLE
    .\scripts\sync_src.ps1 -ComputerName designer-pc -RemoteRoot 'D:\emo_master' -Preview
.EXAMPLE
    .\scripts\sync_src.ps1 -ComputerName designer-pc -RemoteRoot 'D:\emo_master'
.EXAMPLE
    .\scripts\sync_src.ps1 -ComputerName line-2 -RemoteRoot 'E:\emo_master' -VerifyRemote
.EXAMPLE
    .\scripts\sync_src.ps1 -ComputerName line-2 -RemoteRoot 'E:\emo_master' -TimeoutSeconds 300 -BatchSizeMB 2
.DESCRIPTION
    Each SSH target and project directory has its own record under LOCALAPPDATA.
    Without a record all files are pending. For an existing installation, use
    -VerifyRemote to seed the record without uploading. Use it again after
    changing files directly on the target or reassigning an SSH alias.
    -Preview reads the local record. -VerifyRemote -Preview checks the target
    without saving. -ListTargets lists the locally recorded destinations.
#>
param(
    [Alias('WhatIf')][switch]$Preview,
    [ValidatePattern('^[a-zA-Z0-9_][a-zA-Z0-9_.@-]*$')][string]$ComputerName,
    [ValidatePattern('^[A-Za-z]:[\\/]')][string]$RemoteRoot,
    [ValidateRange(1, 3600)][int]$TimeoutSeconds = 120,
    [ValidateRange(1, 256)][int]$BatchSizeMB = 4,
    [switch]$VerifyRemote,
    [switch]$ListTargets
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$utf8 = New-Object System.Text.UTF8Encoding($false)
$stateDirectory = Join-Path $env:LOCALAPPDATA 'emo_master\source-sync'
if ($ListTargets) {
    $records = @(if ([IO.Directory]::Exists($stateDirectory)) {
        foreach ($path in [IO.Directory]::GetFiles($stateDirectory, '*.json')) {
            ([IO.File]::ReadAllText($path) | ConvertFrom-Json) | Select-Object ComputerName, RemoteRoot
        }
    })
    if (!$records.Count) { Write-Host 'No saved targets yet.'; return }
    $records | Sort-Object ComputerName, RemoteRoot
    return
}
if (!$ComputerName -or !$RemoteRoot) { throw 'Specify -ComputerName and -RemoteRoot for the sync target.' }
$RemoteRoot = [IO.Path]::GetFullPath($RemoteRoot.Replace('/', '\'))
if ($RemoteRoot.Length -gt 3) { $RemoteRoot = $RemoteRoot.TrimEnd('\') }
$identity = $ComputerName.ToLowerInvariant() + '|' + $RemoteRoot.ToLowerInvariant()
$sha = [Security.Cryptography.SHA256]::Create()
try { $key = [BitConverter]::ToString($sha.ComputeHash($utf8.GetBytes($identity))).Replace('-', '') }
finally { $sha.Dispose() }
$statePath = Join-Path $stateDirectory "$key.json"

function Save-Record([hashtable]$Confirmed) {
    $record = @{ ComputerName = $ComputerName; RemoteRoot = $RemoteRoot; files = $Confirmed }
    $temp = Join-Path $stateDirectory ([IO.Path]::GetRandomFileName())
    try {
        [IO.File]::WriteAllText($temp, (ConvertTo-Json -Compress -Depth 3 -InputObject $record), $utf8)
        if ([IO.File]::Exists($statePath)) {
            [IO.File]::Replace($temp, $statePath, [System.Management.Automation.Language.NullString]::Value)
        } else { [IO.File]::Move($temp, $statePath) }
    } finally { [IO.File]::Delete($temp) }
}

Add-Type -AssemblyName System.IO.Compression

# Keep the Windows SSH command short. Read the script and payload by length so
# the remote PowerShell can finish without waiting for SSH to deliver stdin EOF.
function Invoke-Remote([string]$Script, [byte[]]$Payload) {
    $scriptBytes = $utf8.GetBytes($Script)
    $bootstrap = @"
`$ErrorActionPreference = 'Stop'
try {
    `$reader = [IO.BinaryReader]::new([Console]::OpenStandardInput())
    `$code = `$reader.ReadBytes($($scriptBytes.Length))
    `$payloadBytes = `$reader.ReadBytes($($Payload.Length))
    if (`$code.Length -ne $($scriptBytes.Length) -or `$payloadBytes.Length -ne $($Payload.Length)) { throw 'Incomplete SSH payload' }
    & ([scriptblock]::Create([Text.Encoding]::UTF8.GetString(`$code)))
} catch { [Console]::Error.WriteLine(`$_.Exception.Message); exit 1 }
"@
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = 'ssh.exe'
    $missedReplies = [Math]::Ceiling($TimeoutSeconds / 10.0)
    $start.Arguments = "-T -o BatchMode=yes -o ConnectTimeout=$TimeoutSeconds -o ServerAliveInterval=10 -o ServerAliveCountMax=$missedReplies $ComputerName powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand $encoded"
    $start.UseShellExecute = $false
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = $utf8
    $start.StandardErrorEncoding = $utf8
    # .NET Framework uses Console.InputEncoding for the child's stdin writer.
    # Its preamble must not precede the raw payload, especially a ZIP archive.
    $previousInputEncoding = [Console]::InputEncoding
    try {
        [Console]::InputEncoding = $utf8
        $process = [Diagnostics.Process]::Start($start)
    } finally { [Console]::InputEncoding = $previousInputEncoding }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $stdin = $process.StandardInput.BaseStream
        # If ssh exits early (e.g. authentication fails), retain its diagnostic.
        try {
            foreach ($part in @($scriptBytes, $Payload)) {
                $write = $stdin.WriteAsync($part, 0, $part.Length)
                $left = [Math]::Max(0, [int]($TimeoutSeconds * 1000 - $timer.ElapsedMilliseconds))
                if (!$write.Wait($left)) { throw "SSH upload timed out after ${TimeoutSeconds}s." }
            }
        } catch [AggregateException] {
            if (!$process.WaitForExit(1000)) { throw }
        } finally {
            $stdin.Close()
        }
        $remaining = [Math]::Max(0, [int]($TimeoutSeconds * 1000 - $timer.ElapsedMilliseconds))
        if (!$process.WaitForExit($remaining)) { throw "SSH command timed out after ${TimeoutSeconds}s." }
        if ($process.ExitCode -ne 0) { throw "SSH failed ($($process.ExitCode)): $($stderr.Result.Trim())" }
        return $stdout.Result.Trim()
    } finally {
        if (!$process.HasExited) { $process.Kill() }
        $process.Dispose()
    }
}

$previousEncoding = [Console]::OutputEncoding
try {
    [Console]::OutputEncoding = $utf8
    $paths = @(git -C $repo -c core.quotepath=false ls-files --cached --others --exclude-standard -- src/)
    if ($LASTEXITCODE -ne 0) { throw 'Could not list the source files with Git.' }
} finally {
    [Console]::OutputEncoding = $previousEncoding
}
$files = @{}
foreach ($path in $paths) {
    $full = Join-Path $repo $path
    if ([IO.File]::Exists($full)) {
        $files[$path] = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash
    }
}
if (!$files.Count) { throw "No source files found under $repo\src." }

$rootLiteral = "'" + $RemoteRoot.Replace("'", "''") + "'"
$remoteSetup = '$project = ' + $rootLiteral + [Environment]::NewLine + @'
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
function Get-Target([string]$name) {
    if ($name -notmatch '^src/' -or $name -match '[:\\]|//|/$|(^|/)\.{1,2}(/|$)') { throw "Invalid source path: $name" }
    $target = [IO.Path]::GetFullPath([IO.Path]::Combine($project, $name))
    $item = [IO.FileInfo]$target
    while ($item.FullName -ne $project) {
        $attributes = $item.Attributes
        if ($attributes -ne -1 -and ($attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Linked source path: $($item.FullName)"
        }
        $item = [IO.Directory]::GetParent($item.FullName)
    }
    return $target
}
$directory = Get-Item -LiteralPath $project -ErrorAction Stop
if (!$directory.PSIsContainer) { throw "Expected project directory: $project" }
$project = $directory.FullName
if ($directory.Parent) { $project = $project.TrimEnd('\') }
'@

$compare = @'
try {
    $files = [Text.Encoding]::UTF8.GetString($payloadBytes) | ConvertFrom-Json
    $properties = @($files.PSObject.Properties)
    $changed = @(foreach ($entry in $properties) {
        $path = Get-Target $entry.Name
        if (![IO.File]::Exists($path) -or (Get-FileHash -LiteralPath $path).Hash -ne $entry.Value) { $entry.Name }
    })
    [Console]::WriteLine((ConvertTo-Json -Compress -InputObject @{ checked = $properties.Count; changed = $changed }))
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
'@
$receive = @'
try {
    Add-Type -AssemblyName System.IO.Compression
    $archive = [IO.Compression.ZipArchive]::new([IO.MemoryStream]::new($payloadBytes), [IO.Compression.ZipArchiveMode]::Read)
    foreach ($entry in $archive.Entries) {
        $target = Get-Target $entry.FullName
        $dir = [IO.Path]::GetDirectoryName($target)
        [void][IO.Directory]::CreateDirectory($dir)
        $temp = [IO.Path]::Combine($dir, [IO.Path]::GetRandomFileName())
        $source = $entry.Open()
        try {
            $dest = [IO.File]::Open($temp, 'CreateNew', 'Write', 'None')
            try { $source.CopyTo($dest) } finally { $dest.Dispose() }
            if ([IO.File]::Exists($target)) { [IO.File]::Replace($temp, $target, [System.Management.Automation.Language.NullString]::Value) }
            else { [IO.File]::Move($temp, $target) }
        } finally { $source.Dispose(); [IO.File]::Delete($temp) }
    }
    [Console]::WriteLine($archive.Entries.Count)
    $archive.Dispose()
} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
'@
function Send-Batch([string[]]$Names, [hashtable]$Confirmed) {
    $packed = @{}
    $buffer = New-Object IO.MemoryStream
    try {
        $archive = [IO.Compression.ZipArchive]::new($buffer, [IO.Compression.ZipArchiveMode]::Create, $true)
        try {
            foreach ($name in $Names) {
                $source = [IO.File]::OpenRead((Join-Path $repo $name))
                try {
                    $packed[$name] = (Get-FileHash -InputStream $source -Algorithm SHA256).Hash
                    $source.Position = 0
                    $dest = $archive.CreateEntry($name).Open()
                    try { $source.CopyTo($dest) } finally { $dest.Dispose() }
                } finally { $source.Dispose() }
            }
        } finally { $archive.Dispose() }
        # The remote side may replace files before its reply reaches us.
        # Invalidate old confirmations first so even a later local revert is retried.
        foreach ($name in $Names) { $Confirmed.Remove($name) }
        Save-Record $Confirmed
        Write-Host "Uploading $($Names.Count) file(s), $($buffer.Length) compressed bytes ..."
        $count = Invoke-Remote "$remoteSetup`n$receive" $buffer.ToArray()
        if ($count -ne [string]$Names.Count) { throw "Unexpected remote file count: $count" }
        foreach ($name in $Names) { $Confirmed[$name] = $packed[$name] }
        Save-Record $Confirmed
    } finally { $buffer.Dispose() }
}

$targetLock = $null
try {
    if (!$Preview) {
        [void][IO.Directory]::CreateDirectory($stateDirectory)
        try { $targetLock = [IO.File]::Open("$statePath.lock", 'OpenOrCreate', 'ReadWrite', 'None') }
        catch [IO.IOException] { throw "Cannot lock the sync record for ${ComputerName}:$RemoteRoot. $($_.Exception.Message)" }
    }
    $confirmed = @{}
    if ($VerifyRemote) {
        Write-Host "Checking $($files.Count) files on ${ComputerName}:$RemoteRoot\src ..."
        $manifest = $utf8.GetBytes((ConvertTo-Json -Compress -InputObject $files))
        $reply = (Invoke-Remote "$remoteSetup`n$compare" $manifest) | ConvertFrom-Json
        if ($reply.checked -ne $files.Count) { throw "Remote compared $($reply.checked) of $($files.Count) source files." }
        $confirmed = $files.Clone()
        foreach ($name in $reply.changed) {
            if (!$files.ContainsKey($name)) { throw "Unexpected remote path: $name" }
            $confirmed.Remove($name)
        }
        if (!$Preview) { Save-Record $confirmed }
    } elseif ([IO.File]::Exists($statePath)) {
        $record = [IO.File]::ReadAllText($statePath) | ConvertFrom-Json
        foreach ($entry in $record.files.PSObject.Properties) { $confirmed[$entry.Name] = $entry.Value }
    } else {
        Write-Host 'First sync for this target. To check an existing copy before uploading, use -VerifyRemote.'
    }
    $changed = @($files.Keys | Where-Object { $files[$_] -ne $confirmed[$_] } | Sort-Object)
    Write-Host "${ComputerName}:$RemoteRoot\src - $($changed.Count) file(s) to copy."
    if (!$changed.Count) { return }
    if ($Preview -or $VerifyRemote) {
        foreach ($name in $changed) { Write-Host "  $name" }
        return
    }
    $batch = [Collections.Generic.List[string]]::new()
    $bytes = 0L
    $done = 0
    try {
        foreach ($name in $changed) {
            $size = ([IO.FileInfo](Join-Path $repo $name)).Length
            if ($batch.Count -and $bytes + $size -gt $BatchSizeMB * 1MB) {
                Send-Batch $batch.ToArray() $confirmed
                $done += $batch.Count
                Write-Host "Confirmed $done / $($changed.Count) files."
                $batch.Clear()
                $bytes = 0L
            }
            $batch.Add($name)
            $bytes += $size
        }
        Send-Batch $batch.ToArray() $confirmed
    } catch {
        throw "Sync incomplete. $done / $($changed.Count) files confirmed this run. Rerun the same command to resume. $($_.Exception.Message)"
    }
    Write-Host "Copied $($changed.Count) file(s) to ${ComputerName}:$RemoteRoot\src."
} finally { if ($targetLock) { $targetLock.Dispose() } }
