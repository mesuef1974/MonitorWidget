param(
    [Parameter(Mandatory)][string]$PreviewData,
    [ValidateSet('light','dark')][string]$Theme = 'light',
    [ValidateSet('teal','blue','violet','graphite')][string]$Palette = 'teal',
    [switch]$RecoveryRequired,
    [string]$Feedback = '',
    [ValidateRange(-1,1)][double]$BackgroundOpacity = -1,
    [ValidateSet('snapshot','fresh','missing')][string]$DataState = 'snapshot',
    [ValidateSet('snapshot','admin','standard')][string]$AdminState = 'snapshot',
    [switch]$Expanded,
    [switch]$ValidateOnly,
    [string]$RenderOutput,
    [ValidateRange(0,4000)][int]$AvailableHeight = 0
    ,[int]$DetailPage = -1
    ,[switch]$AuditAllPages
    ,[switch]$ConfirmPreview
)
# Uses the actual widget source and WPF controls, with all external effects disabled.
# Supply a read-only /api/mini snapshot. This fixture is explicitly labelled; it is not production telemetry.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$source = [IO.File]::ReadAllText((Join-Path $root 'widget.ps1'), [Text.Encoding]::UTF8)
$sourceSha256 = (Get-FileHash -LiteralPath (Join-Path $root 'widget.ps1') -Algorithm SHA256).Hash.ToLowerInvariant()
$payload = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $PreviewData).Path, [Text.Encoding]::UTF8) | ConvertFrom-Json
if (-not $payload.m) { throw 'PreviewData must have the /api/mini payload shape including m' }
if ($DataState -eq 'fresh' -or $DataState -eq 'missing') { $payload.now=(Get-Date).ToString('s');$payload.cpuNow=[pscustomobject]@{pct=24;age=1} }
if ($DataState -eq 'missing') { $payload.cpuNow=$null;$payload.m.ramPct=$null;$payload.m.diskFreeGB=$null }
if ($AdminState -ne 'snapshot') { $payload.admin=($AdminState -eq 'admin') }
$json = ($payload | ConvertTo-Json -Depth 30 -Compress).Replace("'", "''")
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Widget source contains syntax errors' }
$replacementFunctions = @{
    Log = 'function Log($m) { if ($m -like ''fatal:*'') { throw $m }; Write-Verbose $m }'
    'Save-Settings' = 'function Save-Settings { }'
    'Start-Bg' = 'function Start-Bg([string]$path, $body, [scriptblock]$ok, [scriptblock]$err) { & $err ''معاينة معزولة: العمليات الخارجية معطلة'' }'
}
$functions = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $replacementFunctions.ContainsKey($node.Name) }, $true) | Sort-Object { $_.Extent.StartOffset } -Descending
if (@($functions).Count -ne 3) { throw 'Fixture guard functions could not be located' }
foreach ($fn in $functions) { $source = $source.Remove($fn.Extent.StartOffset,$fn.Extent.EndOffset-$fn.Extent.StartOffset).Insert($fn.Extent.StartOffset,$replacementFunctions[$fn.Name]) }
function Replace-Required([string]$old,[string]$new) {
    if (-not $script:source.Contains($old)) { throw ('Fixture guard marker changed: ' + $old.Substring(0,[Math]::Min(70,$old.Length))) }
    $script:source = $script:source.Replace($old,$new)
}
Replace-Required "'Local\Azkia-MiniWidget'" '"Local\Azkia-MiniWidget-VisualFixture-$PID"'
Replace-Required 'if ((Test-Path "$Root\widget.log")' 'if ($false -and (Test-Path "$Root\widget.log")'
Replace-Required '$script:setPath = "$Root\widget.settings.json"' '$script:setPath = Join-Path $env:TEMP ("mw-fixture-no-settings-" + $PID + ".json")'
Replace-Required "(Join-Path `$PSScriptRoot 'assets\icon_192.png')" "(Join-Path `$Root 'assets\icon_192.png')"
Replace-Required "(Join-Path `$PSScriptRoot 'identity.ps1')" "(Join-Path `$Root 'identity.ps1')"   # الهوية المشتركة تُقرأ من جذر المستودع لا من موضع السكربت المولَّد
Replace-Required "Get-IdentityPalette `$PSScriptRoot " "Get-IdentityPalette `$Root "
Replace-Required "theme = 'auto'; palette = 'teal'; restorePos = `$true; expanded = `$false; showChart = `$false" ("theme = '" + $Theme + "'; palette = '" + $Palette + "'; restorePos = `$true; expanded = `$" + $Expanded.IsPresent.ToString().ToLowerInvariant() + '; showChart = $true')
if ($BackgroundOpacity -ge 0) { Replace-Required 'opacity = 0.96;' ('opacity = ' + $BackgroundOpacity.ToString([Globalization.CultureInfo]::InvariantCulture) + ';') }
Replace-Required "(Join-Path `$Root 'appearance.json')" '(Join-Path $env:TEMP ("mw-fixture-no-appearance-" + $PID + ".json"))'
Replace-Required '$script:tok = (Get-Content "$Root\.token" -ErrorAction SilentlyContinue | Select-Object -First 1)' '$script:tok = "visual-fixture-not-a-token"'
$pollStart = '$script:ps = [powershell]::Create(); [void]$script:ps.AddScript($poll).AddArgument($script:sync).AddArgument($Root).AddArgument($script:pyw); [void]$script:ps.BeginInvoke()'
Replace-Required $pollStart ("`$script:ps = `$null; `$script:sync.data = '" + $json + "' | ConvertFrom-Json; `$script:sync.lastAt = Get-Date; `$script:sync.stamp = 1")
$appearanceStart = '$script:appearancePs = [powershell]::Create(); [void]$script:appearancePs.AddScript($appearancePoll).AddArgument($script:sync).AddArgument($Root); [void]$script:appearancePs.BeginInvoke()'
Replace-Required $appearanceStart ("`$script:appearancePs = `$null; `$script:sync.appearance = [pscustomobject]@{schemaVersion=1;revision=0;mode='" + $Theme + "';palette='" + $Palette + "';recoveryRequired=`$" + $RecoveryRequired.IsPresent.ToString().ToLowerInvariant() + "}")
Replace-Required '$UI.trim.Add_Click({' '$UI.trim.Add_Click({ Show-Toast ''معاينة معزولة: التنظيف معطل''; return;'
Replace-Required '$UI.open.Add_Click({' '$UI.open.Add_Click({ Show-Toast ''معاينة معزولة: فتح وإغلاق التطبيقات معطل''; return;'
Replace-Required '$dashTimer.Start()' '# No external-window watcher in visual fixture.'
Replace-Required '$miAuto.Add_Click({' '$miAuto.IsEnabled = $false; $miAuto.Add_Click({'
Replace-Required 'Add-Type -AssemblyName System.Windows.Forms' "throw 'Visual fixture does not create a tray icon'; Add-Type -AssemblyName System.Windows.Forms"
Replace-Required "`$win.Title = 'مرصد الجهاز — الويدجت';" ("`$win.Title = 'معاينة مرصد — بيانات اختبار — " + $Theme + "';")
if ($RenderOutput) {
    $outputPath = [IO.Path]::GetFullPath($RenderOutput)
    if (-not [IO.Path]::IsPathRooted($RenderOutput)) { throw 'RenderOutput must be an absolute artifact path' }
    $render = @'
# Offscreen native WPF artifact rendering. No Show/Activate, cross-process UI or desktop capture.
if ('__FEEDBACK__') { Show-Toast '__FEEDBACK__' }
if(__CONFIRM_PREVIEW__){$UI.confirmText.Text='تشغيل تنظيف يومي الآن؟ التنفيذ الفعلي يحذف الملفات المطابقة للقواعد المفعلة.';$UI.confirm.Visibility='Visible'}
Update-Widget; Update-Countdown
$surface = $win.Content
$width = [int][Math]::Ceiling($win.Width)
function Invalidate-OwnTree($node){if($node -isnot [Windows.DependencyObject]){return};foreach($child in [Windows.LogicalTreeHelper]::GetChildren($node)){Invalidate-OwnTree $child};if($node -is [Windows.UIElement]){$node.InvalidateMeasure();$node.InvalidateArrange()}}
function Layout-OwnSurface {
    Invalidate-OwnTree $surface
    $surface.Measure([Windows.Size]::new($width,[double]::PositiveInfinity));$script:fixtureHeight=[int][Math]::Ceiling($surface.DesiredSize.Height)
    $surface.Arrange([Windows.Rect]::new(0,0,$width,$script:fixtureHeight));$surface.UpdateLayout()
}
Layout-OwnSurface
if (__AVAILABLE_HEIGHT__ -gt 0 -and $UI.expander.IsExpanded) {
    $nonDetails=$script:fixtureHeight-$detailsHost.ActualHeight
    if((Get-WidgetDetailsLimit __AVAILABLE_HEIGHT__ $nonDetails) -lt 180){$compact.Visibility='Collapsed';Layout-OwnSurface;$nonDetails=$script:fixtureHeight-$detailsHost.ActualHeight}
    $script:pageBudget=Get-WidgetDetailsLimit __AVAILABLE_HEIGHT__ $nonDetails;$detailsHost.Height=$script:pageBudget;Update-DetailPages;Layout-OwnSurface
}
if(__CONFIRM_PREVIEW__){Update-DetailPages $UI.confirm}elseif(__DETAIL_PAGE__ -ge 0){Show-DetailPage __DETAIL_PAGE__}
Layout-OwnSurface
$height=$script:fixtureHeight
if ($height -le 0 -or $height -gt 4000) { throw 'Unexpected native WPF fixture height' }
Update-Compact $script:sync.data; $surface.UpdateLayout()
if ($UI.expander.IsExpanded -and $script:S.showChart) { Draw-Chart $script:sync.data.hist.commit $script:sync.data.hist.ram; $surface.UpdateLayout() }
$UI.guard.Text='بيانات اختبار'
$headerRects=@{}
foreach($key in @('headerBrand','headerButtons','sub','pill')){$node=$UI[$key];$headerRects[$key]=$node.TransformToAncestor($UI.header).TransformBounds([Windows.Rect]::new(0,0,$node.ActualWidth,$node.ActualHeight))}
foreach($pair in @(@('headerBrand','headerButtons'),@('sub','pill'))){$intersection=[Windows.Rect]::Intersect($headerRects[$pair[0]],$headerRects[$pair[1]]);if(-not $intersection.IsEmpty -and $intersection.Width -gt .5 -and $intersection.Height -gt .5){throw 'Header overlap in actual widget source'}}
$naturalSub=New-Object Windows.Controls.TextBlock;$naturalSub.Text=$UI.sub.Text;$naturalSub.FontSize=$UI.sub.FontSize;$naturalSub.FontFamily=$UI.sub.FontFamily;$naturalSub.Measure([Windows.Size]::new([double]::PositiveInfinity,[double]::PositiveInfinity))
if($naturalSub.DesiredSize.Width -gt $UI.sub.ActualWidth+.5){throw 'Actual source connection text truncated'}
$ancestor=[Windows.LogicalTreeHelper]::GetParent($UI.adm)
while($ancestor){if($ancestor -eq $UI.header){throw 'Admin information still occupies header'};$ancestor=[Windows.LogicalTreeHelper]::GetParent($ancestor)}
$chartHorizontalFits=$null;$chartLabelFits=$null;$pageAudit=New-Object Collections.ArrayList
function Audit-OwnPage {
    Layout-OwnSurface
    $boundsHost=$detailsHost.TransformToAncestor($surface).TransformBounds([Windows.Rect]::new(0,0,$detailsHost.ActualWidth,$detailsHost.ActualHeight))
    $page=$script:detailPages[$script:detailPageIndex]
    foreach($part in $page.Parts){$node=$part.Node;$bounds=$node.TransformToAncestor($surface).TransformBounds([Windows.Rect]::new(0,0,$node.ActualWidth,$node.ActualHeight));if($bounds.Left -lt $boundsHost.Left-.5 -or $bounds.Right -gt $boundsHost.Right+.5 -or $bounds.Top -lt $boundsHost.Top-.5 -or $bounds.Bottom -gt $boundsHost.Bottom+.5){throw ('Paged content clipped: '+$page.Title)}}
    [void]$pageAudit.Add([pscustomobject]@{index=$script:detailPageIndex;title=$page.Title;parts=$page.Parts.Count;budget=$script:pageBudget;fits=$true})
}
if($UI.expander.IsExpanded){$selected=$script:detailPageIndex;if(__AUDIT_ALL__){for($i=0;$i -lt $script:detailPages.Count;$i++){Show-DetailPage $i;Audit-OwnPage};Show-DetailPage $selected;Layout-OwnSurface}else{Audit-OwnPage};$chartHorizontalFits=$true;$chartLabelFits=$true}
$script:opacityViolations=New-Object Collections.ArrayList;$script:opaqueTextCount=0;$script:opaqueEnabledControls=0
function Inspect-WidgetOpacity($node) {
    if ($node -isnot [Windows.DependencyObject]) {return}
    # Chart marks intentionally have subdued opacity; background control must not fade text/controls/layout parents.
    if ($node -is [Windows.UIElement] -and $node -isnot [Windows.Shapes.Shape] -and $node.IsEnabled -and $node.Opacity -ne 1) {[void]$script:opacityViolations.Add($node.GetType().Name+':opacity')}
    if ($node -is [Windows.Controls.TextBlock]) {$script:opaqueTextCount++;if($node.Foreground -is [Windows.Media.SolidColorBrush] -and $node.Foreground.Color.A -ne 255){[void]$script:opacityViolations.Add('TextBlock:foreground-alpha')}}
    if ($node -is [Windows.Controls.Primitives.ButtonBase] -and $node.IsEnabled){$script:opaqueEnabledControls++}
    foreach($child in [Windows.LogicalTreeHelper]::GetChildren($node)){Inspect-WidgetOpacity $child}
}
Inspect-WidgetOpacity $surface
if ($script:opacityViolations.Count) {throw ('Background opacity faded content: '+($script:opacityViolations -join ', '))}
$bitmap = [Windows.Media.Imaging.RenderTargetBitmap]::new($width,$height,96,96,[Windows.Media.PixelFormats]::Pbgra32)
$bitmap.Render($surface)
$encoder = [Windows.Media.Imaging.PngBitmapEncoder]::new()
[void]$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap))
$renderFile = '__RENDER_PATH__'
$stream = [IO.File]::Create($renderFile)
try { $encoder.Save($stream) } finally { $stream.Dispose() }
$geometry = @()
foreach ($name in 'header','headerBrand','headerButtons','headerStatus','title','sub','pill','feedbackBanner','adm','historyNotice','toast','appearanceWarning','compactcpu','compactram','compactdisk','compactAlert','expander','pageTitle','pageCount','detailsHost','detailsPager','chart','bar','open') {
    $control = $UI[$name]
    $bounds=$null;try { $bounds = $control.TransformToAncestor($surface).TransformBounds([Windows.Rect]::new(0,0,$control.ActualWidth,$control.ActualHeight)) } catch {}
    $geometry += [pscustomobject]@{name=$name;left=if($bounds){$bounds.Left}else{$null};top=if($bounds){$bounds.Top}else{$null};width=if($bounds){$bounds.Width}else{$null};height=if($bounds){$bounds.Height}else{$null};visibility=[string]$control.Visibility}
}
$availableHeight = __AVAILABLE_HEIGHT__
$result = [pscustomobject]@{method='offscreen native WPF rendering of actual source; explicitly labelled fixture data';sourceSha256='__SOURCE_HASH__';palette='__PALETTE__';theme='__THEME__';backgroundOpacity=[double]$script:S.opacity;windowOpacity=[double]$win.Opacity;outerBackgroundAlpha=[int]$card.Background.Color.A;innerBackgroundAlphas=@($script:backgroundSurfaces|ForEach-Object {[int]$_.Background.Color.A});headerOverlap=$false;connectionTextFits=$true;adminInHeader=$false;chartHorizontalFits=$chartHorizontalFits;chartLabelFits=$chartLabelFits;opaqueTextCount=$script:opaqueTextCount;opaqueEnabledControls=$script:opaqueEnabledControls;opacityViolations=@($script:opacityViolations);status=[string]$UI.pillText.Text;appearanceRecoveryRequired=[bool]$script:appearanceRecoveryRequired;metricValues=@{cpu=$UI.compactcpu.Text;ram=$UI.compactram.Text;disk=$UI.compactdisk.Text};width=$width;height=$height;dpi=96;expanded=[bool]$UI.expander.IsExpanded;availableHeight=$availableHeight;pageCount=$script:detailPages.Count;pageIndex=$script:detailPageIndex;pageBudget=$script:pageBudget;pageAudit=@($pageAudit);geometry=$geometry}
$result | Add-Member -NotePropertyName expandedDataNotice -NotePropertyValue ([string]$UI.historyNotice.Text)
$result | Add-Member -NotePropertyName expandedArcColors -NotePropertyValue @($UI.rCommit.Arc.Stroke.Color.ToString(),$UI.rRam.Arc.Stroke.Color.ToString(),$UI.rDisk.Arc.Stroke.Color.ToString())
if($UI.historyNotice.Text -match 'البيانات قديمة'){foreach($ring in @($UI.rCommit,$UI.rRam,$UI.rDisk)){if($ring.Arc.Stroke.Color.ToString() -ne (Brush $C.muted).Color.ToString()){throw 'Historical ring still implies current healthy state'}}}
if ($availableHeight -gt 0 -and $height -gt $availableHeight) { throw 'Offscreen widget exceeds the requested available height' }
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath ($renderFile + '.layout.json') -Encoding UTF8
$result | ConvertTo-Json -Depth 6
$app.Shutdown()
'@
    $render = $render.Replace('__RENDER_PATH__',$outputPath.Replace("'","''"))
    $render = $render.Replace('__AVAILABLE_HEIGHT__',[string]$AvailableHeight)
    $render = $render.Replace('__SOURCE_HASH__',$sourceSha256).Replace('__PALETTE__',$Palette).Replace('__THEME__',$Theme)
    $render = $render.Replace('__FEEDBACK__',$Feedback.Replace("'","''"))
    $render=$render.Replace('__CONFIRM_PREVIEW__',('$'+$ConfirmPreview.IsPresent.ToString().ToLowerInvariant())).Replace('__AUDIT_ALL__',('$'+$AuditAllPages.IsPresent.ToString().ToLowerInvariant())).Replace('__DETAIL_PAGE__',[string]$DetailPage)
    Replace-Required '[void]$app.Run($win)' $render
    foreach ($timerStart in '$bgTimer.Start()','$trimTimer.Start()','$timer.Start()') {
        if ($source.Contains($timerStart)) { $source = $source.Replace($timerStart,'# No event-loop timers in offscreen artifact rendering.') }
    }
}
$tokens = $null; $errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors | ForEach-Object Message | Out-String) }
if ($ValidateOnly) { Write-Output 'PASS fixture parses; settings/log writes, real token reads, network polling, mutations, task startup, tray and external-window actions disabled'; return }
& ([scriptblock]::Create($source)) $root
