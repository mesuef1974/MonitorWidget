param([Parameter(Mandatory)][string]$PreviewData,[Parameter(Mandatory)][string]$RenderOutput,[ValidateSet('light','dark')][string]$Theme='dark',[ValidateSet('teal','blue','violet','graphite')][string]$Palette='teal',[ValidateSet('fresh','stale','missing','disconnected','pressure')][string]$State='fresh',[ValidateRange(600,4000)][int]$WorkHeight=600,[ValidateRange(.1,1)][double]$BackgroundOpacity=.96)
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent;$path=Join-Path $root 'widget.ps1';$source=Get-Content -LiteralPath $path -Raw;$hash=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
$data=Get-Content -LiteralPath $PreviewData -Raw|ConvertFrom-Json;$data.now=(Get-Date).ToString('s');$data.cpuNow=[pscustomobject]@{pct=24;age=1}
if($State -eq 'stale'){$data.now=(Get-Date).AddMinutes(-5).ToString('s')};if($State -eq 'missing'){$data.cpuNow=$null;$data.m.ramPct=$null;$data.m.diskFreeGB=$null};if($State -eq 'pressure'){$data.m.ramPct=99;$data.m.diskFreeGB=12}
$json=($data|ConvertTo-Json -Depth 30 -Compress).Replace("'","''")
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors);if($errors.Count){throw ($errors.Message -join '; ')}
$replacements=@{Log='function Log($m){if($m -like ''fatal:*''){throw $m}}';'Save-Settings'='function Save-Settings {}';'Start-Bg'='function Start-Bg($path,$body,$ok,$err){throw ''Fixture cannot invoke commands''}'}
$functions=$ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $replacements.ContainsKey($n.Name)},$true)|Sort-Object {$_.Extent.StartOffset} -Descending
foreach($fn in $functions){$source=$source.Remove($fn.Extent.StartOffset,$fn.Extent.EndOffset-$fn.Extent.StartOffset).Insert($fn.Extent.StartOffset,$replacements[$fn.Name])}
function Replace-Guard($old,$new){if(-not $script:source.Contains($old)){throw ('Fixture guard changed: '+$old)};$script:source=$script:source.Replace($old,$new)}
Replace-Guard "'Local\Azkia-MiniWidget'" '"Local\MonitorWidget-CompactFixture-$PID"'
Replace-Guard 'if ((Test-Path "$Root\widget.log")' 'if ($false -and (Test-Path "$Root\widget.log")'
Replace-Guard '$script:setPath = "$Root\widget.settings.json"' '$script:setPath = Join-Path $env:TEMP ("mw-fixture-unused-"+$PID+".json")'
Replace-Guard "(Join-Path `$Root 'appearance.json')" '(Join-Path $env:TEMP ("mw-fixture-unused-appearance-"+$PID+".json"))'
Replace-Guard "(Join-Path `$PSScriptRoot 'assets\icon_192.png')" "(Join-Path `$Root 'assets\icon_192.png')"
Replace-Guard "theme = 'auto'; palette = 'teal';" ("theme = '"+$Theme+"'; palette = '"+$Palette+"';")
Replace-Guard 'opacity = 0.96;' ('opacity = '+$BackgroundOpacity.ToString([Globalization.CultureInfo]::InvariantCulture)+';')
Replace-Guard '$script:tok = (Get-Content "$Root\.token" -ErrorAction SilentlyContinue | Select-Object -First 1)' '$script:tok="fixture-not-a-token"'
Replace-Guard '$script:ps = [powershell]::Create(); [void]$script:ps.AddScript($poll).AddArgument($script:sync).AddArgument($Root).AddArgument($script:pyw); [void]$script:ps.BeginInvoke()' ("`$script:ps=`$null;`$script:sync.data='"+$json+"'|ConvertFrom-Json;`$script:sync.lastAt=Get-Date;`$script:sync.stamp=1")
Replace-Guard '$script:appearancePs = [powershell]::Create(); [void]$script:appearancePs.AddScript($appearancePoll).AddArgument($script:sync).AddArgument($Root); [void]$script:appearancePs.BeginInvoke()' ("`$script:appearancePs=`$null;`$script:sync.appearance=[pscustomobject]@{schemaVersion=1;revision=0;mode='"+$Theme+"';palette='"+$Palette+"'}")
Replace-Guard '$UI.open.Add_Click({' '$UI.open.Add_Click({throw ''Fixture blocks application actions'';return;'
Replace-Guard '$dashTimer.Start()' '# Window watcher disabled.'
Replace-Guard '$miAuto.Add_Click({' '$miAuto.IsEnabled=$false;$miAuto.Add_Click({throw ''Fixture blocks startup settings'';return;'
Replace-Guard 'Add-Type -AssemblyName System.Windows.Forms' "throw 'Fixture blocks tray and desktop display'; Add-Type -AssemblyName System.Windows.Forms"
foreach($timer in @('$timer.Start()','$bgTimer.Start()')){$source=$source.Replace($timer,'# Timer disabled in offscreen fixture.')}
$render=@'
$script:widgetWorkHeightOverride=__WORKHEIGHT__
if('__STATE__' -eq 'disconnected'){$script:sync.data=$null;$script:sync.err='offline'}
Update-Widget
$surface=$win.Content;$width=[int][Math]::Ceiling($win.Width)
$surface.Measure([Windows.Size]::new($width,[double]::PositiveInfinity));$height=[int][Math]::Ceiling($surface.DesiredSize.Height);$surface.Arrange([Windows.Rect]::new(0,0,$width,$height));$surface.UpdateLayout()
if($script:sync.data){Update-Compact $script:sync.data;$surface.UpdateLayout()}
$violations=New-Object Collections.ArrayList;$controls=New-Object Collections.ArrayList
function Check-OwnTree($node){if($node -isnot [Windows.DependencyObject]){return};if($node -is [Windows.Controls.ScrollViewer] -or $node -is [Windows.Controls.Primitives.ScrollBar] -or $node -is [Windows.Controls.Expander]){[void]$violations.Add($node.GetType().Name)};if($node -is [Windows.Controls.Primitives.ButtonBase]){[void]$controls.Add([Windows.Automation.AutomationProperties]::GetName($node))};foreach($child in [Windows.LogicalTreeHelper]::GetChildren($node)){Check-OwnTree $child}}
Check-OwnTree $surface
if($violations.Count){throw ('Compact widget contains navigation/scrolling surface: '+($violations -join ','))}
if($controls.Count -ne 3){throw 'Compact widget contains extra action controls'}
$geometry=@();foreach($name in @('header','headerBrand','headerButtons','headerStatus','title','sub','pill','compactcpu','compactram','compactdisk','compactAlert','commitText','historyTitle','memoryChart','memoryLegend','maintenanceTitle','maintenanceLast','maintenanceNext','open')){$node=$UI[$name];$b=$node.TransformToAncestor($surface).TransformBounds([Windows.Rect]::new(0,0,$node.ActualWidth,$node.ActualHeight));if($b.Left -lt -.5 -or $b.Top -lt -.5 -or $b.Right -gt $width+.5 -or $b.Bottom -gt $height+.5){throw ('Compact control outside frame: '+$name)};$geometry+=[pscustomobject]@{name=$name;left=$b.Left;top=$b.Top;width=$b.Width;height=$b.Height}}
$a=$UI.headerBrand.TransformToAncestor($UI.header).TransformBounds([Windows.Rect]::new(0,0,$UI.headerBrand.ActualWidth,$UI.headerBrand.ActualHeight));$b=$UI.headerButtons.TransformToAncestor($UI.header).TransformBounds([Windows.Rect]::new(0,0,$UI.headerButtons.ActualWidth,$UI.headerButtons.ActualHeight));$inter=[Windows.Rect]::Intersect($a,$b);if(-not $inter.IsEmpty -and $inter.Width -gt .5 -and $inter.Height -gt .5){throw 'Compact header overlaps'}
if($height -gt [Math]::Floor(__WORKHEIGHT__*.85)){throw 'Compact widget exceeds85%ofworkarea'}
# Measure all real native menu levels without opening popups or invoking commands.
$menuLevels=New-Object Collections.ArrayList
function Check-OwnMenu($items){foreach($item in $items){if($item -is [Windows.Controls.MenuItem] -and $item.HasItems){$null=$item.ApplyTemplate();$border=$item.Template.FindName('SubMenuBorder',$item);$border.Measure([Windows.Size]::new(380,[double]::PositiveInfinity));if($border.DesiredSize.Height -gt __WORKHEIGHT__-32){throw 'Native submenu exceeds supported work area'};[void]$menuLevels.Add([pscustomobject]@{header=[string]$item.Header;height=$border.DesiredSize.Height;items=$item.Items.Count});Check-OwnMenu $item.Items}}}
$null=$menu.ApplyTemplate();$menu.Measure([Windows.Size]::new(380,[double]::PositiveInfinity));Check-OwnMenu $menu.Items
if($menu.DesiredSize.Height -gt __WORKHEIGHT__-32){throw 'Native settings menu exceeds supported work area'}
$bitmap=[Windows.Media.Imaging.RenderTargetBitmap]::new($width,$height,96,96,[Windows.Media.PixelFormats]::Pbgra32);$bitmap.Render($surface);$encoder=[Windows.Media.Imaging.PngBitmapEncoder]::new();[void]$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap));$file='__OUTPUT__';$stream=[IO.File]::Create($file);try{$encoder.Save($stream)}finally{$stream.Dispose()}
$result=[pscustomobject]@{method='actual compact widget source, offscreen WPF; fixture data; no desktop window/API/settings/actions';sourceSha256='__HASH__';theme='__THEME__';palette='__PALETTE__';state='__STATE__';workHeight=__WORKHEIGHT__;frameWidth=$width;frameHeight=$height;cardWidth=$card.ActualWidth;cardHeight=$card.ActualHeight;contentWidth=$stack.ActualWidth;contentHeight=$stack.ActualHeight;surfaceCount=1;scrollControls=0;expanders=0;pagerControls=0;actionNames=@($controls);openLabel=[string]$UI.open.Content;status=[string]$UI.pillText.Text;alert=[string]$UI.compactAlert.Text;commit=[string]$UI.commitText.Text;lastMaintenance=[string]$UI.maintenanceLast.Text;nextMaintenance=[string]$UI.maintenanceNext.Text;chartHeight=$UI.memoryChart.ActualHeight;windowOpacity=$win.Opacity;backgroundAlpha=$card.Background.Color.A;geometry=$geometry;menuHeight=$menu.DesiredSize.Height;menuLevels=@($menuLevels)}
$result|ConvertTo-Json -Depth 6|Set-Content -LiteralPath ($file+'.layout.json') -Encoding UTF8;$result|ConvertTo-Json -Depth 6;$app.Shutdown()
'@
$render=$render.Replace('__STATE__',$State).Replace('__WORKHEIGHT__',[string]$WorkHeight).Replace('__OUTPUT__',[IO.Path]::GetFullPath($RenderOutput).Replace("'","''")).Replace('__HASH__',$hash).Replace('__THEME__',$Theme).Replace('__PALETTE__',$Palette)
Replace-Guard '[void]$app.Run($win)' $render
$tokens=$null;$errors=$null;$null=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors);if($errors.Count){throw ($errors.Message -join '; ')}
& ([scriptblock]::Create($source)) $root
