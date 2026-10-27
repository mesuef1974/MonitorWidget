param([string]$RenderDirectory='')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
$root=Split-Path $PSScriptRoot -Parent;$source=Get-Content -LiteralPath (Join-Path $root 'widget.ps1') -Raw
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors.Message -join '; ')}
. (Join-Path $PSScriptRoot 'widget_test_identity.ps1')   # الهوية المشتركة من جذر المستودع قبل تحميل الدوال المستخرجة
foreach($name in @('Get-Palette','Brush','New-WidgetMenuStyles','Set-WidgetMenuItems','Set-WidgetMenuTheme','Set-WidgetControlTheme')){
    $fn=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}.GetNewClosure(),$true)
    if(-not $fn){throw "Missing $name"};. ([scriptblock]::Create((Resolve-WidgetFunctionText $fn.Extent.Text)))
}
function Luminance($color){$values=@();foreach($v in @($color.R,$color.G,$color.B)){$n=[double]$v/255.0;$values+=if($n -le .04045){$n/12.92}else{[Math]::Pow(($n+.055)/1.055,2.4)}};return .2126*$values[0]+.7152*$values[1]+.0722*$values[2]}
function Contrast($fg,$bg){$a=Luminance $fg;$b=Luminance $bg;return ([Math]::Max($a,$b)+.05)/([Math]::Min($a,$b)+.05)}
function Set-NativeReadOnly($item,[type]$owner,[string]$key,[bool]$value){$field=$owner.GetField($key,[Reflection.BindingFlags]'NonPublic,Static');if(-not $field){throw "Native state key missing $key"};$item.SetValue($field.GetValue($null),$value)}
function Find-HeaderText($node){if($node -is [Windows.Controls.AccessText] -or $node -is [Windows.Controls.TextBlock]){return $node};for($i=0;$i -lt [Windows.Media.VisualTreeHelper]::GetChildrenCount($node);$i++){$found=Find-HeaderText ([Windows.Media.VisualTreeHelper]::GetChild($node,$i));if($found){return $found}};return $null}
function Layout($control,[double]$width=360){$null=$control.ApplyTemplate();$control.Measure([Windows.Size]::new($width,[double]::PositiveInfinity));$control.Arrange([Windows.Rect]::new(0,0,$width,$control.DesiredSize.Height));$control.UpdateLayout()}
function Save-Native($control,[string]$path,[double]$width=360){Layout $control $width;$height=[int][Math]::Ceiling($control.DesiredSize.Height);$bitmap=[Windows.Media.Imaging.RenderTargetBitmap]::new([int]$width,$height,96,96,[Windows.Media.PixelFormats]::Pbgra32);$bitmap.Render($control);$encoder=[Windows.Media.Imaging.PngBitmapEncoder]::new();[void]$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap));$stream=[IO.File]::Create($path);try{$encoder.Save($stream)}finally{$stream.Dispose()}}
$rows=New-Object Collections.ArrayList;$minimum=100.0
foreach($light in @($true,$false)){foreach($palette in @('teal','blue','violet','graphite')){
    $script:S=@{palette=$palette;opacity=.25};$script:C=Get-Palette $light;$script:brushCache=@{};$UI=@{}
    $menu=New-Object Windows.Controls.ContextMenu;$menu.FlowDirection='RightToLeft'
    $parent=New-Object Windows.Controls.MenuItem;$parent.Header='المظهر الموحد';$child=New-Object Windows.Controls.MenuItem;$child.Header='اختيار المظهر';$child.IsCheckable=$true;[void]$parent.Items.Add($child);[void]$menu.Items.Add($parent)
    # The real opacity header contains nested labels and a Slider; no handler is invoked.
    $opacityStart=$source.IndexOf('# opacity slider');$opacityEnd=$source.IndexOf('$miTop = New-Object',$opacityStart)
    . ([scriptblock]::Create($source.Substring($opacityStart,$opacityEnd-$opacityStart)))
    Set-WidgetMenuTheme;Layout $menu
    $null=$parent.ApplyTemplate();$null=$child.ApplyTemplate();Layout $child
    $popup=$parent.Template.FindName('PART_Popup',$parent);$surface=$parent.Template.FindName('SubMenuBorder',$parent)
    if(-not $popup -or -not $surface -or $popup.IsOpen -or $surface.Background.Color.A -ne 255 -or $surface.Background.Color.ToString() -ne (Brush $C.card).Color.ToString()){throw 'Submenu does not own its closed, opaque semantic surface'}
    foreach($state in @('normal','highlighted','checked','disabled','focused')){
        $child.IsEnabled=$true;$child.IsChecked=($state -eq 'checked');Set-NativeReadOnly $child ([Windows.Controls.MenuItem]) 'IsHighlightedPropertyKey' ($state -eq 'highlighted');Set-NativeReadOnly $child ([Windows.UIElement]) 'IsKeyboardFocusedPropertyKey' ($state -eq 'focused');if($state -eq 'disabled'){$child.IsEnabled=$false};Layout $child
        $frame=$child.Template.FindName('Frame',$child);$presenter=$child.Template.FindName('HeaderPresenter',$child);$text=Find-HeaderText $presenter
        if(-not $text){throw 'Actual templated submenu text not generated'}
        $fg=[Windows.Documents.TextElement]::GetForeground($text);$bg=$frame.Background;$ratio=Contrast $fg.Color $bg.Color
        if($fg.Color.A -ne 255 -or $bg.Color.A -ne 255 -or $ratio -lt 4.5){throw "Unreadable native submenu $palette light=$light state=$state contrast=$ratio"}
        $minimum=[Math]::Min($minimum,$ratio)
        [void]$rows.Add([pscustomobject]@{palette=$palette;light=$light;state=$state;foreground=$fg.Color.ToString();background=$bg.Color.ToString();contrast=$ratio;popupSurface=$surface.Background.Color.ToString()})
        if($state -eq 'checked' -and $child.Template.FindName('CheckGlyph',$child).Visibility -ne 'Visible'){throw 'Checked state has no visible mark'}
        if($state -eq 'checked' -and (Contrast $child.Template.FindName('CheckGlyph',$child).Foreground.Color $bg.Color) -lt 4.5){throw 'Checked glyph contrast lost'}
        if($state -eq 'focused' -and ($frame.BorderBrush.Color.A -ne 255 -or (Contrast $frame.BorderBrush.Color $bg.Color) -lt 3)){throw 'Keyboard focus border missing or too faint'}
    }
    foreach($state in @('normal','highlighted','disabled')){$opItem.IsEnabled=($state -ne 'disabled');Set-NativeReadOnly $opItem ([Windows.Controls.MenuItem]) 'IsHighlightedPropertyKey' ($state -eq 'highlighted');Layout $opItem;$frame=$opItem.Template.FindName('Frame',$opItem);foreach($label in @($opLbl,$opVal)){$ratio=Contrast $label.Foreground.Color $frame.Background.Color;if($ratio -lt 4.5){throw 'Embedded opacity label lost menu foreground pairing'}}}
    $child.IsEnabled=$true;Set-NativeReadOnly $child ([Windows.UIElement]) 'IsKeyboardFocusedPropertyKey' $false;Set-NativeReadOnly $child ([Windows.Controls.MenuItem]) 'IsHighlightedPropertyKey' $false
    $child.IsChecked=$true;$peer=[Windows.Automation.Peers.MenuItemAutomationPeer]::new($child);if(-not $peer.GetPattern([Windows.Automation.Peers.PatternInterface]::Toggle)){throw 'Menu check automation pattern lost'}
    $parentPeer=[Windows.Automation.Peers.MenuItemAutomationPeer]::new($parent);if(-not $parentPeer.GetPattern([Windows.Automation.Peers.PatternInterface]::ExpandCollapse)){throw 'Submenu expansion automation pattern lost'}
    Layout $parent;$arrow=$parent.Template.FindName('SubmenuArrow',$parent);$parentFrame=$parent.Template.FindName('Frame',$parent);if($arrow.Visibility -ne 'Visible' -or (Contrast $arrow.Foreground.Color $parentFrame.Background.Color) -lt 4.5){throw 'Submenu arrow unreadable'}
    if($RenderDirectory -and (($light -and $palette -eq 'teal') -or (-not $light -and $palette -eq 'graphite'))){
        if(-not [IO.Path]::IsPathRooted($RenderDirectory)){throw 'RenderDirectory must be absolute'};[void][IO.Directory]::CreateDirectory($RenderDirectory)
        $panel=New-Object Windows.Controls.StackPanel;$panel.FlowDirection='RightToLeft';$panel.Background=Brush $C.card
        foreach($state in @('normal','highlighted','checked','disabled','focused')){$item=New-Object Windows.Controls.MenuItem;$item.Header=switch($state){'normal'{'خيار عادي'}'highlighted'{'خيار محدد بالفأرة'}'checked'{'اختيار مفعّل'}'disabled'{'خيار غير متاح'}default{'تركيز لوحة المفاتيح'}};$item.Style=$child.Style;$item.IsCheckable=$true;$item.IsChecked=($state -eq 'checked');$item.IsEnabled=($state -ne 'disabled');[void]$panel.Children.Add($item);Set-NativeReadOnly $item ([Windows.Controls.MenuItem]) 'IsHighlightedPropertyKey' ($state -eq 'highlighted');Set-NativeReadOnly $item ([Windows.UIElement]) 'IsKeyboardFocusedPropertyKey' ($state -eq 'focused')}
        $arrow=New-Object Windows.Controls.MenuItem;$arrow.Header='قائمة فرعية';$arrow.Style=$child.Style;[void]$arrow.Items.Add((New-Object Windows.Controls.MenuItem));[void]$panel.Children.Add($arrow)
        $name=if($light){'light-teal'}else{'dark-graphite'};Save-Native $panel (Join-Path $RenderDirectory ('widget-menu-'+$name+'-states.png'))
        # Render the actual PART_Popup child without opening a desktop popup.
        Save-Native $surface (Join-Path $RenderDirectory ('widget-menu-'+$name+'-submenu.png'))
    }
}}
if($RenderDirectory){[pscustomobject]@{sourceSha256=(Get-FileHash -LiteralPath (Join-Path $root 'widget.ps1') -Algorithm SHA256).Hash.ToLowerInvariant();method='owned native WPF MenuItem templates, actual HeaderPresenter text/Frame/closed PART_Popup child; no desktop popup or command';paletteCount=8;menuStateCases=40;minimumContrast=$minimum;rows=@($rows)}|ConvertTo-Json -Depth 6|Set-Content -LiteralPath (Join-Path $RenderDirectory 'widget-menu-contrast.json') -Encoding UTF8}
Write-Output ('PASS actual native submenu text/frame/popup surfaces: 8 palettes ×5 states; minimum contrast '+$minimum.ToString('N2')+':1; embedded opacity labels and check automation preserved')
Write-Output 'No desktop popup opened, command invoked or real settings changed.'
