# Preparation is the default. No task, process, source data, or global runtime is
# changed by Plan. Stage writes only a new protected snapshot. Apply is explicit,
# requires an already quiesced source, and leaves replacement tasks DISABLED.
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [ValidateSet('Plan','Stage','Apply','Rollback')][string]$Mode = 'Plan',
    [switch]$StageOnly,
    [string]$SourceRoot = $PSScriptRoot,
    [string]$SecureRoot = (Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MonitorWidget'),
    [string]$StagePath,
    [switch]$RotateProtectedToken,
    [switch]$MigrateStartupShortcut,
    [ValidateRange(1,1000)][int]$MaxDataGB = 10
)
$ErrorActionPreference = 'Stop'
if ($StageOnly) { $Mode = 'Stage' }
$SourceRoot = [IO.Path]::GetFullPath($SourceRoot).TrimEnd('\')
$SecureRoot = [IO.Path]::GetFullPath($SecureRoot).TrimEnd('\')
$AppRoot = Join-Path $SecureRoot 'app'
$StagingRoot = Join-Path $SecureRoot 'staging'
$startupFolder = [Environment]::GetFolderPath('Startup')
$startupShortcut = if ($startupFolder) { Join-Path $startupFolder 'MiniWidget.lnk' } else { $null }
if (-not $SecureRoot.Equals([IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MonitorWidget')).TrimEnd('\'),[StringComparison]::OrdinalIgnoreCase)) {
    throw 'This installer only supports the reviewed ProgramData\MonitorWidget destination.'
}
if ($AppRoot.IndexOfAny([char[]]@([char]39,[char]34,[char]13,[char]10,[char]0)) -ge 0) { throw 'Protected command paths may not contain quotes, newlines, or null characters.' }
$taskNames = @('MonitorWidget-Agent','MonitorWidget-Collector','MonitorWidget-Launch')
$pythonRelative = 'runtime\python\python314\python.exe'
$pythonwRelative = 'runtime\python\python314\pythonw.exe'
$codeNames = @(
    'agent.py','alertcfg.py','appearance.py','atomic_json.py','collector.py','cpuwatch.py','db.py','dbview.py',
    'diskio.py','diskio_api.py','diskio_collect.py','errlog.py','explain.py','extras.py',
    'gpuwatch.py','health.py','health_score.py','homeapi.py','infra.py','insight.py',
    'quarantine_store.py','relia.py','reliab.py','sched.py','secure.py','server.py',
    'shotwatch.py','space.py','sqlite_runtime.py','startup_mgr.py',
    'widget.ps1','collect.ps1','ops.ps1','healthprobe.ps1','relia_scan.ps1',
    'show_widget.ps1','restart_widget.ps1','restart_agent.ps1','launch_mini.vbs',
    'purge.exe','web\index.html'
)
$settingsNames = @('.token','tasks.json','alerts.json','appearance.json','widget.settings.json','cpu_ignore.txt',
    'explain_cache.json','explain_last.txt','admin_probe.json','purge_result.json')

function Assert-ChildPath([string]$Path,[string]$Parent) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $parentFull = [IO.Path]::GetFullPath($Parent).TrimEnd('\')
    if (-not $full.StartsWith($parentFull + '\',[StringComparison]::OrdinalIgnoreCase)) {
        throw 'A computed path escaped its intended protected directory.'
    }
    return $full
}

function Assert-NoReparse([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    while ($full) {
        if (Test-Path -LiteralPath $full) {
            $item = Get-Item -LiteralPath $full -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Reparse points are forbidden in migration paths.'
            }
        }
        $next = Split-Path -Parent $full
        if (-not $next -or $next -eq $full) { break }
        $full = $next
    }
}

function Assert-Admin {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Stage/Apply/Rollback require an administrator shell. Plan is read-only.'
    }
}

function Protect-Path([string]$Path,[switch]$CodeReadable) {
    $item = Get-Item -LiteralPath $Path -Force
    $security = if ($item.PSIsContainer) { [Security.AccessControl.DirectorySecurity]::new() } else { [Security.AccessControl.FileSecurity]::new() }
    $admins = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $security.SetOwner($admins)
    $security.SetAccessRuleProtection($true,$false)
    $inheritance = if ($item.PSIsContainer) { [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' } else { [Security.AccessControl.InheritanceFlags]::None }
    foreach ($sid in @($admins,$system)) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new($sid,[Security.AccessControl.FileSystemRights]::FullControl,$inheritance,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $security.AddAccessRule($rule)
    }
    if ($CodeReadable) {
        $users = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-545')
        $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($users,[Security.AccessControl.FileSystemRights]::ReadAndExecute,$inheritance,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow))
    }
    Set-Acl -LiteralPath $Path -AclObject $security
}

function Get-TaskSnapshots {
    @($taskNames | ForEach-Object {
        $task = @(Get-ScheduledTask -TaskName $_ -TaskPath '\' -ErrorAction Stop)
        if ($task.Count -ne 1) { throw 'Expected exactly one root application task.' }
        $rawXml = Export-ScheduledTask -TaskName $_ -TaskPath '\'
        [xml]$definition = $rawXml
        [pscustomobject]@{ name = $_; xml = $rawXml; enabled = [bool]$task[0].Settings.Enabled; state = [string]$task[0].State; runLevel = [string]$definition.Task.Principals.Principal.RunLevel }
    })
}

function Get-CodeInventory {
    $files = [Collections.Generic.List[object]]::new()
    foreach ($name in $codeNames) {
        $path = Join-Path $SourceRoot $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw ('Required application file missing: ' + $name) }
        Assert-NoReparse $path
        $files.Add([pscustomobject]@{ relative = $name; source = $path; sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() })
    }
    $resourceRoots = @('assets','runtime\sqlite','runtime\python\python314')
    foreach ($relativeRoot in $resourceRoots) {
        $path = Join-Path $SourceRoot $relativeRoot
        if (-not (Test-Path -LiteralPath $path -PathType Container)) { throw ('Required runtime/resource directory missing: ' + $relativeRoot) }
        Assert-NoReparse $path
        foreach ($entry in (Get-ChildItem -LiteralPath $path -Recurse -Force)) {
            Assert-NoReparse $entry.FullName
            if ($entry.PSIsContainer) { continue }
            # Download archives are provenance, not executable runtime. The
            # embedded python314.zip is REQUIRED stdlib and is preserved.
            if ($relativeRoot -eq 'runtime\sqlite' -and $entry.Extension -eq '.zip') { continue }
            $relative = $entry.FullName.Substring($SourceRoot.Length + 1)
            $files.Add([pscustomobject]@{ relative = $relative; source = $entry.FullName; sha256 = (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash.ToLowerInvariant() })
        }
    }
    $pythonManifest = Join-Path $SourceRoot 'runtime\python\manifest.json'
    if (Test-Path -LiteralPath $pythonManifest) {
        Assert-NoReparse $pythonManifest
        $files.Add([pscustomobject]@{ relative = 'runtime\python\manifest.json'; source = $pythonManifest; sha256 = (Get-FileHash -LiteralPath $pythonManifest -Algorithm SHA256).Hash.ToLowerInvariant() })
    }
    return $files.ToArray()
}

function Copy-VerifiedCode($Inventory,[string]$Destination) {
    foreach ($file in $Inventory) {
        $dest = Assert-ChildPath (Join-Path $Destination $file.relative) $Destination
        $parent = Split-Path -Parent $dest
        $null = New-Item -ItemType Directory -Force -Path $parent
        Copy-Item -LiteralPath $file.source -Destination $dest
        if ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) { throw 'Staged code hash mismatch.' }
        if ((Get-FileHash -LiteralPath $file.source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) { throw 'Source code changed during staging; prepare a fresh reviewed snapshot.' }
        Protect-Path $dest -CodeReadable
    }
}

function Assert-EmbeddedRuntime([string]$Root) {
    $exe = Join-Path $Root $pythonRelative
    $pth = Join-Path $Root 'runtime\python\python314\python314._pth'
    Assert-NoReparse $exe
    $signature = Get-AuthenticodeSignature -LiteralPath $exe
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'The embedded Python executable is not PSF-signed and valid.' }
    $paths = @(Get-Content -LiteralPath $pth | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') } | ForEach-Object { $_.Trim() })
    if (($paths -join '|') -ne 'python314.zip|.|..\..\..') { throw 'Embedded Python search paths differ from the reviewed isolated configuration.' }
}

function Copy-DataSnapshot([string]$Destination) {
    $candidates = [Collections.Generic.List[object]]::new()
    $directories = [Collections.Generic.List[string]]::new()
    foreach ($name in $settingsNames) {
        $path = Join-Path $SourceRoot $name
        if (Test-Path -LiteralPath $path -PathType Leaf) { $candidates.Add([pscustomobject]@{ source = $path; relative = $name }) }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot '.token') -PathType Leaf)) { throw 'Existing token is required; migration never prints or generates it.' }
    foreach ($directory in @('logs','quarantine','shots\auto')) {
        $path = Join-Path $SourceRoot $directory
        if (-not (Test-Path -LiteralPath $path)) { continue }
        Assert-NoReparse $path
        $directories.Add($directory)
        foreach ($item in (Get-ChildItem -LiteralPath $path -Recurse -Force)) {
            Assert-NoReparse $item.FullName
            if ($item.PSIsContainer) { $directories.Add($item.FullName.Substring($SourceRoot.Length + 1)) }
            else { $candidates.Add([pscustomobject]@{ source = $item.FullName; relative = $item.FullName.Substring($SourceRoot.Length + 1) }) }
        }
    }
    $backupRoot = Join-Path $SourceRoot 'backups'
    if (Test-Path -LiteralPath $backupRoot) {
        Assert-NoReparse $backupRoot
        foreach ($file in (Get-ChildItem -LiteralPath $backupRoot -File -Filter 'monitor-*.db' | Sort-Object Name -Descending | Select-Object -First 14)) {
            $candidates.Add([pscustomobject]@{ source = $file.FullName; relative = ('backups\' + $file.Name) })
        }
    }
    $bytes = 0L
    foreach ($file in $candidates) { Assert-NoReparse $file.source; $bytes += (Get-Item -LiteralPath $file.source).Length }
    if ($bytes -gt $MaxDataGB * 1GB) { throw 'Selected application data exceeds MaxDataGB; source is untouched. Review the data budget before staging.' }
    foreach ($relative in $directories) {
        $dest = Assert-ChildPath (Join-Path $Destination $relative) $Destination
        $null = New-Item -ItemType Directory -Force -Path $dest
        Protect-Path $dest
    }
    foreach ($file in $candidates) {
        $dest = Assert-ChildPath (Join-Path $Destination $file.relative) $Destination
        $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest)
        $before = (Get-FileHash -LiteralPath $file.source -Algorithm SHA256).Hash
        Copy-Item -LiteralPath $file.source -Destination $dest -Force
        if ((Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash -ne $before -or (Get-FileHash -LiteralPath $file.source -Algorithm SHA256).Hash -ne $before) { throw 'Application data changed during snapshot; prepare a new snapshot after quiescence.' }
        Protect-Path $dest
    }
    $snapshotCode = @'
import sys,os,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from sqlite_runtime import ensure_sqlite
sqlite = ensure_sqlite()
source = Path(sys.argv[2]) / 'monitor.db'
destination = Path(sys.argv[1]) / 'monitor.db'
temporary = destination.with_suffix('.snapshot.tmp')
live = sqlite.connect(source.as_uri() + '?mode=ro',uri=True,timeout=30)
target = sqlite.connect(temporary)
try:
    live.backup(target)
    # Source qpaths are absolute. Rewrite only this copied database to the
    # FINAL application root (never to a transient Stage directory). Copying
    # files also changes Windows file IDs, so rebind verified held copies.
    old_root = os.path.abspath(str(Path(sys.argv[2]) / 'quarantine'))
    final_root = os.path.abspath(str(Path(sys.argv[3]) / 'quarantine'))
    staged_root = os.path.abspath(str(Path(sys.argv[1]) / 'quarantine'))
    columns = {r[1] for r in target.execute('PRAGMA table_info(quarantine)')}
    if columns:
        rows = target.execute('SELECT id,qpath,status' + (',op_identity' if 'op_identity' in columns else '') + ' FROM quarantine').fetchall()
        for row in rows:
            qid,path,status = row[:3]
            if status in ('moving','restoring','purging'):
                raise RuntimeError('quarantine contains an unresolved operation; reconcile before migration')
            path = os.path.abspath(path)
            try:
                inside = os.path.normcase(os.path.commonpath((old_root,path))) == os.path.normcase(old_root) and os.path.normcase(path) != os.path.normcase(old_root)
            except ValueError:
                inside = False
            if not inside:
                if status in ('held','failed'):
                    raise RuntimeError('active quarantine record is outside the source quarantine root')
                continue
            relative = os.path.relpath(path,old_root)
            staged = os.path.join(staged_root,relative)
            final = os.path.join(final_root,relative)
            identity = None
            if status == 'held':
                if not os.path.exists(path) or not os.path.exists(staged):
                    raise RuntimeError('held quarantine content missing; reconcile before migration')
                original = os.stat(path,follow_symlinks=False)
                if 'op_identity' in columns and row[3] and row[3] != json.dumps([original.st_dev,original.st_ino]):
                    raise RuntimeError('source quarantine identity changed; migration refused')
                copied = os.stat(staged,follow_symlinks=False)
                identity = json.dumps([copied.st_dev,copied.st_ino])
            if 'op_identity' in columns and status == 'held':
                target.execute('UPDATE quarantine SET qpath=?,op_identity=? WHERE id=?',(final,identity,qid))
            else:
                target.execute('UPDATE quarantine SET qpath=? WHERE id=?',(final,qid))
        target.commit()
    if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise RuntimeError('snapshot integrity failure')
finally:
    target.close(); live.close()
os.replace(temporary,destination)
print('verified SQLite online backup completed')
'@
    Assert-NoReparse (Join-Path $SourceRoot 'monitor.db')
    $exe = Join-Path $Destination $pythonRelative
    Assert-EmbeddedRuntime $Destination
    & $exe -I -S -c $snapshotCode $Destination $SourceRoot $AppRoot | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'SQLite snapshot failed. No task action was changed.' }
    Protect-Path (Join-Path $Destination 'monitor.db')
    return $bytes
}

function Assert-Quiesced {
    foreach ($name in $taskNames) {
        $task = Get-ScheduledTask -TaskName $name -TaskPath '\'
        if ($task.Settings.Enabled -or $task.State -eq 'Running') { throw 'Before Apply/Rollback, the owner must stop and disable all three app tasks explicitly.' }
    }
    $roots = @($SourceRoot,$AppRoot)
    foreach ($process in (Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe','pythonw.exe','pwsh.exe','powershell.exe','wscript.exe') })) {
        if ($process.ProcessId -eq $PID) { continue }
        foreach ($root in $roots) {
            $pattern = [regex]::Escape($root).Replace('\\','[\\/]') + '[\\/](?:agent\.py|server\.py|collector\.py|widget\.ps1|launch_mini\.vbs)'
            if ([string]$process.CommandLine -match $pattern) { throw 'An application component is still running; no process is terminated by this installer.' }
        }
    }
}

function Set-ProtectedToken([string]$Directory) {
    $directoryPath = Assert-ChildPath $Directory $StagingRoot
    $tokenPath = Assert-ChildPath (Join-Path $directoryPath '.token') $directoryPath
    $bytes = [byte[]]::new(32)
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($bytes)
        $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
        [IO.File]::WriteAllText($tokenPath,$token,[Text.UTF8Encoding]::new($false))
        Protect-Path $tokenPath
    } finally {
        $random.Dispose()
        [Array]::Clear($bytes,0,$bytes.Length)
        $token = $null
    }
}

function Set-ProtectedLaunchMarker {
    $directory = Assert-ChildPath $AppRoot $SecureRoot
    Assert-NoReparse $directory
    $marker = Assert-ChildPath (Join-Path $directory '.protected-launch') $directory
    [IO.File]::WriteAllText($marker,'MonitorWidget protected launcher v1',[Text.UTF8Encoding]::new($false))
    Protect-Path $marker -CodeReadable # public non-secret marker; metadata stays private
}

function Set-ProtectedStartupShortcut {
    if (-not $startupShortcut) { throw 'The current owner Startup folder could not be resolved.' }
    Assert-NoReparse $startupShortcut
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($startupShortcut)
    $windows = [Environment]::GetFolderPath('Windows')
    $shortcut.TargetPath = Join-Path $windows 'System32\wscript.exe'
    $shortcut.Arguments = '"' + (Join-Path $AppRoot 'launch_mini.vbs') + '"'
    # The normal shell does not require access to the private app data folder.
    $shortcut.WorkingDirectory = Join-Path $windows 'System32'
    $shortcut.WindowStyle = 7
    $shortcut.Save()
}

function Restore-StartupShortcut([string]$Private,$Info) {
    if (-not $Info -or -not $Info.existed -or -not $Info.migrateRequested) { return }
    if (-not $startupShortcut -or $Info.path -ne $startupShortcut) { throw 'Startup shortcut restore path does not match the owner Startup folder.' }
    $backup = Assert-ChildPath (Join-Path $Private 'MiniWidget.original.lnk') $Private
    Assert-NoReparse $backup
    Assert-NoReparse $startupShortcut
    if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Info.originalSha256) { throw 'Startup shortcut backup hash mismatch.' }
    Copy-Item -LiteralPath $backup -Destination $startupShortcut -Force
    if ((Get-FileHash -LiteralPath $startupShortcut -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Info.originalSha256) { throw 'Startup shortcut rollback verification failed.' }
}

function Restore-PreparedActions([string]$Private,$ShortcutInfo) {
    $failures = [Collections.Generic.List[string]]::new()
    foreach ($name in $taskNames) {
        try {
            Write-TaskAction $name (Get-Content -LiteralPath (Join-Path $Private ($name + '.original.xml')) -Raw) -RestoreOriginal
        } catch { $failures.Add($name) }
    }
    try { Restore-StartupShortcut $Private $ShortcutInfo } catch { $failures.Add('MiniWidget shortcut') }
    if ($failures.Count) { throw ('Rollback requires owner review for: ' + ($failures -join ', ') + '. No task was started.') }
}

function Write-TaskAction([string]$Name,[string]$OriginalXml,[switch]$RestoreOriginal) {
    [xml]$xml = $OriginalXml
    $ns = [Xml.XmlNamespaceManager]::new($xml.NameTable)
    $ns.AddNamespace('t',$xml.DocumentElement.NamespaceURI)
    $enabled = $xml.SelectSingleNode('/t:Task/t:Settings/t:Enabled',$ns)
    if (-not $enabled) { $enabled = $xml.CreateElement('Enabled',$xml.DocumentElement.NamespaceURI); $null = $xml.SelectSingleNode('/t:Task/t:Settings',$ns).AppendChild($enabled) }
    $enabled.InnerText = 'false' # activation is a separate owner decision
    if (-not $RestoreOriginal -and $Name -eq 'MonitorWidget-Collector') {
        # Preserve the Limited principal and original action. This task is no
        # longer activated: the protected elevated Agent owns the collector job.
        $registration = $xml.SelectSingleNode('/t:Task/t:RegistrationInfo',$ns)
        if (-not $registration) { $registration = $xml.CreateElement('RegistrationInfo',$xml.DocumentElement.NamespaceURI); $null = $xml.DocumentElement.PrependChild($registration) }
        $description = $xml.SelectSingleNode('/t:Task/t:RegistrationInfo/t:Description',$ns)
        if (-not $description) { $description = $xml.CreateElement('Description',$xml.DocumentElement.NamespaceURI); $null = $registration.AppendChild($description) }
        $description.InnerText = 'Superseded: the protected MonitorWidget-Agent owns collection. Keep this Limited task disabled.'
    } elseif (-not $RestoreOriginal) {
        $actions = $xml.SelectSingleNode('/t:Task/t:Actions',$ns)
        foreach ($child in @($actions.ChildNodes)) { $null = $actions.RemoveChild($child) }
        $exec = $xml.CreateElement('Exec',$xml.DocumentElement.NamespaceURI)
        $command = $xml.CreateElement('Command',$xml.DocumentElement.NamespaceURI)
        $arguments = $xml.CreateElement('Arguments',$xml.DocumentElement.NamespaceURI)
        $working = $xml.CreateElement('WorkingDirectory',$xml.DocumentElement.NamespaceURI)
        if ($Name -eq 'MonitorWidget-Launch') {
            $command.InnerText = Join-Path ([Environment]::GetFolderPath('ProgramFiles')) 'PowerShell\7\pwsh.exe'
            $arguments.InnerText = '-NoProfile -STA -WindowStyle Hidden -File "' + (Join-Path $AppRoot 'widget.ps1') + '"'
        } else {
            $command.InnerText = Join-Path $AppRoot $pythonwRelative
            $entry = if ($Name -eq 'MonitorWidget-Agent') { 'agent.py' } else { 'collector.py' }
            $bootstrap = 'import sys,runpy;sys.path.insert(0,' + "r'$AppRoot'" + ');runpy.run_path(' + "r'$(Join-Path $AppRoot $entry)'" + ',run_name=''__main__'')'
            $arguments.InnerText = '-I -S -c "' + $bootstrap + '"'
        }
        $working.InnerText = $AppRoot
        foreach ($node in @($command,$arguments,$working)) { $null = $exec.AppendChild($node) }
        $null = $actions.AppendChild($exec)
    }
    $null = Register-ScheduledTask -TaskName $Name -TaskPath '\' -Xml $xml.OuterXml -Force
}

Assert-NoReparse $SourceRoot
Assert-NoReparse $SecureRoot
if ($SourceRoot.Equals($AppRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Source and protected destination must differ.' }
$inventory = Get-CodeInventory
if ($Mode -eq 'Plan' -or $WhatIfPreference) {
    $readiness = @()
    try {
        $readiness = @(Get-TaskSnapshots | ForEach-Object {
            [pscustomobject]@{ name = $_.name; originalRunLevel = $_.runLevel; proposedRunLevel = $_.runLevel; runLevelPreserved = $true; activation = $(if ($_.name -eq 'MonitorWidget-Collector') { 'disabled; superseded by Agent-owned collection' } else { 'disabled until owner reviews and activates' }) }
        })
    } catch { $readiness = @([pscustomobject]@{ ready = $false; reason = 'Task inspection unavailable; elevated-principal compatibility must be reviewed before Apply.' }) }
    [pscustomobject]@{
        mode = 'Plan'; source = $SourceRoot; destination = $AppRoot
        taskNames = $taskNames; python = (Join-Path $AppRoot $pythonwRelative)
        taskReadiness = $readiness
        startupShortcut = [pscustomobject]@{ path = $startupShortcut; exists = [bool]($startupShortcut -and (Test-Path -LiteralPath $startupShortcut)); applyBlockedWithoutExplicitMigration = [bool]($startupShortcut -and (Test-Path -LiteralPath $startupShortcut)); migrateRequested = [bool]$MigrateStartupShortcut }
        protectedToken = 'Apply requires -RotateProtectedToken; new protected-only token, source token untouched, no token output.'
        code = @($inventory | Select-Object relative,sha256)
        data = 'SQLite online backup; existing token/settings; logs; quarantine; shots/auto; latest 14 monitor DB backups. No cleanup_apps or old source-code backups.'
        activation = 'No task/process changes by Plan/Stage. Apply requires explicit quiescence and leaves all replacement tasks disabled.'
        rollback = 'Restores original task XML with Enabled=false. Does not overwrite source data or delete either app tree.'
    } | ConvertTo-Json -Depth 5
    return
}
Assert-Admin
if ($Mode -eq 'Stage') {
    if (-not $PSCmdlet.ShouldProcess($SecureRoot,'Create protected code/data snapshot; no task changes')) { return }
    $null = New-Item -ItemType Directory -Force -Path $SecureRoot,$StagingRoot
    Protect-Path $SecureRoot -CodeReadable
    Protect-Path $StagingRoot
    $StagePath = Join-Path $StagingRoot ('app-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $StagePath
    Protect-Path $StagePath # new DB/WAL/SHM inherit admin-only ACL
    Copy-VerifiedCode $inventory $StagePath
    $private = Join-Path $StagePath '.deployment'
    $null = New-Item -ItemType Directory -Path $private
    Protect-Path $private
    $snapshots = Get-TaskSnapshots
    foreach ($task in $snapshots) {
        $task.xml | Set-Content -LiteralPath (Join-Path $private ($task.name + '.original.xml')) -Encoding Unicode
    }
    $bytes = Copy-DataSnapshot $StagePath
    $manifest = [ordered]@{ source = $SourceRoot; destination = $AppRoot; createdUtc = [DateTime]::UtcNow.ToString('o'); code = @($inventory | Select-Object relative,sha256); tasks = @($snapshots | Select-Object name,enabled,state,runLevel); copiedDataBytes = $bytes }
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $private 'manifest.json') -Encoding UTF8
    [pscustomobject]@{ mode = 'Stage'; stage = $StagePath; tasksChanged = $false; sourceRetained = $true; snapshotIntegrity = 'ok'; activation = 'pending explicit owner approval and quiescence' } | ConvertTo-Json
    return
}
if ($Mode -eq 'Apply') {
    if (-not $RotateProtectedToken) { throw 'Apply requires explicit -RotateProtectedToken so the old readable source token cannot authenticate to the protected app.' }
    if (-not $StagePath) { throw 'Apply requires the exact StagePath returned by Stage.' }
    $shortcutExists = [bool]($startupShortcut -and (Test-Path -LiteralPath $startupShortcut))
    if ($shortcutExists -and -not $MigrateStartupShortcut) { throw 'Legacy MiniWidget.lnk exists. Apply requires explicit -MigrateStartupShortcut with private backup and rollback.' }
    $StagePath = Assert-ChildPath $StagePath $StagingRoot
    Assert-NoReparse $StagePath
    if (Test-Path -LiteralPath $AppRoot) { throw 'Protected app already exists. Review the previous deployment before replacing it.' }
    $private = Join-Path $StagePath '.deployment'
    $manifest = Get-Content -LiteralPath (Join-Path $private 'manifest.json') -Raw | ConvertFrom-Json
    if ($manifest.source -ne $SourceRoot -or $manifest.destination -ne $AppRoot) { throw 'Stage manifest paths do not match this migration.' }
    foreach ($file in $manifest.code) {
        $staged = Assert-ChildPath (Join-Path $StagePath $file.relative) $StagePath
        Assert-NoReparse $staged
        if ((Get-FileHash -LiteralPath $staged -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) { throw 'Prepared release hash changed; Apply refused.' }
        if ((Get-FileHash -LiteralPath (Join-Path $SourceRoot $file.relative) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $file.sha256) { throw 'Source release changed after Stage; prepare a new reviewed snapshot.' }
    }
    Assert-Quiesced
    if (-not $PSCmdlet.ShouldProcess($AppRoot,'Finalize snapshot and replace actions of three disabled tasks; no start')) { return }
    # Build a fresh final tree: no stale quarantine/log files survive from the
    # preliminary live snapshot, and the earlier Stage is kept for review.
    $finalStage = Assert-ChildPath (Join-Path $StagingRoot ('final-' + [guid]::NewGuid().ToString('N'))) $StagingRoot
    $null = New-Item -ItemType Directory -Path $finalStage
    Protect-Path $finalStage
    $stagedInventory = @($manifest.code | ForEach-Object { [pscustomobject]@{ relative = $_.relative; source = (Assert-ChildPath (Join-Path $StagePath $_.relative) $StagePath); sha256 = $_.sha256 } })
    Copy-VerifiedCode $stagedInventory $finalStage
    $finalPrivate = Join-Path $finalStage '.deployment'
    $null = New-Item -ItemType Directory -Path $finalPrivate
    Protect-Path $finalPrivate
    foreach ($name in $taskNames) {
        Copy-Item -LiteralPath (Join-Path $private ($name + '.original.xml')) -Destination (Join-Path $finalPrivate ($name + '.original.xml'))
    }
    $null = Copy-DataSnapshot $finalStage # final consistent snapshot after owner stopped source
    $shortcutInfo = [pscustomobject]@{ path = $startupShortcut; existed = $shortcutExists; migrateRequested = [bool]$MigrateStartupShortcut; originalSha256 = $null }
    if ($shortcutExists -and $MigrateStartupShortcut) {
        Assert-NoReparse $startupShortcut
        $shortcutBackup = Join-Path $finalPrivate 'MiniWidget.original.lnk'
        Copy-Item -LiteralPath $startupShortcut -Destination $shortcutBackup
        Protect-Path $shortcutBackup
        $shortcutInfo.originalSha256 = (Get-FileHash -LiteralPath $shortcutBackup -Algorithm SHA256).Hash.ToLowerInvariant()
        if ((Get-FileHash -LiteralPath $startupShortcut -Algorithm SHA256).Hash.ToLowerInvariant() -ne $shortcutInfo.originalSha256) { throw 'Startup shortcut changed while backing it up; Apply refused.' }
    }
    Set-ProtectedToken $finalStage # after snapshot; never touches or prints source token
    $manifest | Add-Member -NotePropertyName protectedTokenRotated -NotePropertyValue $true -Force
    $manifest | Add-Member -NotePropertyName startupShortcut -NotePropertyValue $shortcutInfo -Force
    $manifest | Add-Member -NotePropertyName appliedDbSha256 -NotePropertyValue ((Get-FileHash -LiteralPath (Join-Path $finalStage 'monitor.db') -Algorithm SHA256).Hash.ToLowerInvariant()) -Force
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $finalPrivate 'manifest.json') -Encoding UTF8
    $resolvedFinal = Assert-ChildPath $finalStage $StagingRoot
    $resolvedApp = Assert-ChildPath $AppRoot $SecureRoot
    Move-Item -LiteralPath $resolvedFinal -Destination $resolvedApp
    try {
        Set-ProtectedLaunchMarker # Apply only; normal launcher can read this without private deployment metadata
        foreach ($name in $taskNames) {
            $original = Get-Content -LiteralPath (Join-Path $AppRoot ('.deployment\' + $name + '.original.xml')) -Raw
            Write-TaskAction $name $original
        }
        if ($shortcutExists -and $MigrateStartupShortcut) {
            if ((Get-FileHash -LiteralPath $startupShortcut -Algorithm SHA256).Hash.ToLowerInvariant() -ne $shortcutInfo.originalSha256) { throw 'Startup shortcut changed before migration.' }
            Set-ProtectedStartupShortcut
        }
    } catch {
        Restore-PreparedActions (Join-Path $AppRoot '.deployment') $shortcutInfo
        throw 'Task/shortcut update failed; original actions restored disabled and original shortcut restored. Source and protected data retained for review.'
    }
    [pscustomobject]@{ mode = 'Apply'; application = $AppRoot; tasksDisabled = $true; processesStarted = $false; next = 'Owner reviews task actions/ACL, then explicitly enables Agent and Launch only. Collector stays disabled; Agent owns collection.' } | ConvertTo-Json
    return
}
if ($Mode -eq 'Rollback') {
    Assert-Quiesced
    $private = Join-Path $AppRoot '.deployment'
    $manifest = Get-Content -LiteralPath (Join-Path $private 'manifest.json') -Raw | ConvertFrom-Json
    $db = Join-Path $AppRoot 'monitor.db'
    if ((Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.appliedDbSha256 -or ((Test-Path -LiteralPath ($db + '-wal')) -and (Get-Item -LiteralPath ($db + '-wal')).Length -gt 0)) { throw 'Protected data changed after Apply. Review data reconciliation before restoring source actions.' }
    if (-not $PSCmdlet.ShouldProcess('MonitorWidget tasks','Restore original actions disabled; keep both data/code trees')) { return }
    Restore-PreparedActions $private $manifest.startupShortcut
    [pscustomobject]@{ mode = 'Rollback'; originalActionsRestored = $true; tasksDisabled = $true; sourceAndProtectedDataRetained = $true } | ConvertTo-Json
}
