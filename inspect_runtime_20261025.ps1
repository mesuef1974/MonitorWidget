# Read-only deployment evidence. Does not run task actions, Python, installers,
# cleanup, or scripts being inspected. Never emits task argument strings.
# Run: pwsh -NoProfile -File .\inspect_runtime_20261025.ps1
# Redirect the resulting JSON to an evidence file if required.
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$approvedRoots = @(
    $projectRoot,
    (Join-Path (Split-Path $projectRoot -Parent) 'Cleanup'),
    (Join-Path (Split-Path $projectRoot -Parent) 'MachineCare'),
    (Join-Path $env:ProgramData 'MonitorWidget'),
    (Join-Path $env:USERPROFILE 'AppData\Local\Programs\Python'),
    $env:ProgramFiles,
    ${env:ProgramFiles(x86)},
    $env:SystemRoot
) | Where-Object { $_ } | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') }
$warnings = [Collections.Generic.List[string]]::new()

function Get-ApprovedPath([string]$Candidate) {
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return $null }
    $candidateText = [Environment]::ExpandEnvironmentVariables($Candidate.Trim('"'))
    if (-not [IO.Path]::IsPathRooted($candidateText)) { return $null }
    try { $full = [IO.Path]::GetFullPath($candidateText) } catch { return $null }
    foreach ($root in $approvedRoots) {
        if ($full.Equals($root, [StringComparison]::OrdinalIgnoreCase) -or
            $full.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
            return $full
        }
    }
    return $null
}

function Get-FileEvidence([string]$Candidate) {
    $path = Get-ApprovedPath $Candidate
    if (-not $path) { return [pscustomobject]@{ status = 'outside-approved-roots' } }
    if (-not (Test-Path -LiteralPath $path)) {
        return [pscustomobject]@{ path = $path; status = 'missing' }
    }
    $item = Get-Item -LiteralPath $path -Force
    # Do not inspect paths redirected by a reparse point. Parent paths are
    # checked too, so an approved textual root cannot conceal a junction.
    $cursor = $item
    while ($cursor) {
        if (($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            return [pscustomobject]@{ path = $path; status = 'reparse-point-not-inspected' }
        }
        $cursor = if ($cursor.PSIsContainer) { $cursor.Parent } else { $cursor.Directory }
    }
    $hash = $null
    $version = $null
    if (-not $item.PSIsContainer) {
        if ($item.Extension -notin @('.exe', '.dll', '.py', '.pyw', '.ps1', '.cs')) {
            return [pscustomobject]@{ path = $path; status = 'file-type-not-inspected' }
        }
        $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
        if ($item.Extension -in @('.exe', '.dll')) { $version = $item.VersionInfo.FileVersion }
    }
    $access = @()
    try {
        $acl = Get-Acl -LiteralPath $path
        $access = @($acl.Access | ForEach-Object {
            $identity = try { $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value } catch { 'unresolved-identity' }
            [pscustomobject]@{
                sid = $identity
                type = [string]$_.AccessControlType
                rights = [string]$_.FileSystemRights
                inherited = $_.IsInherited
            }
        })
        $protected = $acl.AreAccessRulesProtected
    } catch {
        $protected = $null
        $warnings.Add('ACL unavailable for one approved evidence path')
    }
    [pscustomobject]@{
        path = $path; status = 'inspected'; fileVersion = $version
        sha256 = $hash; aclProtected = $protected; access = $access
    }
}

$knownTasks = @{}
$tasksFile = Join-Path $projectRoot 'tasks.json'
if (Test-Path -LiteralPath $tasksFile) {
    try {
        foreach ($entry in (Get-Content -LiteralPath $tasksFile -Raw | ConvertFrom-Json)) {
            $knownTasks[([string]$entry.path + [string]$entry.name)] = $true
        }
    } catch { $warnings.Add('Project task allowlist could not be read') }
}
$taskEvidence = @()
$pathSet = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($candidate in @(
    (Join-Path $env:ProgramData 'MonitorWidget'),
    (Join-Path $env:ProgramData 'MonitorWidget\bin'),
    (Join-Path $env:ProgramData 'MonitorWidget\cleanup'),
    (Join-Path $env:ProgramData 'MonitorWidget\out'),
    (Join-Path $projectRoot 'server.py'),
    (Join-Path $projectRoot 'agent.py'),
    (Join-Path $projectRoot 'collector.py'),
    (Join-Path $projectRoot 'purge.exe')
)) { $null = $pathSet.Add($candidate) }

try {
    $tasks = @(Get-ScheduledTask | Where-Object {
        $_.TaskName -match '^(MonitorWidget(?:-|$)|MemoryPurge-|Cleanup_v2_)' -or
        $knownTasks.ContainsKey(([string]$_.TaskPath + [string]$_.TaskName))
    })
    $taskEvidence = @($tasks | ForEach-Object {
        $task = $_
        $actions = @($task.Actions | ForEach-Object {
            $action = $_
            $execute = Get-ApprovedPath ([string]$action.Execute)
            $scripts = @()
            # Parse ONLY absolute script filenames. Arguments themselves and
            # all other values (including tokens) are never output or logged.
            foreach ($match in [regex]::Matches([string]$action.Arguments, '(?i)"([A-Z]:\\[^"\r\n]+\.(?:ps1|py|pyw))"|(?<!\S)([A-Z]:\\[^\s"\r\n]+\.(?:ps1|py|pyw))(?=\s|$)')) {
                $rawPath = if ($match.Groups[1].Success) { $match.Groups[1].Value } else { $match.Groups[2].Value }
                $script = Get-ApprovedPath $rawPath
                if ($script) { $scripts += $script; $null = $pathSet.Add($script) }
            }
            if ($execute) {
                $null = $pathSet.Add($execute)
                if ([IO.Path]::GetFileName($execute) -match '^pythonw?\.exe$') {
                    $dll = Join-Path (Split-Path $execute -Parent) 'DLLs\sqlite3.dll'
                    $null = $pathSet.Add($dll)
                }
            }
            [pscustomobject]@{
                executable = if ($execute) { $execute } else { 'outside-approved-roots-or-relative' }
                scripts = @($scripts | Select-Object -Unique)
            }
        })
        [pscustomobject]@{
            name = $task.TaskName; path = $task.TaskPath
            runLevel = [string]$task.Principal.RunLevel
            state = [string]$task.State; actions = $actions
        }
    })
} catch { $warnings.Add('Scheduled task inspection unavailable; no task was modified') }

$files = @($pathSet | Sort-Object | ForEach-Object {
    try { Get-FileEvidence $_ } catch { [pscustomobject]@{ status = 'evidence-read-failed' } }
})
[pscustomobject]@{
    collectedUtc = [DateTime]::UtcNow.ToString('o')
    mode = 'read-only; no task actions or inspected programs executed'
    tasks = $taskEvidence; files = $files; warnings = @($warnings)
    runtimeLimit = 'File versions and hashes only; determine loaded SQLite version inside each trusted interpreter separately.'
} | ConvertTo-Json -Depth 10
