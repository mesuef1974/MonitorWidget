param([string]$ArtifactDirectory='C:/Users/mesue/.codex/visualizations/2026/10/25/01a17266-b23c-7271-993a-11ccd8b89286/qa-widget-balanced-20261026')
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'widget_compact_check.ps1')
[void][IO.Directory]::CreateDirectory($ArtifactDirectory)
$hash=(Get-FileHash (Join-Path $root 'widget.ps1')).Hash.ToLowerInvariant()
function Balanced-Data {
 $d=Fresh-Data;$d.m.commitPct=62;$d.m.commitGB=20;$d.m.limitGB=32
 $now=[DateTimeOffset]::Now.ToUnixTimeSeconds();$d.hist=@{t=@(($now-300),($now-250),($now-200),($now-150),($now-100),($now-50),$now);ram=@(40,42,43,44,44,45,45);commit=@(50,52,$null,59,61,60,62)}
 $d.tiers=@{daily=@{paused=$false;last=@{ts=(Get-Date).AddHours(-2).ToString('s');failed=0;freed=1048576};next=(Get-Date).AddHours(2).ToString('s')};weekly=@{paused=$true;next=(Get-Date).AddMinutes(20).ToString('s')}};$d.manual=@{running=$false};return $d
}
function Render-Balanced($name,$height,$light,$data){
 $script:widgetWorkHeightOverride=$height;$script:S.palette='teal';Apply-Theme $light;Set-CardOpacity .96;$script:sync.data=$data;$script:sync.lastAt=Get-Date;$script:sync.err=$null;$script:appearanceRecoveryRequired=$false;$script:toastUntil=[datetime]::MinValue;$UI.toast.Visibility='Collapsed'
 Update-Widget;$h=Layout-Compact;if($data){Update-Compact $data};$h=Layout-Compact
 if($h -gt [Math]::Floor($height*.85)){throw "Balanced frame $h exceeds85%of$height"};if($h -lt 380){throw "Balanced view is still too sparse: $h"}
 $geometry=@();foreach($key in @('header','headerBrand','headerButtons','headerStatus','compactcpu','compactram','compactdisk','commitText','historyTitle','memoryChart','memoryLegend','maintenanceTitle','maintenanceLast','maintenanceNext','compactAlert','open')){$n=$UI[$key];$b=$n.TransformToAncestor($win.Content).TransformBounds([Windows.Rect]::new(0,0,$n.ActualWidth,$n.ActualHeight));if($b.Left -lt -.5 -or $b.Right -gt 340.5 -or $b.Top -lt -.5 -or $b.Bottom -gt $h+.5){throw ('Balanced bounds invalid: '+$key)};$geometry+=[pscustomobject]@{name=$key;left=$b.Left;top=$b.Top;width=$b.Width;height=$b.Height}}
 $surface=$win.Content;$bitmap=[Windows.Media.Imaging.RenderTargetBitmap]::new(340,$h,96,96,[Windows.Media.PixelFormats]::Pbgra32);$bitmap.Render($surface);$encoder=[Windows.Media.Imaging.PngBitmapEncoder]::new();[void]$encoder.Frames.Add([Windows.Media.Imaging.BitmapFrame]::Create($bitmap));$path=Join-Path $ArtifactDirectory ($name+'.png');$stream=[IO.File]::Create($path);try{$encoder.Save($stream)}finally{$stream.Dispose()}
 $result=[pscustomobject]@{method='actual widget source offscreen WPF, synthetic data; no live window/API/settings/startup changes';sourceSha256=$hash;workHeight=$height;theme=$(if($light){'light'}else{'dark'});frameWidth=340;frameHeight=$h;cardHeight=$card.ActualHeight;contentHeight=$stack.ActualHeight;chartHeight=$UI.memoryChart.ActualHeight;commit=$UI.commitText.Text;lastMaintenance=$UI.maintenanceLast.Text;nextMaintenance=$UI.maintenanceNext.Text;status=$UI.pillText.Text;alert=$UI.compactAlert.Text;geometry=$geometry;windowOpacity=$win.Opacity;backgroundAlpha=$card.Background.Color.A};$result|ConvertTo-Json -Depth 6|Set-Content ($path+'.layout.json') -Encoding UTF8;return $result
}
$results=@();foreach($height in @(600,768,1080)){foreach($light in @($false,$true)){$theme=if($light){'light'}else{'dark'};$results+=Render-Balanced ('balanced-'+$theme+'-'+$height) $height $light (Balanced-Data)}}
if($UI.commitText.Text -notmatch '62%' -or $UI.maintenanceLast.Text -notmatch 'نجحت' -or $UI.maintenanceNext.Text -notmatch 'يومية'){throw 'Commit/maintenance summary missing or paused schedule chosen'}
$polylines=@($UI.memoryChart.Children|Where-Object {$_ -is [Windows.Shapes.Polyline]});if($polylines.Count -lt 3){throw 'Missing memory histories or null-gap segmentation'}
foreach($line in $polylines){foreach($pt in $line.Points){if([double]::IsNaN($pt.X) -or [double]::IsNaN($pt.Y) -or $pt.X -lt 0 -or $pt.X -gt $UI.memoryChart.ActualWidth -or $pt.Y -gt $UI.memoryChart.ActualHeight){throw 'History point outside actual chart'}}}
$d=Balanced-Data;$d.m.commitPct=99;$results+=Render-Balanced 'balanced-commit-pressure-600' 600 $false $d;if($UI.pillText.Text -ne 'حرج' -or $UI.compactAlert.Text -notmatch 'التزام.*99'){throw 'Critical commit is invisible'}
$d=Balanced-Data;$d.cpuNow=$null;$d.m.ramPct=$null;$d.m.diskFreeGB=$null;$d.m.commitPct=$null;$d.hist=$null;$d.tiers=$null;$results+=Render-Balanced 'balanced-missing-light-600' 600 $true $d;if($UI.pillText.Text -ne 'ناقص' -or $UI.commitText.Text -notmatch 'غير مقاس' -or $UI.maintenanceNext.Text -notmatch 'غير متاح'){throw 'Missing sources appear measured or scheduled'}
$d=Balanced-Data;$d.cpuAlert=@{name=('long-process '*70)};$results+=Render-Balanced 'balanced-long-alert-600' 600 $false $d;if($UI.compactAlert.Text.Length -gt 125){throw 'Small-workarea alert is not bounded'}
$d=Balanced-Data;$d.now=(Get-Date).AddMinutes(-5).ToString('s');$results+=Render-Balanced 'balanced-stale-dark-600' 600 $false $d;if($UI.historyTitle.Text -notmatch 'قديمة' -or $UI.pillText.Text -ne 'قديم'){throw 'Historical trace presented as live'}
$results+=Render-Balanced 'balanced-disconnected-dark-600' 600 $false $null;if($UI.pillText.Text -ne 'غير متصل' -or $UI.historyTitle.Text -notmatch 'لا اتصال'){throw 'Disconnected presentation claims live data'}
$results|ConvertTo-Json -Depth 7|Set-Content (Join-Path $ArtifactDirectory 'manifest.json') -Encoding UTF8
Write-Output ('PASS balanced single-surface source: '+$results.Count+' native offscreen renders; frames '+(($results|ForEach-Object{$_.frameHeight})-join ',')+' DIP; commit/history/null gaps/maintenance/paused/unknown/stale/long-alert; source '+$hash)
