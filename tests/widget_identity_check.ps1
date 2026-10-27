$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
$root=Split-Path $PSScriptRoot -Parent
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'widget.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors.Message -join '; ')}
. (Join-Path $PSScriptRoot 'widget_test_identity.ps1')   # الهوية المشتركة من جذر المستودع قبل تحميل الدوال المستخرجة
foreach($name in @('Get-Palette','Alpha','Brush','Normalize-WidgetOpacity','Surface-Brush','New-Card','Set-CardOpacity','New-Text','New-Ring','Ring-Arc','Set-Ring','Level-Brush','Set-Switch','Remap-Tree','Apply-Theme','Set-Theme','Test-AppearanceRecord','Receive-Appearance','Set-WidgetControlTheme')){
 $fn=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}.GetNewClosure(),$true)
 if(-not $fn){throw "Missing $name"};. ([scriptblock]::Create((Resolve-WidgetFunctionText $fn.Extent.Text)))
}
function Get-SysLight{return $false}
$script:S=@{palette='teal';theme='dark';opacity=.96};$script:brushCache=@{};$script:backgroundSurfaces=New-Object Collections.ArrayList
$script:C=Get-Palette $false;$script:isLight=$false;$script:appearanceRevision=0;$script:appearanceMenuItems=@();$menu=$null;$shadow=$null
$outer=New-Object Windows.Controls.StackPanel;$card=New-Object Windows.Controls.Border;$panel=New-Card;$title=New-Text 'مرصد الجهاز' 14 $C.accent
$track=New-Object Windows.Shapes.Ellipse;$track.Stroke=Brush $C.track
[void]$outer.Children.Add($card);[void]$outer.Children.Add($panel);[void]$outer.Children.Add($title);[void]$outer.Children.Add($track)
$UI=@{title=$title;tiers=@{}}
$bases=@{};$surfaces=@{};$accents=@{};$revision=0
foreach($light in @($false,$true)){foreach($palette in @('teal','blue','violet','graphite')){
 $revision++;$mode=if($light){'light'}else{'dark'}
 Receive-Appearance ([pscustomobject]@{schemaVersion=1;revision=$revision;mode=$mode;palette=$palette})
 Set-CardOpacity .96
 $p=Get-Palette $light
 if($card.Background.Color.R -ne [Windows.Media.ColorConverter]::ConvertFromString('#'+$p.base).R -or $card.Background.Color.ToString() -ne (Surface-Brush ('#'+$p.base) .96).Color.ToString()){throw 'Main surface did not adopt canonical identity'}
 if($panel.Background.Color.ToString() -ne (Surface-Brush $p.card (.96*.94)).Color.ToString()){throw 'Section surface did not adopt canonical identity'}
 if($panel.Background.Color.A -lt 220){throw 'Section identity is almost invisible'}
 if($track.Stroke.Color.ToString() -ne (Brush $p.track).Color.ToString()){throw 'Indicator track retained previous identity'}
 $bases["$mode/$palette"]=$card.Background.Color.ToString();$surfaces["$mode/$palette"]=$panel.Background.Color.ToString();$accents["$mode/$palette"]=(Brush $p.accent).Color.ToString()
 $r=New-Ring 'مؤشر';Set-Ring $r 45 'ok' '45' '%' 'عينة'
 if($r.Arc.Stroke.Color.ToString() -ne (Brush $p.accent).Color.ToString()){throw 'Normal indicator does not use identity'}
 Set-Ring $r 95 'bad' '95' '%' 'عينة'
 if($r.Arc.Stroke.Color.ToString() -ne (Brush $p.bad).Color.ToString()){throw 'Danger lost semantic color'}
}}
# في الهوية المشتركة (tokens.css) تتبع الأسطح الوضعَ (داكن/فاتح) بينما تحدّد اللوحة لونَ الهوية؛ لذا سطحان مميّزان وثمانية ألوان هوية
if(@($bases.Values|Select-Object -Unique).Count -ne 2 -or @($surfaces.Values|Select-Object -Unique).Count -ne 2 -or $bases['dark/teal'] -eq $bases['light/teal']){throw 'Surfaces do not follow the canonical mode'}
if(@($accents.Values|Select-Object -Unique).Count -ne 8){throw 'Identity accent does not follow the canonical palette'}
Write-Output 'PASS eight canonical identity changes repaint main surface, section surfaces, tracks and healthy gauges per mode, accent per palette; danger stays semantic; no network, real settings or live window.'
