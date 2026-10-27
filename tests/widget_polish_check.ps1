$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
$widgetRoot=Split-Path $PSScriptRoot -Parent
$source=Get-Content -LiteralPath (Join-Path $widgetRoot 'widget.ps1') -Raw
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors.Message -join '; ')}
. (Join-Path $PSScriptRoot 'widget_test_identity.ps1')   # الهوية المشتركة من جذر المستودع قبل تحميل الدوال المستخرجة
foreach($name in @('Normalize-WidgetOpacity','Surface-Brush','Brush','Get-Palette','New-Text','New-Card','Set-CardOpacity','Gap','Update-Compact')){
    $fn=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}.GetNewClosure(),$true)
    if(-not $fn){throw "Missing $name"};. ([scriptblock]::Create((Resolve-WidgetFunctionText $fn.Extent.Text)))
}
$script:S=@{opacity=1;palette='teal';muteAlerts=$true};$script:C=Get-Palette $true;$script:brushCache=@{};$script:backgroundSurfaces=New-Object Collections.ArrayList
foreach($value in @([double]::NaN,[double]::PositiveInfinity,[double]::NegativeInfinity,$null,$true,'invalid')){if((Normalize-WidgetOpacity $value) -ne .96){throw 'Invalid opacity not safely normalized'}}
if((Normalize-WidgetOpacity 1) -ne 1 -or (Normalize-WidgetOpacity .1) -ne .1 -or (Normalize-WidgetOpacity 2) -ne 1 -or (Normalize-WidgetOpacity -1) -ne .1){throw 'Valid opacity or bounds not preserved'}
$card=New-Object Windows.Controls.Border;$shadow=New-Object Windows.Media.Effects.DropShadowEffect
$panel=New-Card;$text=New-Text 'نص واضح' 14 $C.ink;$panel.Child=$text
$win=New-Object Windows.Window;$win.Opacity=1
foreach($opacity in @(1,.96,.5,.25,.1)){
    Set-CardOpacity $opacity
    # عقد الشفافية الشاملة: النافذة كلها تحمل المنزلقة (فتشمل النص والأزرار والرسم) والأسطح بسُمكها الثابت فلا تتضاعف الشفافية
    if($script:S.opacity -ne $opacity -or $win.Opacity -ne $opacity){throw 'Whole-widget opacity does not match slider'}
    if($card.Background.Color.A -ne 255){throw 'Outer surface alpha must stay fixed; the window carries the slider'}
    # اللوحة الداخلية تتبع المنزلق بنسبة 94% من المادة الخارجية (عقد الهوية المشتركة في widget_identity_check) ولا تبقى مصمتة عند خفضه
    if($panel.Background.Color.A -ne [byte][Math]::Round(255*.94) -or $panel.Background.Color.A -gt $card.Background.Color.A){throw 'Inner panel alpha must stay at 94% of the outer surface'}
    if($text.Opacity -ne 1 -or $text.Foreground.Color.A -ne 255 -or $panel.Opacity -ne 1){throw 'Per-element opacity would compound the window opacity'}
}
Write-Output 'PASS finite opacity normalization, saved100% preservation, whole-window opacity with fixed surface alphas and no per-element compounding'
if($source -notmatch "\`$opLbl.Text = 'عتامة الويدجت'" -or $source -notmatch '100% معتم تمامًا' -or $source -notmatch '\$opSl.Add_LostKeyboardFocus'){throw 'Opacity control lacks clear meaning or keyboard persistence'}
$stack=New-Object Windows.Controls.StackPanel;$stack.FlowDirection='RightToLeft';$UI=@{tiers=@{}}
$start=$source.IndexOf('# ---- header');$end=$source.IndexOf('# ---- rings',$start)
$headerSource=$source.Substring($start,$end-$start).Replace("(Join-Path `$PSScriptRoot 'assets\icon_192.png')","(Join-Path `$widgetRoot 'assets\icon_192.png')")
. ([scriptblock]::Create($headerSource))
foreach($name in @('compactcpu','compactram','compactdisk','compactAlert')){$UI[$name]=New-Object Windows.Controls.TextBlock}
$script:sync=@{lastAt=Get-Date;err=$null};$script:THR=@{dB=20;ramB=92};$script:alertSeen=@{};$script:alertSeeded=$false
function Notify-Important($m){}
function Header-Bounds($control){return $control.TransformToAncestor($UI.header).TransformBounds([Windows.Rect]::new(0,0,$control.ActualWidth,$control.ActualHeight))}
function Intersects-Substantial($a,$b){$rect=[Windows.Rect]::Intersect($a,$b);return (-not $rect.IsEmpty -and $rect.Width -gt .5 -and $rect.Height -gt .5)}
foreach($case in @('fresh','stale','missing')){foreach($admin in @($true,$false)){
    $UI.pillText.Text='مستقر';$d=@{now=(Get-Date).ToString('s');m=@{ramPct=45;diskFreeGB=148};cpuNow=@{pct=24;age=1}}
    if($case -eq 'stale'){$d.now=(Get-Date).AddMinutes(-10).ToString('s')};if($case -eq 'missing'){$d.cpuNow=$null}
    $UI.adm.Visibility=if($admin){'Visible'}else{'Collapsed'};Update-Compact $d
    $stack.Measure([Windows.Size]::new(284,[double]::PositiveInfinity));$stack.Arrange([Windows.Rect]::new(0,0,284,$stack.DesiredSize.Height));$stack.UpdateLayout()
    $brand=Header-Bounds $UI.headerBrand;$buttons=Header-Bounds $UI.headerButtons;$sub=Header-Bounds $UI.sub;$pill=Header-Bounds $UI.pill
    if((Intersects-Substantial $brand $buttons) -or (Intersects-Substantial $sub $pill)){throw "Header overlaps in $case admin=$admin"}
    if($sub.Top -lt $brand.Bottom-1 -or $pill.Top -lt $buttons.Bottom-1){throw 'Connection/status did not move onto separate row'}
    if($brand.Left -lt -.5 -or $buttons.Left -lt -.5 -or $sub.Left -lt -.5 -or $pill.Left -lt -.5 -or $brand.Right -gt 284.5 -or $buttons.Right -gt 284.5 -or $sub.Right -gt 284.5 -or $pill.Right -gt 284.5){throw 'Header exceeds inner width'}
    $natural=New-Object Windows.Controls.TextBlock;$natural.Text=$UI.sub.Text;$natural.FontSize=$UI.sub.FontSize;$natural.Measure([Windows.Size]::new([double]::PositiveInfinity,[double]::PositiveInfinity))
    if($natural.DesiredSize.Width -gt $UI.sub.ActualWidth+.5){throw "Connection text would be truncated in $case"}
    if([Windows.LogicalTreeHelper]::GetParent($UI.adm)){throw 'Privilege badge is still in visible header'}
}}
if($source -notmatch '\$UI.restoredMetrics.Children.Insert\(0,\$UI.adm\)'){throw 'Privilege information was not preserved in details'}
foreach($button in @($UI.menuBtn,$UI.closeBtn)){if($button.Opacity -ne 1 -or $button.Content.Opacity -ne 1 -or $button.Content.Foreground.Color.A -ne 255){throw 'Opacity faded header controls'}}
Write-Output 'PASS actual two-row WPF header geometry and complete connection text: fresh/stale/missing × admin/standard, no overlap or truncation; privilege information outside header'
