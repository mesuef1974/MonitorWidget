param([string]$RenderDirectory='')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
$root=Split-Path $PSScriptRoot -Parent;$source=Get-Content -LiteralPath (Join-Path $root 'widget.ps1') -Raw
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors.Message -join '; ')}
. (Join-Path $PSScriptRoot 'widget_test_identity.ps1')   # الهوية المشتركة من جذر المستودع قبل تحميل الدوال المستخرجة
foreach($name in @('Get-Palette','Brush','Alpha','Normalize-WidgetOpacity','Surface-Brush','New-Text','New-Card','Gap','New-ActionButton','New-IconBtn','Set-WidgetControlTheme')){
    $fn=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}.GetNewClosure(),$true)
    if(-not $fn){throw "Missing $name"};. ([scriptblock]::Create((Resolve-WidgetFunctionText $fn.Extent.Text)))
}
function Luminance($color){$v=@();foreach($part in @($color.R,$color.G,$color.B)){$n=[double]$part/255.0;$v+=if($n -le .04045){$n/12.92}else{[Math]::Pow(($n+.055)/1.055,2.4)}};return .2126*$v[0]+.7152*$v[1]+.0722*$v[2]}
function Contrast($a,$b){$x=Luminance $a;$y=Luminance $b;return ([Math]::Max($x,$y)+.05)/([Math]::Min($x,$y)+.05)}
function Set-FocusState($item,$value){$key=[Windows.UIElement].GetField('IsKeyboardFocusedPropertyKey',[Reflection.BindingFlags]'NonPublic,Static').GetValue($null);$item.SetValue($key,[bool]$value)}
function Send-Hover($control,$entered){$event=New-Object Windows.Input.MouseEventArgs([Windows.Input.Mouse]::PrimaryDevice,[Environment]::TickCount);$event.RoutedEvent=if($entered){[Windows.Input.Mouse]::MouseEnterEvent}else{[Windows.Input.Mouse]::MouseLeaveEvent};$control.RaiseEvent($event)}
function Find-Text($node){$out=@();if($node -is [Windows.Controls.TextBlock] -or $node -is [Windows.Controls.AccessText]){$out+=$node};for($i=0;$i -lt [Windows.Media.VisualTreeHelper]::GetChildrenCount($node);$i++){$out+=Find-Text ([Windows.Media.VisualTreeHelper]::GetChild($node,$i))};return $out}
function Layout($button){$null=$button.ApplyTemplate();$button.Measure([Windows.Size]::new(220,80));$button.Arrange([Windows.Rect]::new(0,0,220,[Math]::Max(34,$button.DesiredSize.Height)));$button.UpdateLayout()}
function Save-Button($button,$path){Layout $button;$w=[int][Math]::Ceiling($button.ActualWidth);$h=[int][Math]::Ceiling($button.ActualHeight);$bmp=[Windows.Media.Imaging.RenderTargetBitmap]::new($w,$h,96,96,[Windows.Media.PixelFormats]::Pbgra32);$bmp.Render($button);$enc=[Windows.Media.Imaging.PngBitmapEncoder]::new();[void]$enc.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bmp));$stream=[IO.File]::Create($path);try{$enc.Save($stream)}finally{$stream.Dispose()}}
$rows=New-Object Collections.ArrayList;$minimum=100.0;$knobMinimum=100.0
foreach($light in @($true,$false)){foreach($palette in @('teal','blue','violet','graphite')){
    $script:S=@{palette=$palette;opacity=.25};$script:C=Get-Palette $light;$script:brushCache=@{};$script:backgroundSurfaces=New-Object Collections.ArrayList
    $UI=@{tiers=@{}};$stack=New-Object Windows.Controls.StackPanel
    # Construct actual source command controls only; no interaction section or real command handler is loaded.
    $a=$source.IndexOf('# ---- cleanup panel');$b=$source.IndexOf('# ---- footer',$a)
    . ([scriptblock]::Create($source.Substring($a,$b-$a)))
    $a=$source.IndexOf('# ---- footer');$b=$source.IndexOf('# Approved reference composition',$a)
    . ([scriptblock]::Create($source.Substring($a,$b-$a)))
    $UI.menuBtn=New-IconBtn '⋯';$UI.closeBtn=New-IconBtn '✕'
    Set-WidgetControlTheme
    $buttons=@{cfReal=$UI.cfReal;cfDry=$UI.cfDry;cfNo=$UI.cfNo;trim=$UI.trim;open=$UI.open;menuBtn=$UI.menuBtn;closeBtn=$UI.closeBtn;daily=$UI.tiers.daily.Run}
    foreach($name in $buttons.Keys){$button=$buttons[$name]
        foreach($state in @('normal','hover','disabled','focused')){
            $button.IsEnabled=$true;Set-FocusState $button $false;Send-Hover $button $false
            if($state -eq 'hover'){Send-Hover $button $true};if($state -eq 'disabled'){$button.IsEnabled=$false};if($state -eq 'focused'){Set-FocusState $button $true};Layout $button
            $frame=$button.Template.FindName('Frame',$button);if(-not $frame -or $frame.Background.Color.A -ne 255 -or $button.Opacity -ne 1){throw 'Action surface is not opaque or disabled state fades contrast'}
            $texts=@(Find-Text $button);if(-not $texts.Count){throw 'Actual command text was not templated'}
            foreach($text in $texts){$fg=[Windows.Documents.TextElement]::GetForeground($text);$ratio=Contrast $fg.Color $frame.Background.Color;if($fg.Color.A -ne 255 -or $ratio -lt 4.5){throw "Unreadable actual command $palette light=$light $name $state contrast=$ratio"};$minimum=[Math]::Min($minimum,$ratio);[void]$rows.Add([pscustomobject]@{palette=$palette;light=$light;control=$name;state=$state;foreground=$fg.Color.ToString();background=$frame.Background.Color.ToString();contrast=$ratio})}
            if($RenderDirectory -and $name -eq 'cfReal' -and $palette -eq 'teal'){
                if(-not [IO.Path]::IsPathRooted($RenderDirectory)){throw 'RenderDirectory must be absolute'};[void][IO.Directory]::CreateDirectory($RenderDirectory);$mode=if($light){'light'}else{'dark'};Save-Button $button (Join-Path $RenderDirectory ('widget-confirm-'+$mode+'-'+$state+'.png'))
            }
        }
    }
    foreach($on in @($true,$false)){Set-Switch $UI.tiers.daily.Switch $on;$switch=$UI.tiers.daily.Switch;$ratio=Contrast $switch.Content.Fill.Color $switch.Background.Color;if($ratio -lt 3){throw 'Switch thumb lost nontext contrast'};$knobMinimum=[Math]::Min($knobMinimum,$ratio)}
}}
if($RenderDirectory){[pscustomobject]@{sourceSha256=(Get-FileHash -LiteralPath (Join-Path $root 'widget.ps1') -Algorithm SHA256).Hash.ToLowerInvariant();method='actual source command factories and role application; native Content text/Frame background in owned controls; no real command or settings write';paletteCount=8;controlStateCases=256;minimumTextContrast=$minimum;minimumSwitchContrast=$knobMinimum;rows=@($rows)}|ConvertTo-Json -Depth 6|Set-Content -LiteralPath (Join-Path $RenderDirectory 'widget-button-contrast.json') -Encoding UTF8}
Write-Output ('PASS actual source buttons ×4states ×8palettes: minimum text contrast '+$minimum.ToString('N2')+':1; minimum on/off switch contrast '+$knobMinimum.ToString('N2')+':1; opaque backgrounds, effective Content text and disabled opacity checked')
Write-Output 'No cleanup, API request, click/command, settings write or native window executed.'
