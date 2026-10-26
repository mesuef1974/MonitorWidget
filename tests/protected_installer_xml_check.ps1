# Pure XML/action tests. Register-ScheduledTask is a local test double;
# no real Task Scheduler operation is invoked.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'install_protected_app_20261025.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Installer parser failure.' }
$definition = $ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Write-TaskAction'},$true)
if (-not $definition) { throw 'Task action function not found.' }
. ([scriptblock]::Create($definition.Extent.Text))
$AppRoot = 'C:\ProgramData\MonitorWidget\app'
$pythonwRelative = 'runtime\python\python314\pythonw.exe'
$script:Captured = @{}
function Register-ScheduledTask {
    param([string]$TaskName,[string]$TaskPath,[string]$Xml,[switch]$Force)
    $script:Captured[$TaskName] = $Xml
}
function Assert-True([bool]$Value,[string]$Message) { if (-not $Value) { throw $Message } }
function Get-Fixture([string]$RunLevel) {
    return @"
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><RegistrationInfo><Description>original</Description></RegistrationInfo><Triggers><LogonTrigger><Enabled>true</Enabled><UserId>TEST-OWNER</UserId></LogonTrigger></Triggers><Principals><Principal id="Author"><UserId>TEST-OWNER</UserId><LogonType>InteractiveToken</LogonType><RunLevel>$RunLevel</RunLevel></Principal></Principals><Settings><Enabled>true</Enabled><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy></Settings><Actions Context="Author"><Exec><Command>C:\old\pythonw.exe</Command><Arguments>old-script.py</Arguments><WorkingDirectory>C:\old</WorkingDirectory></Exec></Actions></Task>
"@
}
$count = 0
foreach ($name in @('MonitorWidget-Agent','MonitorWidget-Collector','MonitorWidget-Launch')) {
    $level = if ($name -eq 'MonitorWidget-Collector') { 'LeastPrivilege' } else { 'HighestAvailable' }
    $fixture = Get-Fixture $level
    [xml]$before = $fixture
    Write-TaskAction $name $fixture
    [xml]$after = $script:Captured[$name]
    Assert-True ($after.Task.Principals.OuterXml -eq $before.Task.Principals.OuterXml) 'Principal/run level changed.'
    Assert-True ($after.Task.Triggers.OuterXml -eq $before.Task.Triggers.OuterXml) 'Triggers changed.'
    Assert-True ([string]$after.Task.Settings.Enabled -eq 'false') 'Replacement task not disabled.'
    if ($name -eq 'MonitorWidget-Collector') {
        Assert-True ($after.Task.Actions.OuterXml -eq $before.Task.Actions.OuterXml) 'Limited collector action changed unexpectedly.'
        Assert-True ([string]$after.Task.RegistrationInfo.Description -match 'Superseded') 'Collector not marked superseded.'
    } elseif ($name -eq 'MonitorWidget-Agent') {
        Assert-True ([string]$after.Task.Actions.Exec.Command -eq (Join-Path $AppRoot $pythonwRelative)) 'Agent does not use private embedded Python.'
        Assert-True ([string]$after.Task.Actions.Exec.Arguments -match '^-I -S -c ') 'Agent bootstrap is not isolated.'
        Assert-True ([string]$after.Task.Actions.Exec.Arguments -match 'runpy.run_path') 'Agent bootstrap not explicit.'
    } else {
        Assert-True ([string]$after.Task.Actions.Exec.Arguments -match [regex]::Escape((Join-Path $AppRoot 'widget.ps1'))) 'Widget path not protected.'
    }
    Write-TaskAction $name $fixture -RestoreOriginal
    [xml]$restored = $script:Captured[$name]
    Assert-True ($restored.Task.Actions.OuterXml -eq $before.Task.Actions.OuterXml) 'Rollback failed to restore original actions.'
    Assert-True ($restored.Task.Principals.OuterXml -eq $before.Task.Principals.OuterXml) 'Rollback principal changed.'
    Assert-True ([string]$restored.Task.Settings.Enabled -eq 'false') 'Rollback must remain disabled for owner review.'
    $count++
}
foreach ($functionName in @('Assert-ChildPath','Assert-NoReparse','Set-ProtectedToken','Set-ProtectedLaunchMarker','Set-ProtectedStartupShortcut','Restore-StartupShortcut','Restore-PreparedActions')) {
    $nameToFind = $functionName
    $definition = $ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $nameToFind},$true)
    . ([scriptblock]::Create($definition.Extent.Text))
}
function Protect-Path { param([string]$Path,[switch]$CodeReadable) } # ACL is tested separately; no production ACL changes.
$testRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ('install-helper-' + [guid]::NewGuid().ToString('N'))))
$testParent = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
Assert-True ($testRoot.StartsWith($testParent + '\',[StringComparison]::OrdinalIgnoreCase)) 'Test directory escaped workspace.'
$null = New-Item -ItemType Directory -Path $testRoot
try {
    $StagingRoot = Join-Path $testRoot 'staging'
    $final = Join-Path $StagingRoot 'final'
    $private = Join-Path $final '.deployment'
    $null = New-Item -ItemType Directory -Force -Path $private
    $sourceToken = Join-Path $testRoot 'source.token'
    $protectedToken = Join-Path $final '.token'
    [IO.File]::WriteAllText($sourceToken,'synthetic-original-token')
    Copy-Item -LiteralPath $sourceToken -Destination $protectedToken
    $emitted = @(Set-ProtectedToken $final)
    Assert-True ($emitted.Count -eq 0) 'Token rotation emitted output.'
    Assert-True ([IO.File]::ReadAllText($sourceToken) -eq 'synthetic-original-token') 'Source token changed.'
    Assert-True ([IO.File]::ReadAllText($protectedToken) -match '^[A-Za-z0-9_-]{43}$') 'Protected token not generated securely.'
    $count++
    $AppRoot = $final
    $SecureRoot = $testRoot
    Set-ProtectedLaunchMarker
    Assert-True ([IO.File]::ReadAllText((Join-Path $AppRoot '.protected-launch')) -eq 'MonitorWidget protected launcher v1') 'Public launch marker not created correctly.'
    $count++
    $startupShortcut = Join-Path $testRoot 'MiniWidget.lnk'
    [IO.File]::WriteAllText($startupShortcut,'synthetic-original-shortcut-bytes')
    $backup = Join-Path $private 'MiniWidget.original.lnk'
    Copy-Item -LiteralPath $startupShortcut -Destination $backup
    $info = [pscustomobject]@{ path=$startupShortcut; existed=$true; migrateRequested=$true; originalSha256=(Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash.ToLowerInvariant() }
    $script:fakeShortcut = [pscustomobject]@{ TargetPath=''; Arguments=''; WorkingDirectory=''; WindowStyle=0 }
    $script:fakeShortcut | Add-Member -MemberType ScriptMethod -Name Save -Value { [IO.File]::WriteAllText($script:startupShortcut,'synthetic-retargeted-shortcut') }
    $script:fakeShell = [pscustomobject]@{}
    $script:fakeShell | Add-Member -MemberType ScriptMethod -Name CreateShortcut -Value { param($path) return $script:fakeShortcut }
    function New-Object { param([string]$ComObject) if ($ComObject -ne 'WScript.Shell') { throw 'Unexpected COM request.' }; return $script:fakeShell }
    Set-ProtectedStartupShortcut
    Assert-True ($script:fakeShortcut.Arguments -eq ('"' + (Join-Path $AppRoot 'launch_mini.vbs') + '"')) 'Shortcut did not target protected launcher.'
    Assert-True ($script:fakeShortcut.TargetPath.EndsWith('System32\wscript.exe',[StringComparison]::OrdinalIgnoreCase)) 'Shortcut host not wscript.'
    Assert-True ($script:fakeShortcut.WindowStyle -eq 7) 'Shortcut window behavior changed.'
    Restore-StartupShortcut $private $info
    Assert-True ([IO.File]::ReadAllText($startupShortcut) -eq 'synthetic-original-shortcut-bytes') 'Exact shortcut rollback failed.'
    $count++
    foreach ($name in @('MonitorWidget-Agent','MonitorWidget-Collector','MonitorWidget-Launch')) { (Get-Fixture 'HighestAvailable') | Set-Content -LiteralPath (Join-Path $private ($name + '.original.xml')) }
    $taskNames = @('MonitorWidget-Agent','MonitorWidget-Collector','MonitorWidget-Launch')
    $script:restoreCalls = [Collections.Generic.List[string]]::new()
    $script:shortcutRestoreCalls = 0
    function Write-TaskAction { param($Name,$OriginalXml,[switch]$RestoreOriginal) $script:restoreCalls.Add($Name); if ($Name -eq 'MonitorWidget-Collector') { throw 'synthetic registration failure' } }
    function Restore-StartupShortcut { param($Private,$Info) $script:shortcutRestoreCalls++ }
    $failed = $false
    try { Restore-PreparedActions $private $info } catch { $failed = $true }
    Assert-True $failed 'Partial rollback failure not surfaced.'
    Assert-True ($script:restoreCalls.Count -eq 3) 'Rollback did not attempt every task after one failed.'
    Assert-True ($script:shortcutRestoreCalls -eq 1) 'Shortcut rollback skipped after task failure.'
    $count++
} finally {
    # Only remove the disposable, explicitly validated test tree above.
    if ($testRoot.StartsWith($testParent + '\',[StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $testRoot)) {
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
}
[pscustomobject]@{ tests = $count; status = 'PASS'; taskSchedulerTouched = $false; scope = 'isolated XML, private token fixtures, shortcut COM double, and rollback failure injection' } | ConvertTo-Json
