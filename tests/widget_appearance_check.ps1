$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
$tokens=$null;$errors=$null
$path=Join-Path (Split-Path $PSScriptRoot -Parent) 'widget.ps1'
$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors.Message -join '; ') }
. (Join-Path $PSScriptRoot 'widget_test_identity.ps1')   # الهوية المشتركة من جذر المستودع قبل تحميل الدوال المستخرجة
foreach ($name in @('Test-AppearanceRecord','Get-Palette','Alpha','Brush','Remap-Tree','New-WidgetMenuStyles','Set-WidgetMenuItems','Set-WidgetMenuTheme','Set-WidgetControlTheme','Apply-Theme','Set-Theme','Receive-Appearance','Request-Appearance','Save-Settings','New-Text','New-Ring','Set-Ring','Level-Brush')) {
    $fn=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}.GetNewClosure(),$true)
    if (-not $fn) { throw "Missing $name" }; . ([scriptblock]::Create((Resolve-WidgetFunctionText $fn.Extent.Text)))
}
function Log($m) { throw $m }
function Show-Toast($m) { $script:lastToast=$m }
function Get-SysLight { return $script:systemLight }
function Set-CardOpacity($v) {}
$script:systemLight=$false;$script:S=@{theme='dark';palette='teal';opacity=.98};$script:brushCache=@{}
$script:isLight=$false;$script:C=Get-Palette $false
$outer=New-Object Windows.Controls.StackPanel
$identity=New-Object Windows.Controls.TextBlock;$identity.Foreground=Brush $C.accent
$success=New-Object Windows.Controls.TextBlock;$success.Foreground=Brush $C.ok
[void]$outer.Children.Add($identity);[void]$outer.Children.Add($success)
$menu=New-Object Windows.Controls.ContextMenu;$script:appearanceMenuItems=@();$script:appearanceRevision=1
$successBefore=$success.Foreground.Color.ToString();$identityBefore=$identity.Foreground.Color.ToString()
$script:S.palette='blue';$blueAccent=(Brush (Get-Palette $false).accent).Color.ToString();$script:S.palette='teal'   # لون الهوية الأزرق يُشتقّ من tokens.css لا من قيمة ثابتة
Receive-Appearance ([pscustomobject]@{schemaVersion=1;revision=2;mode='dark';palette='blue'})
if ($script:S.palette -ne 'blue' -or $script:appearanceRevision -ne 2) { throw 'Canonical appearance not applied' }
if ($identity.Foreground.Color.ToString() -ne $blueAccent -or $blueAccent -eq $identityBefore) { throw 'Identity did not update to blue' }
if ($success.Foreground.Color.ToString() -ne $successBefore) { throw 'Success meaning changed with identity' }
Receive-Appearance ([pscustomobject]@{schemaVersion=1;revision=1;mode='light';palette='violet'})
if ($script:S.palette -ne 'blue') { throw 'Older revision overwrote confirmed appearance' }
Receive-Appearance ([pscustomobject]@{schemaVersion=1;revision=3;mode='invalid';palette='teal'})
if ($script:appearanceRevision -ne 2) { throw 'Invalid mode accepted' }
Receive-Appearance ([pscustomobject]@{schemaVersion=1;revision=3;mode='auto';palette='graphite'})
$script:systemLight=$true;Set-Theme 'auto'
if (-not $script:isLight -or $script:S.theme -ne 'auto') { throw 'Auto mode did not follow system' }
Set-Theme 'dark';$script:systemLight=$true
if ($script:isLight) { throw 'Explicit dark mode followed system' }
foreach ($light in @($false,$true)) { foreach ($palette in @('teal','blue','violet','graphite')) { $script:S.palette=$palette;$p=Get-Palette $light;foreach ($key in $p.Keys) { if ($key -ne 'base') { $null=[Windows.Media.ColorConverter]::ConvertFromString($p[$key]) } };if ($p.accent -eq $p.ok) { throw 'Identity and success brush alias' } } }
Write-Output 'PASS all eight WPF palettes, native brush remapping, independent success state, stale revision, invalid mode and system/manual modes'
$valid=[pscustomobject]@{schemaVersion=1;revision=0;mode='auto';palette='teal'}
if (-not (Test-AppearanceRecord $valid)) { throw 'Revision zero is invalid' }
foreach ($badRevision in @($true,$false,-1,'1',1.5,$null)) {
    $candidate=[pscustomobject]@{schemaVersion=1;revision=$badRevision;mode='auto';palette='teal'}
    if (Test-AppearanceRecord $candidate) { throw 'Invalid revision accepted by common startup/live predicate' }
    $beforeRevision=$script:appearanceRevision;Receive-Appearance $candidate
    if ($script:appearanceRevision -ne $beforeRevision) { throw 'Invalid revision applied' }
}
if ($ast.Extent.Text -notmatch 'if \(Test-AppearanceRecord \$initialAppearance\)') { throw 'Startup bypasses canonical appearance predicate' }
$r=New-Ring 'اختبار مؤشر';Set-Ring $r 73 'ok' '73' '%' 'اختبار'
if ($r.Grid.Width -ne 68 -or $r.Grid.Height -ne 68 -or -not $r.Grid.LayoutTransform.Value.IsIdentity -or $r.Unit.FontSize -lt 11 -or $r.Val.FontSize -lt 11) { throw 'Compact ring reduces text below readable size' }
if ($r.Arc.Data.Bounds.Left -lt 0 -or $r.Arc.Data.Bounds.Right -gt 68 -or $r.Arc.Data.Bounds.Top -lt 0 -or $r.Arc.Data.Bounds.Bottom -gt 68) { throw 'Ring geometry exceeds its compact container' }
Write-Output 'PASS common strict startup/live revision validation and 68 DIP reference gauge with unscaled readable text'
$script:appearancePending=$false;$script:S.theme='dark';$script:S.palette='blue';$script:appearanceRevision=4
function Start-Bg($path,$body,$ok,$err) { $script:posted=@{path=$path;body=$body}; & $err 'synthetic failure' }
Request-Appearance 'light' 'violet'
if ($script:posted.path -ne '/api/appearance' -or $script:posted.body.revision -ne 4 -or $script:posted.body.palette -ne 'violet') { throw 'Appearance request contract mismatch' }
if ($script:S.palette -ne 'blue' -or $script:appearancePending -or $script:lastToast -notmatch 'تعذّر') { throw 'Failed save was optimistically applied' }
Write-Output 'PASS asynchronous canonical request revision and failed-save behavior (mocked transport; no network)'
$tmp=Join-Path ([IO.Path]::GetTempPath()) ('mw-widget-appearance-test-'+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($tmp)
try {
    $script:setPath=Join-Path $tmp 'widget.settings.json'
    [IO.File]::WriteAllText($script:setPath,'{"theme":"light","unknown":"preserve","top":10}')
    $script:S=@{theme='dark';palette='violet';top=33;expanded=$true};Save-Settings
    $saved=Get-Content -LiteralPath $script:setPath -Raw | ConvertFrom-Json
    if ($saved.theme -ne 'light' -or $saved.unknown -ne 'preserve' -or $saved.top -ne 33 -or $null -ne $saved.palette) { throw 'Local preferences overwrote central/legacy appearance or unrelated settings' }
} finally { $resolved=[IO.Path]::GetFullPath($tmp); $parent=[IO.Path]::GetFullPath([IO.Path]::GetTempPath()); if (-not $resolved.StartsWith($parent,[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($resolved) -notmatch '^mw-widget-appearance-test-[a-f0-9]{32}$') { throw 'Unsafe temporary cleanup path' }; Remove-Item -LiteralPath $resolved -Recurse -Force }
Write-Output 'PASS local geometry save preserves legacy appearance and unrelated settings (temporary file only)'
