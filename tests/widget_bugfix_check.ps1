$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
$tokens=$null;$errors=$null;$path=Join-Path (Split-Path $PSScriptRoot -Parent) 'widget.ps1'
$ast=[Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors.Message -join '; ')}
foreach($name in @('Get-SummaryLevel','Get-WidgetPlacement','Get-WidgetDetailsLimit','Show-Toast','Update-Compact','Test-AppearanceRecord','Get-Palette','Brush','Alpha','Remap-Tree','New-WidgetMenuStyles','Set-WidgetMenuItems','Set-WidgetMenuTheme','Set-WidgetControlTheme','Apply-Theme','Set-Theme','Receive-Appearance','Request-Appearance')){
    $fn=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}.GetNewClosure(),$true)
    if(-not $fn){throw "Missing $name"};. ([scriptblock]::Create($fn.Extent.Text))
}
if((Get-SummaryLevel @('ok','bad','ok')) -ne 'bad'){throw 'Critical RAM omitted from summary'}
if((Get-SummaryLevel @('ok','warn','ok')) -ne 'warn'){throw 'Warning RAM omitted from summary'}
$updateFn=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Update-Widget'},$true)
if($updateFn.Extent.Text -notmatch 'Get-SummaryLevel @\(\$lc, \$lr, \$ld\)'){throw 'Actual widget summary omits RAM'}
Write-Output 'PASS RAM warning and critical levels participate in actual widget summary'
foreach($case in @(@([Windows.Rect]::new(0,0,1920,1040),340,600,1500,850),@([Windows.Rect]::new(-1280,0,1280,720),340,500,-1500,600),@([Windows.Rect]::new(1920,-200,1536,824),340,600,3400,500))){
    $w=$case[0];$p=Get-WidgetPlacement $w $case[1] $case[2] $case[3] $case[4]
    if($p.Left -lt $w.Left -or $p.Top -lt $w.Top -or $p.Left+$case[1] -gt $w.Right -or $p.Top+$case[2] -gt $w.Bottom){throw 'Expanded widget outside monitor work area'}
}
if($ast.Extent.Text -notmatch '\$win.Add_SizeChanged\(\{ Fit-WidgetBounds \}\)'){throw 'No expansion-size bounds correction'}
Write-Output 'PASS work-area placement at primary, negative-coordinate and secondary-monitor positions; resize handler is wired'
foreach($nonDetails in @(266,306,346)){if((Get-WidgetDetailsLimit 600 $nonDetails)+$nonDetails -gt 510){throw 'Transient feedback/recovery exceeds expanded height budget'}}
function Notify-Important($m){}
$script:S=@{theme='light';palette='teal';muteAlerts=$false};$script:C=Get-Palette $true;$script:isLight=$true;$script:brushCache=@{}
$UI=@{}
foreach($name in @('toast','appearanceWarning','historyNotice','sub','compactcpu','compactram','compactdisk','compactAlert','pillText')){$UI[$name]=New-Object Windows.Controls.TextBlock}
$UI.pill=New-Object Windows.Controls.Border;$script:THR=@{dB=20;ramB=92};$script:sync=@{lastAt=Get-Date;err=$null};$script:alertSeen=@{};$script:alertSeeded=$false
Show-Toast 'تعذّر حفظ المظهر؛ رسالة يجب أن تبقى واضحة'
$d=@{now=(Get-Date).ToString('s');m=@{ramPct=45;diskFreeGB=120};cpuNow=@{pct=20;age=1}}
1..3 | ForEach-Object { Update-Compact $d }
if($UI.toast.Text -notmatch 'تعذّر حفظ' -or $UI.toast.Visibility -ne 'Visible' -or $script:toastUntil -lt (Get-Date).AddSeconds(3)){throw 'Poll erased feedback message'}
Write-Output 'PASS operation feedback survives repeated metric refreshes in a separate wrapping control'
$d.now=(Get-Date).AddMinutes(-5).ToString('s');Update-Compact $d
if($UI.historyNotice.Visibility -ne 'Visible' -or $UI.historyNotice.Text -notmatch 'آخر قراءة محفوظة.*قديمة'){throw 'Expanded old data is not visibly labeled historical'}
$d.now=(Get-Date).ToString('s');Update-Compact $d
if($UI.historyNotice.Visibility -ne 'Collapsed' -or $UI.historyNotice.Text){throw 'Expanded historical qualifier survived fresh reconnection'}
$missing=@{now=(Get-Date).ToString('s');m=@{ramPct=$null;diskFreeGB=$null};cpuNow=@{pct=20;age=1}}
foreach($key in @('rCommit','rRam','rDisk')){$UI[$key]=@{Arc=New-Object Windows.Shapes.Path;Val=New-Object Windows.Controls.TextBlock}}
Update-Compact $missing
if($UI.pillText.Text -ne 'ناقص' -or $UI.compactAlert.Text -notmatch 'غير متاحة' -or $UI.compactram.Text -ne '—' -or $UI.compactdisk.Text -ne '—'){throw 'Missing RAM/disk presented as healthy or critically low'}
if($UI.historyNotice.Visibility -ne 'Visible' -or $UI.historyNotice.Text -notmatch 'غير مكتملة'){throw 'Expanded missing-data qualifier absent'}
foreach($key in @('rCommit','rRam','rDisk')){if($UI[$key].Val.Text -ne '—' -or $UI[$key].Arc.Stroke.Color.ToString() -ne (Brush $C.muted).Color.ToString()){throw 'Missing expanded metric became zero or a colored health judgment'}}
$d.now=(Get-Date).ToString('s');Update-Compact $d
if($UI.historyNotice.Visibility -ne 'Collapsed'){throw 'Expanded missing-data qualifier survived complete measurements'}
Write-Output 'PASS missing RAM/disk remain unknown instead of healthy or zero-space alarm'
Write-Output 'PASS expanded stale/missing qualifiers follow the compact freshness decision and clear on fresh reconnection'
$outer=New-Object Windows.Controls.StackPanel;$sample=New-Object Windows.Controls.Border;$sample.Background=Brush $C.card
$text=New-Object Windows.Controls.TextBlock;$text.Foreground=Brush $C.ink;$sample.Child=$text;[void]$outer.Children.Add($sample)
$menu=New-Object Windows.Controls.ContextMenu;$script:appearanceRevision=0;$script:appearanceMenuItems=@();$script:appearanceRecoveryRequired=$false
function Get-SysLight{return $true}
function Set-CardOpacity($v){}
$revision=1
foreach($palette in @('teal','blue','violet','graphite')){foreach($mode in @('dark','light','dark','light')){
    Receive-Appearance ([pscustomobject]@{schemaVersion=1;revision=$revision;mode=$mode;palette=$palette});$revision++
    if($script:isLight -ne ($mode -eq 'light')){throw 'Night mode state did not apply'}
    $r=$sample.Background.Color
    if($mode -eq 'dark' -and ([int]$r.R+[int]$r.G+[int]$r.B) -gt 300){throw 'Night mode retained light panel'}
    if($mode -eq 'light' -and ([int]$r.R+[int]$r.G+[int]$r.B) -lt 500){throw 'Light mode retained dark panel'}
}}
Write-Output 'PASS sixteen real WPF brush transitions across four palettes, including repeated night/day changes'
$beforeMode=$script:S.theme;$beforePalette=$script:S.palette;$beforeRevision=$script:appearanceRevision
Receive-Appearance ([pscustomobject]@{schemaVersion=1;revision=0;mode='auto';palette='teal';recoveryRequired=$true})
if(-not $script:appearanceRecoveryRequired -or $UI.appearanceWarning.Visibility -ne 'Visible' -or $script:S.theme -ne $beforeMode -or $script:S.palette -ne $beforePalette -or $script:appearanceRevision -ne $beforeRevision){throw 'Recovery fallback replaced confirmed appearance or omitted warning'}
$script:posted=$false
function Start-Bg($path,$body,$ok,$err){$script:posted=$true}
Request-Appearance 'dark' 'violet'
if($script:posted -or $UI.toast.Text -notmatch 'تحتاج إصلاح'){throw 'Corrupt appearance accepted cosmetic save'}
Write-Output 'PASS recoveryRequired preserves last confirmed appearance, exposes persistent warning and blocks writes'
