# Constructs isolated controls only. Does not source/run the widget, show windows or call its API.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework
$tokens = $null; $errors = $null
$path = Join-Path (Split-Path $PSScriptRoot -Parent) 'widget.ps1'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | ForEach-Object Message | Out-String) }
$fn = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'New-ActionButton' }, $true)
if (-not $fn) { throw 'Accessible control factory missing' }
. ([scriptblock]::Create($fn.Extent.Text))
$button = New-ActionButton 'اختبار زر'
if (-not $button.ApplyTemplate()) { throw 'Button template did not apply' }
$peer = [Windows.Automation.Peers.ButtonAutomationPeer]::new($button)
if ($peer.GetName() -ne 'اختبار زر') { throw 'Automation name missing' }
if ($null -eq $peer.GetPattern([Windows.Automation.Peers.PatternInterface]::Invoke)) { throw 'Invoke pattern missing' }
if (-not ($button.Focusable -and $button.IsTabStop)) { throw 'Button cannot participate in keyboard navigation' }
$toggle = New-ActionButton 'اختبار مفتاح' $true
$null = $toggle.ApplyTemplate()
$peer = [Windows.Automation.Peers.ToggleButtonAutomationPeer]::new($toggle)
if ($null -eq $peer.GetPattern([Windows.Automation.Peers.PatternInterface]::Toggle)) { throw 'Toggle pattern missing' }
$toggle.IsChecked = $true
if (-not $toggle.IsChecked) { throw 'Toggle state missing' }
Write-Output 'PASS PowerShell syntax; native Button/ToggleButton templates, names, Invoke/Toggle patterns and keyboard participation'
$compactFn = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Update-Compact' }, $true)
. ([scriptblock]::Create($compactFn.Extent.Text))
function Brush([string]$hex) { [Windows.Media.BrushConverter]::new().ConvertFromString($hex) }
$C = @{ muted = '#556677'; warn = '#B96A00' }
$UI = @{}
foreach ($name in 'compactcpu','compactram','compactdisk','compactAlert','sub','pillText') { $UI[$name] = New-Object Windows.Controls.TextBlock }
$UI.pill = New-Object Windows.Controls.Border
$script:sync = @{ lastAt = Get-Date; err = $null }
$script:S = @{ muteAlerts = $false }
$script:THR = @{ dB = 20; ramB = 92 }
$script:alertSeen = @{}; $script:alertSeeded = $false; $script:lastCompactAlert = ''
$data = @{ now = (Get-Date).ToString('s'); m = @{ ramPct = 68; diskFreeGB = 99 }; cpuNow = @{ pct = 24; age = 5 } }
Update-Compact $data
if ($UI.compactcpu.Text -notmatch '24' -or $UI.compactram.Text -notmatch '68' -or $UI.compactdisk.Text -notmatch '99') { throw 'Compact metrics do not reflect fixture values' }
$script:sync.lastAt = (Get-Date).AddSeconds(-45)
Update-Compact $data
if ($UI.sub.Text -notmatch 'قديمة|منقطع') { throw 'Stale connection is not explicit' }
$script:sync.lastAt = Get-Date; $data.cpuNow.age = 999
Update-Compact $data
if ($UI.compactcpu.Text -ne '—') { throw 'Stale CPU still presented as a current measurement' }
$data.cpuNow.age = 5; $data.now = (Get-Date).AddMinutes(-5).ToString('s')
Update-Compact $data
if ($UI.compactcpu.Text -ne '—' -or $UI.sub.Text -notmatch 'قديمة|منقطع') { throw 'Fresh receipt of an old source snapshot presented as live' }
$data.now = $null
Update-Compact $data
if ($UI.compactcpu.Text -ne '—') { throw 'Missing source timestamp inferred to be a current CPU measurement' }
Write-Output 'PASS compact metric binding, disconnected freshness and stale CPU handling (synthetic controls and data only)'
$notificationFn = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Notify-Important' }, $true)
if (-not $notificationFn) { throw 'Native important notification helper missing' }
. ([scriptblock]::Create($notificationFn.Extent.Text))
$script:notifications = @()
$script:tray = [pscustomobject]@{ Visible = $true; BalloonTipTitle = ''; BalloonTipText = ''; BalloonTipIcon = '' }
$script:tray | Add-Member -MemberType ScriptMethod -Name ShowBalloonTip -Value { param($timeout) $script:notifications += $this.BalloonTipText }
$script:sync.lastAt = Get-Date; $data.now = (Get-Date).ToString('s'); $data.cpuNow.age = 1
$data.m.ramPct = 40; $data.m.diskFreeGB = 99
Update-Compact $data
$data.m.diskFreeGB = 5
Update-Compact $data
Update-Compact $data
if ($script:notifications.Count -ne 1) { throw 'Important native notification missing or repeated' }
$script:S.muteAlerts = $true; $data.m.diskFreeGB = 99; $data.m.ramPct = 99
Update-Compact $data
if ($script:notifications.Count -ne 1) { throw 'Muted notification was delivered' }
Write-Output 'PASS native notification delegation, important-state deduplication and mute (notification object is a test double; no OS notification sent)'
