param([string]$Root = $PSScriptRoot)
# Mini desktop widget (WPF): translucent glass card, live rings + history chart, real cleanup switches.
# Data comes from the local backend (server.py /api/mini), polled on a background runspace so the UI never blocks.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase
$script:mutex = New-Object Threading.Mutex($false, 'Local\Azkia-MiniWidget')
$wait = if ($env:MW_REPLACE -eq '1') { 12000 } else { 0 }
$got = $false
try { $got = $script:mutex.WaitOne($wait) } catch [Threading.AbandonedMutexException] { $got = $true }   # previous owner crashed: the mutex is now ours
if (-not $got) { exit 0 }
$env:MW_REPLACE = $null
Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public static class W32{[DllImport("user32.dll")]public static extern int GetWindowLong(IntPtr h,int i);[DllImport("user32.dll")]public static extern int SetWindowLong(IntPtr h,int i,int v);}'

function Log($m) { try { Add-Content "$Root\widget.log" ("{0:s} {1}" -f (Get-Date), $m) -Encoding UTF8 } catch {} }
if ((Test-Path "$Root\widget.log") -and ((Get-Item "$Root\widget.log").Length -gt 200KB)) { Remove-Item "$Root\widget.log" -Force }

# ------------------------------------------------------------------ settings
$script:setPath = "$Root\widget.settings.json"
$script:S = @{ left = $null; top = $null; opacity = 0.96; topmost = $false; theme = 'auto'; palette = 'teal'; restorePos = $true; expanded = $false; showChart = $false; muteAlerts = $false }
if (Test-Path $script:setPath) {
    try { $j = Get-Content $script:setPath -Raw | ConvertFrom-Json; foreach ($k in 'left','top','opacity','topmost','theme','restorePos','expanded','showChart','muteAlerts') { if ($null -ne $j.$k) { $script:S[$k] = $j.$k } } } catch {}
}
function Save-Settings {
    # Geometry and widget preferences remain local. Appearance is owned by the backend.
    $gate = New-Object Threading.Mutex($false, 'Local\MonitorWidget-WidgetSettings'); $held = $false
    try {
        try { $held = $gate.WaitOne(2000) } catch [Threading.AbandonedMutexException] { $held = $true }
        if (-not $held) { return }
        $o = @{}; if (Test-Path -LiteralPath $script:setPath) { $old = Get-Content -LiteralPath $script:setPath -Raw | ConvertFrom-Json; foreach ($p in $old.PSObject.Properties) { $o[$p.Name] = $p.Value } }
        foreach ($k in $script:S.Keys) { if ($k -notin @('theme','palette')) { $o[$k] = $script:S[$k] } }
        $tmp = "$script:setPath.$PID.tmp"; $o | ConvertTo-Json | Set-Content -LiteralPath $tmp -Encoding UTF8; Move-Item -LiteralPath $tmp -Destination $script:setPath -Force
    } catch { Log ('settings: ' + $_.Exception.Message) } finally { if ($held) { $gate.ReleaseMutex() }; $gate.Dispose() }
}
$script:appearanceRevision = -1; $script:appearancePending = $false; $script:appearanceRecoveryRequired = $false
function Test-AppearanceRecord($value) {
    if (-not $value) { return $false }
    $schemaInteger = ($value.schemaVersion -is [int] -or $value.schemaVersion -is [long])
    $revisionInteger = ($value.revision -is [int] -or $value.revision -is [long])
    return ($schemaInteger -and $value.schemaVersion -eq 1 -and $revisionInteger -and $value.revision -ge 0 -and $value.mode -in @('auto','light','dark') -and $value.palette -in @('teal','blue','violet','graphite'))
}
try {
    $initialAppearance = Get-Content -LiteralPath (Join-Path $Root 'appearance.json') -Raw -ErrorAction Stop | ConvertFrom-Json
    if (Test-AppearanceRecord $initialAppearance) {
        $script:S.theme = $initialAppearance.mode; $script:S.palette = $initialAppearance.palette; $script:appearanceRevision = [long]$initialAppearance.revision
    }
} catch {}

$script:restoreLeft = $script:S.left; $script:restoreTop = $script:S.top

# ------------------------------------------------------------------ palette + builders
$sysLight = $false
try { $sysLight = ((Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -ErrorAction Stop).AppsUseLightTheme -eq 1) } catch {}
$script:isLight = switch ($script:S.theme) { 'light' { $true } 'dark' { $false } default { $sysLight } }
# Shared identity covers surfaces, indicators and controls; warning/danger meanings stay independent.
function Get-SysLight { try { return ((Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -ErrorAction Stop).AppsUseLightTheme -eq 1) } catch { return $false } }
. (Join-Path $PSScriptRoot 'identity.ps1')
function Get-Palette([bool]$light) {
    $p = if ($light) { @{ ink='#182E34'; muted='#536970'; accent='#086F64'; violet='#A33F71'; ok='#08765E'; warn='#865000'; bad='#B42318'; card='#F3FAF7'; line='#B8D9CE'; base='E4F1ED'; edge='#D6E2DE'; track='#E8EFED'; hover='#DFF1EB'; swoff='#9BAFA8'; seg2='#7042AA'; seg3='#849A94'; seg4='#E8EFED'; pilltxt='#FFFFFF' } }
         else { @{ ink='#E5EEED'; muted='#A8BFBC'; accent='#65DAC3'; violet='#EF9AC3'; ok='#58D6AF'; warn='#EFC471'; bad='#FF9B92'; card='#1B312C'; line='#3A6258'; base='101F1D'; edge='#344947'; track='#213130'; hover='#203D35'; swoff='#566D65'; seg2='#D3B0FF'; seg3='#6A827B'; seg4='#213130'; pilltxt='#10231F' } }
    # الألوان من web\css	okens.css (المصدر الوحيد للهوية)؛ القاموس أعلاه احتياطي إن تعذّرت القراءة
    $p = Get-IdentityPalette $PSScriptRoot $light ([string]$script:S.palette) $p
    # Avoid token-color aliases: remapping cannot otherwise distinguish identity and composition series.
    $p.seg2 = if ($light) { '#8159B3' } else { '#C1A3E8' }; $p.seg4 = if ($light) { '#E3EBE7' } else { '#283A35' }
    $p.onWarn=if($light){'#FFFFFF'}else{'#1A1100'}
    if(-not $p.accentText){$p.accentText=$p.accent}   # نصّ accent على البطاقة؛ من --accent-text إن وُجد
    $p.knob=if($light){'#FFFFFF'}else{'#10231F'}
    $p.swoff=if($light){'#61766E'}else{'#7C958B'}
    return $p
}
$C = Get-Palette $script:isLight
$FONTS = Get-IdentityFonts $PSScriptRoot   # Zain لكل النص العربي وOutfit للأرقام من assets\fonts بحسب رموز tokens.css؛ Segoe UI إن غابت
function Alpha([string]$hex, [string]$aa) { '#' + $aa + $hex.TrimStart('#') }
$script:brushCache = @{}
$script:backgroundSurfaces = New-Object Collections.ArrayList
function Normalize-WidgetOpacity($value) {
    if ($null -eq $value -or $value -is [bool]) { return 0.96 }
    $number=0.0
    if (-not [double]::TryParse([string]$value,[Globalization.NumberStyles]::Float,[Globalization.CultureInfo]::InvariantCulture,[ref]$number) -or [double]::IsNaN($number) -or [double]::IsInfinity($number)) { return 0.96 }
    return [Math]::Max(0.1,[Math]::Min(1.0,$number))
}
function Surface-Brush([string]$color,[double]$opacity) {
    $c=[Windows.Media.ColorConverter]::ConvertFromString($color)
    $a=[byte][Math]::Round(255*[Math]::Max(0.0,[Math]::Min(1.0,$opacity)))
    return Brush ([Windows.Media.Color]::FromArgb($a,$c.R,$c.G,$c.B).ToString())
}
function Brush([string]$hex) {
    $b = $script:brushCache[$hex]
    if (-not $b) { $b = New-Object Windows.Media.SolidColorBrush ([Windows.Media.ColorConverter]::ConvertFromString($hex)); $b.Freeze(); $script:brushCache[$hex] = $b }
    $b
}
function New-WidgetMenuStyles {
    # Every popup owns an opaque surface. System/Aero popup brushes cannot inherit a paired foreground reliably.
    $xaml=@'
<ResourceDictionary xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
  <Style x:Key="WidgetMenuItem" TargetType="{x:Type MenuItem}">
    <Setter Property="Foreground" Value="__INK__"/><Setter Property="Background" Value="__SURFACE__"/>
    <Setter Property="BorderBrush" Value="Transparent"/><Setter Property="BorderThickness" Value="1"/>
    <Setter Property="Padding" Value="8,6"/><Setter Property="MinHeight" Value="32"/><Setter Property="Opacity" Value="1"/>
    <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="{x:Type MenuItem}">
      <Grid>
        <Border x:Name="Frame" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" Padding="{TemplateBinding Padding}" CornerRadius="5" TextElement.Foreground="{TemplateBinding Foreground}">
          <Grid>
            <Grid.ColumnDefinitions><ColumnDefinition Width="20"/><ColumnDefinition Width="*"/><ColumnDefinition Width="Auto"/><ColumnDefinition Width="16"/></Grid.ColumnDefinitions>
            <ContentPresenter x:Name="IconPresenter" ContentSource="Icon" VerticalAlignment="Center" HorizontalAlignment="Center"/>
            <TextBlock x:Name="CheckGlyph" Text="✓" Visibility="Collapsed" Foreground="{TemplateBinding Foreground}" VerticalAlignment="Center" HorizontalAlignment="Center"/>
            <ContentPresenter x:Name="HeaderPresenter" Grid.Column="1" ContentSource="Header" RecognizesAccessKey="True" VerticalAlignment="Center" TextElement.Foreground="{TemplateBinding Foreground}"/>
            <TextBlock Grid.Column="2" Text="{TemplateBinding InputGestureText}" Foreground="{TemplateBinding Foreground}" VerticalAlignment="Center" Margin="12,0,0,0"/>
            <TextBlock x:Name="SubmenuArrow" Grid.Column="3" Text="‹" Visibility="Collapsed" Foreground="{TemplateBinding Foreground}" VerticalAlignment="Center" HorizontalAlignment="Center"/>
          </Grid>
        </Border>
        <Popup x:Name="PART_Popup" IsOpen="{Binding IsSubmenuOpen, RelativeSource={RelativeSource TemplatedParent}}" Placement="Left" PlacementTarget="{Binding ElementName=Frame}" Focusable="False" AllowsTransparency="True" PopupAnimation="None">
          <Border x:Name="SubMenuBorder" Background="__SURFACE__" BorderBrush="__LINE__" BorderThickness="1" CornerRadius="8" Padding="4" TextElement.Foreground="__INK__">
            <StackPanel IsItemsHost="True" KeyboardNavigation.DirectionalNavigation="Cycle"/>
          </Border>
        </Popup>
      </Grid>
      <ControlTemplate.Triggers>
        <Trigger Property="HasItems" Value="True"><Setter TargetName="SubmenuArrow" Property="Visibility" Value="Visible"/></Trigger>
        <Trigger Property="IsChecked" Value="True"><Setter TargetName="CheckGlyph" Property="Visibility" Value="Visible"/><Setter TargetName="IconPresenter" Property="Visibility" Value="Collapsed"/></Trigger>
        <Trigger Property="IsHighlighted" Value="True"><Setter Property="Background" Value="__HOVER__"/><Setter Property="Foreground" Value="__INK__"/></Trigger>
        <Trigger Property="IsSubmenuOpen" Value="True"><Setter Property="Background" Value="__HOVER__"/><Setter Property="Foreground" Value="__INK__"/></Trigger>
        <Trigger Property="IsKeyboardFocused" Value="True"><Setter Property="BorderBrush" Value="__ACCENT__"/></Trigger>
        <Trigger Property="IsEnabled" Value="False"><Setter Property="Foreground" Value="__MUTED__"/><Setter Property="Background" Value="__SURFACE__"/><Setter Property="BorderBrush" Value="Transparent"/><Setter Property="Opacity" Value="1"/></Trigger>
      </ControlTemplate.Triggers>
    </ControlTemplate></Setter.Value></Setter>
  </Style>
  <Style x:Key="WidgetSeparator" TargetType="{x:Type Separator}"><Setter Property="Template"><Setter.Value><ControlTemplate TargetType="{x:Type Separator}"><Border Background="__LINE__" Height="1" Margin="8,5"/></ControlTemplate></Setter.Value></Setter></Style>
  <Style x:Key="WidgetContextMenu" TargetType="{x:Type ContextMenu}">
    <Setter Property="Background" Value="__SURFACE__"/><Setter Property="Foreground" Value="__INK__"/><Setter Property="BorderBrush" Value="__LINE__"/><Setter Property="Opacity" Value="1"/>
    <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="{x:Type ContextMenu}"><Border x:Name="MenuBorder" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="1" CornerRadius="8" Padding="4" TextElement.Foreground="{TemplateBinding Foreground}"><StackPanel IsItemsHost="True" KeyboardNavigation.DirectionalNavigation="Cycle"/></Border></ControlTemplate></Setter.Value></Setter>
  </Style>
</ResourceDictionary>
'@
    $height=[Math]::Max(160,[Windows.SystemParameters]::WorkArea.Height-48)
    foreach($pair in @(@('__SURFACE__',$C.card),@('__INK__',$C.ink),@('__MUTED__',$C.muted),@('__LINE__',$C.line),@('__HOVER__',$C.hover),@('__ACCENT__',$C.accent),@('__MAXHEIGHT__',$height.ToString([Globalization.CultureInfo]::InvariantCulture)))){$xaml=$xaml.Replace($pair[0],[string]$pair[1])}
    return [Windows.Markup.XamlReader]::Parse($xaml)
}
function Set-WidgetMenuItems($items,$resources) {
    foreach($item in $items){
        if($item -is [Windows.Controls.MenuItem]){
            $item.ClearValue([Windows.Controls.Control]::ForegroundProperty);$item.ClearValue([Windows.Controls.Control]::BackgroundProperty)
            $item.Style=$resources['WidgetMenuItem'];$item.Resources[[Windows.Controls.MenuItem]]=$resources['WidgetMenuItem'];$item.Resources[[Windows.Controls.Separator]]=$resources['WidgetSeparator']
            Set-WidgetMenuItems $item.Items $resources
        }elseif($item -is [Windows.Controls.Separator]){$item.Style=$resources['WidgetSeparator']}
    }
}
function Set-WidgetMenuTheme {
    if($menu){$resources=New-WidgetMenuStyles;$menu.ClearValue([Windows.Controls.Control]::BackgroundProperty);$menu.ClearValue([Windows.Controls.Control]::ForegroundProperty);$menu.Style=$resources['WidgetContextMenu'];$menu.Resources[[Windows.Controls.MenuItem]]=$resources['WidgetMenuItem'];$menu.Resources[[Windows.Controls.Separator]]=$resources['WidgetSeparator'];Set-WidgetMenuItems $menu.Items $resources}
    Set-WidgetControlTheme
}
function Set-WidgetControlTheme {
    if(-not $UI){return}
    if($UI.title){$UI.title.Foreground=Brush $C.accentText};if($UI.logoRing){$UI.logoRing.Stroke=Brush $C.accent;$UI.logoPulse.Stroke=Brush $C.warn;$UI.logoDot.Fill=Brush $C.warn};if($UI.confirm){$UI.confirm.Background=Brush $C.card;$UI.confirm.BorderBrush=Brush $C.warn}
    foreach($key in @('menuBtn','closeBtn','open')){$button=$UI[$key];if($button){$button.Background=Brush $C.card;$button.BorderBrush=Brush $C.line;$button.BorderThickness=1;$button.Foreground=Brush $(if($key -eq 'open'){$C.accentText}else{$C.muted});if($button.Content -is [Windows.Controls.TextBlock]){$button.Content.Foreground=$button.Foreground}}}
    if($UI.toast){$UI.toast.Background=Brush $C.line;$UI.toast.Foreground=Brush $C.ink;$UI.toast.FontWeight='SemiBold'}
    if($UI.trim){$UI.trim.Background=Brush $C.card;$UI.trim.BorderBrush=Brush $C.accent;$UI.trimText.Foreground=Brush $C.accentText;$UI.trimCd.Foreground=Brush $C.muted}
    foreach($spec in @(@('cfReal',$C.warn,$C.onWarn),@('cfDry',$C.hover,$C.ink),@('cfNo',$C.card,$C.muted))){$button=$UI[$spec[0]];if($button){$button.Background=Brush $spec[1];$button.Foreground=Brush $spec[2];if($button.Content -is [Windows.Controls.TextBlock]){$button.Content.Foreground=Brush $spec[2]}}}
    if($UI.tiers){foreach($tier in $UI.tiers.Values){if($tier.Run){$tier.Run.Background=Brush $C.card;$tier.Run.BorderBrush=Brush $C.accent;$tier.RunText.Foreground=Brush $C.accentText};if($tier.Switch -and $tier.Switch.Content -is [Windows.Shapes.Ellipse]){$tier.Switch.Content.Fill=Brush $C.knob;$tier.Switch.Background=if($tier.Switch.IsChecked){Brush $C.accent}else{Brush $C.swoff}}}}
}
function Remap-Tree($o) {
    if ($o -isnot [Windows.DependencyObject]) { return }
    foreach ($pn in 'Foreground', 'Background', 'BorderBrush', 'Fill', 'Stroke') {
        $pi = $o.GetType().GetProperty($pn)
        if ($pi) { try { $br = $pi.GetValue($o); if ($br -is [Windows.Media.SolidColorBrush]) { $k = $br.Color.ToString(); if ($script:themeMap.ContainsKey($k)) { $pi.SetValue($o, (New-Object Windows.Media.SolidColorBrush $script:themeMap[$k])) } } } catch {} }
    }
    foreach ($ch in [Windows.LogicalTreeHelper]::GetChildren($o)) { Remap-Tree $ch }
}
function Apply-Theme([bool]$light) {
    $old = $script:C; $new = Get-Palette $light; $script:themeMap = @{}
    foreach ($k in $old.Keys) {
        if ($k -eq 'base') { continue }
        $oc = [Windows.Media.ColorConverter]::ConvertFromString($old[$k]).ToString()
        if (-not $script:themeMap.ContainsKey($oc)) { $script:themeMap[$oc] = [Windows.Media.ColorConverter]::ConvertFromString($new[$k]) }
        foreach ($aa in @('26','2A','66','70')) {
            $oa = [Windows.Media.ColorConverter]::ConvertFromString((Alpha $old[$k] $aa)).ToString()
            if (-not $script:themeMap.ContainsKey($oa)) { $script:themeMap[$oa] = [Windows.Media.ColorConverter]::ConvertFromString((Alpha $new[$k] $aa)) }
        }
    }
    $script:C = $new; $script:isLight = $light
    Remap-Tree $outer
    if ($menu) { Set-WidgetMenuTheme }
    $script:chartSig = $null
}
function Set-Theme([string]$name) {
    if ($name -notin @('auto','light','dark')) { return }
    $script:S.theme = $name
    $light = switch ($name) { 'light' { $true } 'dark' { $false } default { Get-SysLight } }
    if ($light -ne $script:isLight) { Apply-Theme $light; Set-CardOpacity $script:S.opacity; $script:lastStamp = -1 }
}
function Receive-Appearance($appearance) {
    if (-not (Test-AppearanceRecord $appearance)) { return }
    if ($appearance.recoveryRequired -eq $true) {
        $script:appearanceRecoveryRequired = $true
        if ($UI.appearanceWarning) { $UI.appearanceWarning.Text = 'تعذّر قراءة إعدادات المظهر؛ آخر مظهر مؤكد محفوظ حتى إصلاحها.'; $UI.appearanceWarning.Visibility = 'Visible' }
        foreach ($i in @($script:appearanceMenuItems)) { $i.IsEnabled = $false }
        return
    }
    if ([long]$appearance.revision -lt $script:appearanceRevision) { return }
    $script:appearanceRecoveryRequired = $false
    if ($UI.appearanceWarning) { $UI.appearanceWarning.Visibility = 'Collapsed' }
    foreach ($i in @($script:appearanceMenuItems)) { $i.IsEnabled = $true }
    $paletteChanged = $script:S.palette -ne $appearance.palette
    $script:S.palette = [string]$appearance.palette; $script:appearanceRevision = [long]$appearance.revision
    Set-Theme ([string]$appearance.mode)
    if ($paletteChanged) { Apply-Theme $script:isLight; Set-CardOpacity $script:S.opacity; $script:lastStamp = -1 }
    foreach ($i in @($script:appearanceMenuItems)) { $i.IsChecked = if ($i.Tag -like 'mode:*') { $i.Tag -eq ('mode:' + $script:S.theme) } else { $i.Tag -eq ('palette:' + $script:S.palette) } }
}
function Request-Appearance([string]$mode, [string]$palette) {
    if ($script:appearanceRecoveryRequired) { Show-Toast 'إعدادات المظهر تحتاج إصلاحًا؛ تعذّر حفظ التغيير'; return }
    if ($script:appearancePending) { Show-Toast 'جارٍ حفظ المظهر؛ انتظر النتيجة'; return }
    if ($script:appearanceRevision -lt 0) { Show-Toast 'انتظر اتصال إعدادات المظهر بالخادم'; return }
    $script:appearancePending = $true
    Start-Bg '/api/appearance' @{ revision=$script:appearanceRevision; mode=$mode; palette=$palette } { param($r) $script:appearancePending=$false; Receive-Appearance $r; Show-Toast 'حُفظ المظهر للتطبيق والويدجت معًا' } { param($m) $script:appearancePending=$false; Show-Toast 'تعذّر حفظ المظهر؛ يُعاد تحميل آخر اختيار مؤكد' }
}
function New-Text([string]$text, [double]$size = 12, [string]$color = $C.ink, [string]$weight = 'Normal', [bool]$ltr = $false, [string]$kind = '') {
    $t = New-Object Windows.Controls.TextBlock
    $t.Text = $text; $t.FontSize = $size; $t.Foreground = Brush $color; $t.FontWeight = [Windows.FontWeights]::$weight
    # خطوط الهوية كما في الواجهة: النص LTR أرقام بـ --f-num (Outfit)، والعناوين بـ --f-head (Zain)، وسائر النصوص بـ --f-ui (Zain أيضًا منذ 28/10).
    # ارتفاع السطر مثبّت على 1.34 من الحجم (مقياس Segoe UI السابق) حتى لا يغيّر تباعدُ Zain الأكبر هندسةَ الويدجت المعتمدة.
    if (-not $kind) { $kind = if ($ltr) { 'num' } else { 'ui' } }
    if ($FONTS -and $FONTS[$kind]) { $t.FontFamily = $FONTS[$kind] }
    $t.LineStackingStrategy = 'BlockLineHeight'; $t.LineHeight = [Math]::Round($size * 1.34, 2)
    $t.TextTrimming = 'CharacterEllipsis'
    if ($ltr) { $t.FlowDirection = 'LeftToRight' }
    $t
}
function New-Card([double]$pad = 10) {
    $b = New-Object Windows.Controls.Border
    $b.Background = Surface-Brush $C.card .94; $b.BorderBrush=Surface-Brush $C.line .45; $b.BorderThickness=.75
    $b.CornerRadius = 10; $b.Padding = $pad
    if (-not $script:backgroundSurfaces) { $script:backgroundSurfaces=New-Object Collections.ArrayList }
    [void]$script:backgroundSurfaces.Add($b)
    $b
}
function Level-Brush([string]$lvl) { switch ($lvl) { 'bad' { Brush $C.bad } 'warn' { Brush $C.warn } default { Brush $C.ok } } }
# thresholds come from /api/mini -> thr (alerts.json); defaults keep the old behaviour if the backend omits them
$script:THR = @{ ramW = 80; ramB = 92; cmW = 85; cmB = 95; dW = 40; dB = 20 }
function Load-Thr($t) {
    try {
        if ($t.mem) { foreach ($p in @(@('ramW','ramWarn'), @('ramB','ramBad'), @('cmW','commitWarn'), @('cmB','commitBad'))) { $v = $t.mem.($p[1]); if ($null -ne $v) { $script:THR[$p[0]] = [double]$v } } }
        if ($t.disk) { if ($null -ne $t.disk.freeWarnGB) { $script:THR.dW = [double]$t.disk.freeWarnGB }; if ($null -ne $t.disk.freeBadGB) { $script:THR.dB = [double]$t.disk.freeBadGB } }
    } catch { }
}
function Lvl([double]$v, [double]$w, [double]$b) { if ($v -ge $b) { 'bad' } elseif ($v -ge $w) { 'warn' } else { 'ok' } }

$IW = 294  # Approved350DIPportrait includes28DIPpadding and28DIPshadow margin

# ------------------------------------------------------------------ window skeleton
$win = New-Object Windows.Window
$win.Title = 'مرصد الجهاز — الويدجت'; $win.WindowStyle = 'None'; $win.AllowsTransparency = $true; $win.Background = [Windows.Media.Brushes]::Transparent
$win.ShowInTaskbar = $false; $win.ResizeMode = 'NoResize'; $win.SizeToContent = 'Height'; $win.Width = $IW + 28 + 28
$win.MinWidth = $win.Width; $win.MaxWidth = $win.Width
$win.Topmost = [bool]$script:S.topmost; $win.FlowDirection = 'RightToLeft'; $win.FontFamily = $FONTS.ui
$win.UseLayoutRounding = $true; $win.SnapsToDevicePixels = $true

$outer = New-Object Windows.Controls.Grid; $outer.Margin = 14
$card = New-Object Windows.Controls.Border
$card.CornerRadius = 14; $card.Padding = 14; $card.BorderThickness = 1; $card.BorderBrush = Brush $C.edge
$shadow = New-Object Windows.Media.Effects.DropShadowEffect; $shadow.BlurRadius = 20; $shadow.ShadowDepth = 4; $shadow.Opacity = 0.22; $shadow.Color = [Windows.Media.Colors]::Black
$card.Effect = $shadow
function Set-CardOpacity($value) {
    # شفافية شاملة: النافذة كلها (الخلفية والنص والأزرار والرسم والظل) تحمل العتامة؛ الأسطح بسُمكها الثابت عند 100%
    $v=Normalize-WidgetOpacity $value; $script:S.opacity=$v
    if ($win) { $win.Opacity=$v }
    $card.Background=Surface-Brush ('#'+$C.base) 1.0
    $card.BorderBrush=Surface-Brush $C.edge .8
    if ($shadow) { $shadow.Opacity=.22 }
    foreach ($surface in @($script:backgroundSurfaces)) { $surface.Background=Surface-Brush $C.card .94; $surface.BorderBrush=Surface-Brush $C.line .45 }
}
Set-CardOpacity $script:S.opacity
$outer.Children.Add($card) | Out-Null; $win.Content = $outer
$stack = New-Object Windows.Controls.StackPanel; $card.Child = $stack
$UI = @{ tiers = @{} }

function Gap([double]$h) { $g = New-Object Windows.Controls.Border; $g.Height = $h; $g }

# ---- header
$hdr = New-Object Windows.Controls.Grid
foreach ($w in @('*', 'Auto')) { $cd = New-Object Windows.Controls.ColumnDefinition; $cd.Width = $w; $hdr.ColumnDefinitions.Add($cd) }
1..2 | ForEach-Object { $row=New-Object Windows.Controls.RowDefinition; $row.Height='Auto';[void]$hdr.RowDefinitions.Add($row) }
$UI.header=$hdr
$ttl = New-Object Windows.Controls.StackPanel
$UI.title=New-Text 'مرصد الجهاز' 16 $C.accentText 'SemiBold' $false 'head';$UI.title.TextTrimming='None'
$ttl.Children.Add($UI.title) | Out-Null
$UI.sub = New-Text 'جارٍ الاتصال…' 12 $C.muted
$brand = New-Object Windows.Controls.StackPanel; $brand.Orientation = 'Horizontal'; $brand.VerticalAlignment = 'Center'
try {
    # الشعار رسم متجهي يتبع لوحة الهوية (الحلقة بلون --accent والنبضة بلون --warn) بدل صورة ثابتة
    $logoCanvas = New-Object Windows.Controls.Canvas; $logoCanvas.Width = 180; $logoCanvas.Height = 180
    $ring = New-Object Windows.Shapes.Ellipse; $ring.Width = 152; $ring.Height = 152; $ring.StrokeThickness = 14; $ring.StrokeDashCap = 'Round'
    $ring.StrokeDashArray = New-Object Windows.Media.DoubleCollection; $ring.StrokeDashArray.Add(30.7); $ring.StrokeDashArray.Add(3.4)
    $ring.RenderTransformOrigin = '0.5,0.5'; $ring.RenderTransform = New-Object Windows.Media.RotateTransform(-58)
    [Windows.Controls.Canvas]::SetLeft($ring, 14); [Windows.Controls.Canvas]::SetTop($ring, 14)
    $pulse = New-Object Windows.Shapes.Polyline; $pulse.StrokeThickness = 12; $pulse.StrokeLineJoin = 'Round'; $pulse.StrokeStartLineCap = 'Round'; $pulse.StrokeEndLineCap = 'Round'
    $pulse.Points = [Windows.Media.PointCollection]::Parse('30,94 60,94 75,58 97,128 112,80 120,94 150,94')
    $dot = New-Object Windows.Shapes.Ellipse; $dot.Width = 18; $dot.Height = 18
    [Windows.Controls.Canvas]::SetLeft($dot, 100.7); [Windows.Controls.Canvas]::SetTop($dot, 7.6)
    foreach ($shape in @($ring, $pulse, $dot)) { $logoCanvas.Children.Add($shape) | Out-Null }
    $logo = New-Object Windows.Controls.Viewbox; $logo.Width = 34; $logo.Height = 34; $logo.VerticalAlignment = 'Center'; $logo.FlowDirection = 'LeftToRight'; $logo.Child = $logoCanvas
    $UI.logoRing = $ring; $UI.logoPulse = $pulse; $UI.logoDot = $dot
    $ring.Stroke = Brush $C.accent; $pulse.Stroke = Brush $C.warn; $dot.Fill = Brush $C.warn
    $gap = New-Object Windows.Controls.Border; $gap.Width = 9
    $brand.Children.Add($logo) | Out-Null; $brand.Children.Add($gap) | Out-Null
} catch {}
$ttl.VerticalAlignment = 'Center'; $brand.HorizontalAlignment='Stretch'; $brand.Children.Add($ttl) | Out-Null
$UI.headerBrand=$brand
[Windows.Controls.Grid]::SetColumn($brand, 0); $hdr.Children.Add($brand) | Out-Null
$UI.pill = New-Object Windows.Controls.Border; $UI.pill.CornerRadius = 8; $UI.pill.Padding = '8,1,8,2'; $UI.pill.Margin = '0,0,8,0'; $UI.pill.VerticalAlignment = 'Center'
$UI.pillText = New-Text '—' 11 $C.pilltxt 'SemiBold'; $UI.pill.Child = $UI.pillText; $UI.pill.Background = Brush $C.muted
$UI.pill.MinWidth=42
$statusRow=New-Object Windows.Controls.Grid; $statusRow.Margin='0,8,0,0'
foreach($width in @('*','Auto')){$column=New-Object Windows.Controls.ColumnDefinition;$column.Width=$width;[void]$statusRow.ColumnDefinitions.Add($column)}
[void]$statusRow.Children.Add($UI.sub);[Windows.Controls.Grid]::SetColumn($UI.pill,1);[void]$statusRow.Children.Add($UI.pill)
[Windows.Controls.Grid]::SetRow($statusRow,1);[Windows.Controls.Grid]::SetColumnSpan($statusRow,2);[void]$hdr.Children.Add($statusRow)
$UI.headerStatus=$statusRow
$UI.adm = New-Text 'صلاحية الخادم: مسؤول' 11.5 $C.muted
$UI.adm.ToolTip = 'الخادم يعمل بصلاحية مسؤول؛ هذه معلومة تشغيل وليست تنبيهًا'; $UI.adm.Visibility = 'Collapsed';$UI.adm.Margin='0,0,0,8'
function New-ActionButton([string]$name, [bool]$toggle = $false) {
    $b = if ($toggle) { New-Object Windows.Controls.Primitives.ToggleButton } else { New-Object Windows.Controls.Button }
    $b.Cursor = 'Hand'; $b.Focusable = $true; $b.IsTabStop = $true; $b.MinHeight = 24
    $b.HorizontalContentAlignment = 'Stretch'; $b.VerticalContentAlignment = 'Center'
    [Windows.Automation.AutomationProperties]::SetName($b, $name)
    $targetType = if ($toggle) { 'ToggleButton' } else { 'Button' }
    $template = '<ControlTemplate TargetType="__TARGET__" xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"><Border x:Name="Frame" CornerRadius="8" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}" BorderThickness="{TemplateBinding BorderThickness}" Padding="{TemplateBinding Padding}"><ContentPresenter Content="{TemplateBinding Content}" ContentTemplate="{TemplateBinding ContentTemplate}" HorizontalAlignment="{TemplateBinding HorizontalContentAlignment}" VerticalAlignment="{TemplateBinding VerticalContentAlignment}"/></Border><ControlTemplate.Triggers><Trigger Property="IsKeyboardFocused" Value="True"><Setter TargetName="Frame" Property="BorderBrush" Value="#167CA5"/><Setter TargetName="Frame" Property="BorderThickness" Value="2"/></Trigger><Trigger Property="IsEnabled" Value="False"><Setter Property="Opacity" Value="1"/><Setter TargetName="Frame" Property="BorderBrush" Value="#7F8C8D"/><Setter TargetName="Frame" Property="BorderThickness" Value="1"/><Setter TargetName="Frame" Property="Cursor" Value="Arrow"/></Trigger></ControlTemplate.Triggers></ControlTemplate>'
    $b.Template = [Windows.Markup.XamlReader]::Parse($template.Replace('__TARGET__', $targetType))
    return $b
}
function New-IconBtn([string]$glyph) {
    $b = New-ActionButton $glyph; $b.Width = 26; $b.Height = 26; $b.Background = Brush $C.card; $b.BorderBrush=Brush $C.line;$b.BorderThickness=1; $b.Cursor = 'Hand'; $b.Margin = '2,0,0,0'
    $t = New-Text $glyph 13 $C.muted; $t.HorizontalAlignment = 'Center'; $t.VerticalAlignment = 'Center'; $t.FlowDirection = 'LeftToRight'; $b.Content = $t
    $b.Add_MouseEnter({ $this.Background = Brush $C.hover }); $b.Add_MouseLeave({ $this.Background = Brush $C.card })
    $b
}
$btnRow = New-Object Windows.Controls.StackPanel; $btnRow.Orientation = 'Horizontal'; $btnRow.VerticalAlignment = 'Center'
$UI.menuBtn = New-IconBtn '⋯'; $UI.closeBtn = New-IconBtn '✕'; [Windows.Automation.AutomationProperties]::SetName($UI.menuBtn, 'خيارات الويدجت'); [Windows.Automation.AutomationProperties]::SetName($UI.closeBtn, 'إخفاء الويدجت')
$btnRow.Children.Add($UI.menuBtn) | Out-Null; $btnRow.Children.Add($UI.closeBtn) | Out-Null
[Windows.Controls.Grid]::SetColumn($btnRow, 1); $hdr.Children.Add($btnRow) | Out-Null
$UI.headerButtons=$btnRow
$stack.Children.Add($hdr) | Out-Null
$UI.cpuAlert = New-Text '' 11.5 $C.warn; $UI.cpuAlert.TextWrapping = 'Wrap'; $UI.cpuAlert.Margin = '0,6,0,0'; $UI.cpuAlert.Visibility = 'Collapsed'; [Windows.Automation.AutomationProperties]::SetLiveSetting($UI.cpuAlert, 'Assertive'); $stack.Children.Add($UI.cpuAlert) | Out-Null
$UI.toast = New-Text '' 12 $C.ink; $UI.toast.TextWrapping = 'Wrap'; $UI.toast.TextTrimming = 'None'; $UI.toast.Margin='0,8,0,0'; $UI.toast.Visibility='Collapsed'; [Windows.Automation.AutomationProperties]::SetLiveSetting($UI.toast, 'Polite'); [void]$stack.Children.Add($UI.toast)   # قارئ الشاشة يعلن الإشعار (يختفي بعد 7 ثوانٍ)
$UI.appearanceWarning = New-Text '' 12 $C.warn; $UI.appearanceWarning.TextWrapping='Wrap'; $UI.appearanceWarning.TextTrimming='None'; $UI.appearanceWarning.Margin='0,8,0,0'; $UI.appearanceWarning.Visibility='Collapsed'; [Windows.Automation.AutomationProperties]::SetLiveSetting($UI.appearanceWarning, 'Polite'); [void]$stack.Children.Add($UI.appearanceWarning)
$stack.Children.Add((Gap 10)) | Out-Null

# ---- rings
function New-Ring([string]$label) {
    $wrap = New-Object Windows.Controls.StackPanel; $wrap.HorizontalAlignment = 'Center'
    $g = New-Object Windows.Controls.Grid; $g.Width = 68; $g.Height = 68; $g.FlowDirection = 'LeftToRight'
    # المسار قوس 270° يبدأ من أسفل اليسار كمقياس الواجهة (gauge في index.html) بدل حلقة كاملة
    $track = New-Object Windows.Shapes.Path; $track.StrokeThickness = 6; $track.StrokeStartLineCap = 'Round'; $track.StrokeEndLineCap = 'Round'; $track.Stroke = Brush $C.track; $track.Data = Ring-Arc 100
    $cv = New-Object Windows.Controls.Canvas; $cv.Width = 68; $cv.Height = 68
    $arc = New-Object Windows.Shapes.Path; $arc.StrokeThickness = 6; $arc.StrokeStartLineCap = 'Round'; $arc.StrokeEndLineCap = 'Round'; $arc.Stroke = Brush $C.accent
    $cv.Children.Add($arc) | Out-Null
    $ctr = New-Object Windows.Controls.StackPanel; $ctr.VerticalAlignment = 'Center'; $ctr.HorizontalAlignment = 'Center'
    $val = New-Text '—' 20 $C.ink 'SemiBold' $true; $val.HorizontalAlignment = 'Center'
    $unit = New-Text '' 12 $C.muted 'Normal' $true; $unit.HorizontalAlignment = 'Center'
    $ctr.Children.Add($val) | Out-Null; $ctr.Children.Add($unit) | Out-Null
    $trackCv = New-Object Windows.Controls.Canvas; $trackCv.Width = 68; $trackCv.Height = 68; $trackCv.Children.Add($track) | Out-Null
    $g.Children.Add($trackCv) | Out-Null; $g.Children.Add($cv) | Out-Null; $g.Children.Add($ctr) | Out-Null
    $lab = New-Text $label 12 $C.muted; $lab.HorizontalAlignment = 'Center'; $lab.Margin = '0,2,0,0'
    $wrap.Children.Add($g) | Out-Null; $wrap.Children.Add($lab) | Out-Null
    [Windows.Automation.AutomationProperties]::SetName($val, $label)   # قارئ الشاشة يقرأ «الاسم: القيمة والوحدة» من نص القيمة؛ Set-Ring تحدّثه
    @{ Root = $wrap; Arc = $arc; Val = $val; Unit = $unit; Grid = $g; Label = $label }
}
function Ring-Arc([double]$pct) {
    # قوس من 135° باتجاه عقارب الساعة بطول 270°×النسبة حول مركز (34,34) ونصف قطر 31
    $cx = 34; $cy = 34; $rad = 31
    $p = [Math]::Max(0.6, [Math]::Min(100, $pct)); $sweep = 270 * $p / 100
    $a0 = 135 * [Math]::PI / 180; $a1 = (135 + $sweep) * [Math]::PI / 180
    $fig = New-Object Windows.Media.PathFigure; $fig.StartPoint = [Windows.Point]::new($cx + $rad * [Math]::Cos($a0), $cy + $rad * [Math]::Sin($a0)); $fig.IsClosed = $false
    $seg = New-Object Windows.Media.ArcSegment
    $seg.Point = [Windows.Point]::new($cx + $rad * [Math]::Cos($a1), $cy + $rad * [Math]::Sin($a1)); $seg.Size = [Windows.Size]::new($rad, $rad)
    $seg.SweepDirection = 'Clockwise'; $seg.IsLargeArc = ($sweep -gt 180)
    $fig.Segments.Add($seg); $geo = New-Object Windows.Media.PathGeometry; $geo.Figures.Add($fig); $geo.Freeze(); $geo
}
function Set-Ring($r, [double]$pct, [string]$lvl, [string]$val, [string]$unit, [string]$tip) {
    $r.Arc.Data = Ring-Arc ([Math]::Min(99.6, $pct)); $r.Arc.Stroke = if ($lvl -in @('warn','bad')) { Level-Brush $lvl } else { Brush $C.accent }; $r.Val.Text = $val; $r.Unit.Text = $unit; $r.Root.ToolTip = $tip
    [Windows.Automation.AutomationProperties]::SetName($r.Val, (($r.Label + ': ' + $val + ' ' + $unit).Trim())); [Windows.Automation.AutomationProperties]::SetHelpText($r.Val, $tip)
}
$ringsGrid = New-Object Windows.Controls.Primitives.UniformGrid; $ringsGrid.Columns = 3
$UI.rCommit = New-Ring 'التزام الذاكرة'; $UI.rRam = New-Ring 'الذاكرة الفعلية'; $UI.rDisk = New-Ring 'القرص C'
foreach ($r in @($UI.rCommit, $UI.rRam, $UI.rDisk)) { $ringsGrid.Children.Add($r.Root) | Out-Null }
$stack.Children.Add($ringsGrid) | Out-Null
$stack.Children.Add((Gap 10)) | Out-Null

# ---- history chart
$chartCard = New-Card 8
$chartStack = New-Object Windows.Controls.StackPanel
$chHead = New-Object Windows.Controls.Grid
foreach ($w in @('*', 'Auto')) { $cd = New-Object Windows.Controls.ColumnDefinition; $cd.Width = $w; $chHead.ColumnDefinitions.Add($cd) }
$chHead.Children.Add((New-Text 'آخر 30 دقيقة' 11 $C.muted)) | Out-Null
$leg = New-Object Windows.Controls.StackPanel; $leg.Orientation = 'Horizontal'
foreach ($it in @(@('التزام', $C.accentText), @('فعلية', $C.violet))) {
    $dot = New-Object Windows.Shapes.Ellipse; $dot.Width = 7; $dot.Height = 7; $dot.Fill = Brush $it[1]; $dot.Margin = '8,0,3,0'; $dot.VerticalAlignment = 'Center'
    $leg.Children.Add($dot) | Out-Null; $leg.Children.Add((New-Text $it[0] 11.5 $C.muted)) | Out-Null
}
[Windows.Controls.Grid]::SetColumn($leg, 1); $chHead.Children.Add($leg) | Out-Null
$chartStack.Children.Add($chHead) | Out-Null
$UI.chart = New-Object Windows.Controls.Canvas; $UI.chart.HorizontalAlignment='Stretch'; $UI.chart.Height = 96; $UI.chart.FlowDirection = 'LeftToRight'; $UI.chart.Margin = '0,6,0,0'; $UI.chart.ClipToBounds = $true
$UI.chart.Add_SizeChanged({ $script:chartSig=$null; $script:lastStamp=-1 })
$chartStack.Children.Add($UI.chart) | Out-Null
$chartCard.Child = $chartStack; $stack.Children.Add($chartCard) | Out-Null
$stack.Children.Add((Gap 8)) | Out-Null

# ---- composition bar + chips
$compCard = New-Card 8
$compStack = New-Object Windows.Controls.StackPanel
$UI.compTitle = New-Text 'توزيع الالتزام' 12 $C.muted 'Normal' $false 'head'
$compStack.Children.Add($UI.compTitle) | Out-Null
$UI.bar = New-Object Windows.Controls.Grid; $UI.bar.Height = 9; $UI.bar.Margin = '0,6,0,0'; $UI.bar.FlowDirection = 'LeftToRight'
$UI.barCols = @(); $barColors = @($C.accent, $C.seg2, $C.seg3, $C.seg4)
for ($i = 0; $i -lt 4; $i++) {
    $cd = New-Object Windows.Controls.ColumnDefinition; $cd.Width = [Windows.GridLength]::new(1, 'Star'); $UI.bar.ColumnDefinitions.Add($cd); $UI.barCols += $cd
    $rc = New-Object Windows.Shapes.Rectangle; $rc.Fill = Brush $barColors[$i]; $rc.Margin = if ($i -gt 0) { '1,0,0,0' } else { '0' }
    [Windows.Controls.Grid]::SetColumn($rc, $i); $UI.bar.Children.Add($rc) | Out-Null
}
$UI.bar.ClipToBounds=$true
$UI.bar.Add_SizeChanged({ $this.Clip = New-Object Windows.Media.RectangleGeometry ([Windows.Rect]::new(0,0,$this.ActualWidth,$this.ActualHeight),4.5,4.5) })
$compStack.Children.Add($UI.bar) | Out-Null
$UI.compLegend = New-Text '' 12 $C.muted; $UI.compLegend.Margin = '0,5,0,0'; $UI.compLegend.TextWrapping = 'Wrap'; $UI.compLegend.TextTrimming = 'None'
$compStack.Children.Add($UI.compLegend) | Out-Null
$compCard.Child = $compStack; $stack.Children.Add($compCard) | Out-Null
$stack.Children.Add((Gap 8)) | Out-Null

$chips = New-Object Windows.Controls.Primitives.UniformGrid; $chips.Columns = 3
function New-Chip([string]$label) {
    $b = New-Card 7; $b.Margin = '0,0,0,0'
    $sp = New-Object Windows.Controls.StackPanel; $sp.HorizontalAlignment = 'Center'
    $v = New-Text '—' 17 $C.ink 'SemiBold' $true; $v.HorizontalAlignment = 'Center'
    $l = New-Text $label 12 $C.muted; $l.HorizontalAlignment = 'Center'; $l.TextAlignment = 'Center'
    $l.TextWrapping = 'Wrap'; $l.TextTrimming = 'None'   # «عمليات Claude» أعرض من ثلث البطاقة بخط Zain؛ يلتفّ على سطرين بدل البتر
    $sp.Children.Add($v) | Out-Null; $sp.Children.Add($l) | Out-Null; $b.Child = $sp
    [Windows.Automation.AutomationProperties]::SetName($v, $label)
    @{ Root = $b; Val = $v; Label = $label }
}
function Set-Chip($chip, [string]$text) { $chip.Val.Text = $text; [Windows.Automation.AutomationProperties]::SetName($chip.Val, ($chip.Label + ': ' + $text)) }
$UI.cClaude = New-Chip 'عمليات Claude'; $UI.cShell = New-Chip 'شلات'; $UI.cConhost = New-Chip 'conhost'
$UI.cClaude.Root.Margin = '0,0,3,0'; $UI.cConhost.Root.Margin = '3,0,0,0'
foreach ($chip in @($UI.cClaude, $UI.cShell, $UI.cConhost)) { $chips.Children.Add($chip.Root) | Out-Null }
$stack.Children.Add($chips) | Out-Null
$stack.Children.Add((Gap 12)) | Out-Null

# ---- cleanup panel with real switches
$cleanHead = New-Object Windows.Controls.Grid
foreach ($w in @('*', 'Auto')) { $cd = New-Object Windows.Controls.ColumnDefinition; $cd.Width = $w; $cleanHead.ColumnDefinitions.Add($cd) }
$cleanHead.Children.Add((New-Text 'التنظيف التلقائي' 13 $C.ink 'SemiBold' $false 'head')) | Out-Null
$UI.cleanHint = New-Text 'المفتاح للجدولة' 12 $C.muted; [Windows.Controls.Grid]::SetColumn($UI.cleanHint, 1); $cleanHead.Children.Add($UI.cleanHint) | Out-Null
$stack.Children.Add($cleanHead) | Out-Null
$stack.Children.Add((Gap 6)) | Out-Null

function New-Switch {
    $b = New-ActionButton 'الجدولة التلقائية' $true; $b.Width = 42; $b.Height = 24; $b.Cursor = 'Hand'; $b.FlowDirection = 'LeftToRight'; $b.VerticalAlignment = 'Center'
    $k = New-Object Windows.Shapes.Ellipse; $k.Width = 18; $k.Height = 18; $k.Fill = Brush $C.knob; $k.VerticalAlignment = 'Center'; $k.Margin = '3,0,3,0'; $b.Content = $k
    $b
}
function Set-Switch($b, [bool]$on) { $b.Background = if ($on) { Brush $C.accent } else { Brush $C.swoff }; $b.IsChecked = $on; $b.Content.HorizontalAlignment = if ($on) { 'Right' } else { 'Left' } }

$TIERS = @(@('daily', 'يومي'), @('weekly', 'أسبوعي'), @('monthly', 'شهري'), @('quarterly', 'تقرير ربع سنوي'))
foreach ($t in $TIERS) {
    $key = $t[0]
    $row = New-Card 7; $row.Margin = '0,0,0,4'
    $g = New-Object Windows.Controls.Grid
    foreach ($w in @('Auto', '*', 'Auto')) { $cd = New-Object Windows.Controls.ColumnDefinition; $cd.Width = $w; $g.ColumnDefinitions.Add($cd) }
    $sw = New-Switch; [Windows.Automation.AutomationProperties]::SetName($sw, ('جدولة التنظيف ' + $t[1])); $sw.Tag = $key; $sw.Margin = '0,0,0,0'
    [Windows.Controls.Grid]::SetColumn($sw, 0); $g.Children.Add($sw) | Out-Null
    $info = New-Object Windows.Controls.StackPanel; $info.Margin = '10,0,8,0'; $info.VerticalAlignment = 'Center'
    $nm = New-Text ('تنظيف ' + $t[1]) 14 $C.ink 'SemiBold' $false 'head'
    $s1 = New-Text '' 12 $C.muted; $s2 = New-Text '' 12 $C.muted
    $info.Children.Add($nm) | Out-Null; $info.Children.Add($s1) | Out-Null; $info.Children.Add($s2) | Out-Null
    [Windows.Controls.Grid]::SetColumn($info, 1); $g.Children.Add($info) | Out-Null
    $run = New-ActionButton ('تشغيل تنظيف ' + $t[1]); $run.Padding = '10,3,10,4'; $run.Cursor = 'Hand'; $run.VerticalAlignment = 'Center'; $run.Tag = $key
    $run.Background = Brush (Alpha $C.accent '2A'); $run.BorderBrush = Brush (Alpha $C.accent '66'); $run.BorderThickness = 1
    $rt = New-Text '▶ تشغيل' 12 $C.accentText 'SemiBold'; $run.Content = $rt
    [Windows.Controls.Grid]::SetColumn($run, 2); $g.Children.Add($run) | Out-Null
    $row.Child = $g; $stack.Children.Add($row) | Out-Null
    $UI.tiers[$key] = @{ Switch = $sw; Name = $nm; S1 = $s1; S2 = $s2; Run = $run; RunText = $rt; Label = $t[1]; Paused = $false }
}

# ---- inline confirm strip (hidden until ▶ is pressed)
$UI.confirm = New-Object Windows.Controls.Border; $UI.confirm.Visibility = 'Collapsed'; $UI.confirm.CornerRadius = 12; $UI.confirm.Padding = 10
$UI.confirm.Background = Brush '#33F5B841'; $UI.confirm.BorderBrush = Brush '#88F5B841'; $UI.confirm.BorderThickness = 1; $UI.confirm.Margin = '0,0,0,6'
$cf = New-Object Windows.Controls.StackPanel
$UI.confirmText = New-Text '' 12 $C.ink; $UI.confirmText.TextWrapping = 'Wrap'; $UI.confirmText.TextTrimming = 'None'; [Windows.Automation.AutomationProperties]::SetLiveSetting($UI.confirmText, 'Assertive')
$cfBtns = New-Object Windows.Controls.StackPanel; $cfBtns.Orientation = 'Horizontal'; $cfBtns.Margin = '0,8,0,0'
function New-TextBtn([string]$text, [string]$bg, [string]$fg) {
    $b = New-ActionButton $text; $b.Padding = '14,4,14,5'; $b.Cursor = 'Hand'; $b.Background = Brush $bg; $b.Margin = '0,0,0,0'
    $b.Content = New-Text $text 12 $fg 'SemiBold'; $b
}
$UI.cfReal = New-TextBtn 'تنفيذ فعلي' $C.warn $C.onWarn; $UI.cfDry = New-TextBtn 'تجربة بلا حذف' $C.hover $C.ink; $UI.cfNo = New-TextBtn 'إلغاء' $C.card $C.muted
$UI.cfDry.Margin = '8,0,0,0'; $UI.cfNo.Margin = '8,0,0,0'
foreach ($b in @($UI.cfReal, $UI.cfDry, $UI.cfNo)) { $cfBtns.Children.Add($b) | Out-Null }
$cf.Children.Add($UI.confirmText) | Out-Null; $cf.Children.Add($cfBtns) | Out-Null; $UI.confirm.Child = $cf
$stack.Children.Add($UI.confirm) | Out-Null

# ---- memory trim button (PC Manager style, on demand)
$stack.Children.Add((Gap 2)) | Out-Null
$UI.trim = New-ActionButton 'تنظيف الذاكرة الآن'; $UI.trim.Padding = '10,7,10,8'; $UI.trim.Cursor = 'Hand'
$UI.trim.Background = Brush (Alpha $C.accent '26'); $UI.trim.BorderBrush = Brush (Alpha $C.accent '70'); $UI.trim.BorderThickness = 1
$UI.trimText = New-Text '⚡ تنظيف الذاكرة الآن' 12 $C.accentText 'SemiBold'; $UI.trimText.HorizontalAlignment = 'Center'; $UI.trimCd = New-Text '' 12 $C.muted; $UI.trimCd.HorizontalAlignment = 'Center'; $UI.trimCd.Margin = '0,2,0,0'
$tsp = New-Object Windows.Controls.StackPanel; [void]$tsp.Children.Add($UI.trimText); [void]$tsp.Children.Add($UI.trimCd); $UI.trim.Content = $tsp
$stack.Children.Add($UI.trim) | Out-Null
$stack.Children.Add((Gap 6)) | Out-Null
# ---- footer
$foot = New-Object Windows.Controls.Grid
foreach ($w in @('*', 'Auto')) { $cd = New-Object Windows.Controls.ColumnDefinition; $cd.Width = $w; $foot.ColumnDefinitions.Add($cd) }
$UI.guard = New-Text '' 12 $C.muted; $UI.guard.MaxWidth = 170; $foot.Children.Add($UI.guard) | Out-Null
$UI.open = New-ActionButton 'فتح التطبيق أو إظهار نافذته'; $UI.open.Content = 'فتح لوحة التحكم'; $UI.open.Foreground = Brush $C.accentText; $UI.open.Background = Brush '#00FFFFFF'; $UI.open.BorderThickness = 0; $UI.open.FontSize = 12; $UI.open.FontFamily = $FONTS.list; $UI.open.HorizontalContentAlignment = 'Center'; $UI.open.MinHeight = 32; $UI.open.MinWidth = 0; $UI.open.MaxWidth = [double]::PositiveInfinity; $script:txtOpen = 'فتح لوحة التحكم'; $script:txtClose = 'إظهار لوحة التحكم'
[Windows.Controls.Grid]::SetColumn($UI.open, 1); $foot.Children.Add($UI.open) | Out-Null
$stack.Children.Add((Gap 4)) | Out-Null; $stack.Children.Add($foot) | Out-Null

# Approved reference composition: stacked readings, primary column, action column, open action and footer.
$fullTierRows=@($TIERS|ForEach-Object {$UI.tiers[$_[0]].Switch.Parent.Parent})
$stack.Children.Clear()
$compact = New-Object Windows.Controls.StackPanel; $compact.Margin = '0,10,0,10'
$UI.compactBars = @{}
$UI.approvedReadingRows=@()
foreach ($spec in @(@('cpu','المعالج'), @('ram','الذاكرة الفعلية'), @('disk','المساحة المتاحة C'))) {
    $row=New-Object Windows.Controls.Grid; $row.Margin='0,0,0,2'
    foreach($width in @('*','Auto')){$cd=New-Object Windows.Controls.ColumnDefinition;$cd.Width=$width;[void]$row.ColumnDefinitions.Add($cd)}
    $label=New-Text $spec[1] 12 $C.muted
    $value=New-Text '—' 14 $C.ink 'SemiBold' $true; $value.VerticalAlignment='Center'
    [Windows.Controls.Grid]::SetColumn($value,1);[void]$row.Children.Add($label);[void]$row.Children.Add($value);[void]$compact.Children.Add($row)
    $UI[('compact'+$spec[0])]=$value;$UI.approvedReadingRows+=@{Root=$row;Label=$label;Value=$value}
}
$UI.compactAlert=New-Text '' 12 $C.muted;$UI.compactAlert.TextWrapping='Wrap';$UI.compactAlert.Margin='0,4,0,0';[void]$compact.Children.Clear();[void]$compact.Children.Add($UI.compactAlert)
foreach($n in @($hdr,$UI.cpuAlert,$UI.appearanceWarning)){[void]$stack.Children.Add($n)}
$UI.historyNotice=New-Text '' 12 $C.muted;$UI.historyNotice.TextWrapping='Wrap';$UI.historyNotice.Visibility='Collapsed';[void]$stack.Children.Add($UI.historyNotice)
$UI.restoredBody=New-Object Windows.Controls.Grid
$UI.restoredMetrics=New-Object Windows.Controls.StackPanel;$UI.restoredActions=New-Object Windows.Controls.StackPanel
foreach($n in @($compact,$ringsGrid,$chartCard,$compCard,$chips)){if($n.Parent -is [Windows.Controls.Panel]){$n.Parent.Children.Remove($n)};$n.Margin='0,0,0,8';[void]$UI.restoredMetrics.Children.Add($n)}
# معلومة الصلاحية تبقى في تفاصيل اللوحة لا في الرأس (مطوية ما لم يعمل الخادم كمسؤول)
[void]$UI.restoredMetrics.Children.Insert(0,$UI.adm)
[void]$UI.restoredActions.Children.Add($cleanHead)
foreach($n in $fullTierRows){$n.Padding='6,6,6,6';$n.Margin='0,0,0,8';$n.MinHeight=60;[void]$UI.restoredActions.Children.Add($n)}
[void]$UI.restoredActions.Children.Add($UI.trim)
# The approved full-width open action belongs after maintenance, not cramped into the guard footer.
$foot.Children.Remove($UI.open);[Windows.Controls.Grid]::SetColumn($UI.open,0);$UI.open.Margin='0,8,0,0';$UI.open.BorderThickness=1;$UI.open.BorderBrush=Brush $C.line;$UI.open.Background=Brush $C.card
[void]$UI.restoredActions.Children.Add($UI.open)
$UI.guard.MaxWidth=[double]::PositiveInfinity;$foot.Margin='0,4,0,0'
[void]$UI.restoredBody.Children.Add($UI.restoredMetrics);[void]$UI.restoredBody.Children.Add($UI.restoredActions)
# Reference confirmation is an overlay: all original confirmation handlers/buttons remain attached.
$UI.confirm.Margin=16;$UI.confirm.VerticalAlignment='Center';$UI.confirm.Background=Brush $C.card;$UI.confirm.BorderBrush=Brush $C.warn;[Windows.Controls.Panel]::SetZIndex($UI.confirm,10);[void]$UI.restoredBody.Children.Add($UI.confirm)
$UI.toast.Margin=16;$UI.toast.Padding='12,9,12,9';$UI.toast.VerticalAlignment='Center';$UI.toast.HorizontalAlignment='Stretch';$UI.toast.TextAlignment='Center';$UI.toast.Background=Brush $C.card;[Windows.Controls.Panel]::SetZIndex($UI.toast,11);if($UI.toast.Parent -is [Windows.Controls.Panel]){$UI.toast.Parent.Children.Remove($UI.toast)};[void]$UI.restoredBody.Children.Add($UI.toast)
$UI.toast.Background=Brush $C.line;$UI.toast.Foreground=Brush $C.ink;$UI.toast.FontWeight='SemiBold'
[void]$stack.Children.Add($UI.restoredBody);[void]$stack.Children.Add($foot)
function Set-RestoredLayout([double]$height=0){
 if($height -le 0){$height=if($script:widgetWorkHeightOverride){$script:widgetWorkHeightOverride}elseif(Get-Command Get-WidgetWorkArea -ErrorAction SilentlyContinue){(Get-WidgetWorkArea).Height}else{[Windows.SystemParameters]::WorkArea.Height}}
 $natural=56.0;foreach($node in @($UI.restoredMetrics,$UI.restoredActions)+@($stack.Children|Where-Object {$_ -ne $UI.restoredBody})){$node.Measure([Windows.Size]::new(294,[double]::PositiveInfinity));$natural+=$node.DesiredSize.Height};$script:restoredPortraitHeight=$natural
 # Same reference breakpoint:350DIPportrait at1150DIPusableheight,660DIPtwo columns below it.
 $wide=$height -lt 1150 -or $height -lt ($natural+12);$width=if($wide){660}else{350};if($win.Width -ne $width){$win.MinWidth=0;$win.MaxWidth=[double]::PositiveInfinity;$win.Width=$width;$win.MinWidth=$width;$win.MaxWidth=$width}
 $grid=$UI.restoredBody;$grid.ColumnDefinitions.Clear();$grid.RowDefinitions.Clear()
 if($wide){foreach($x in 1..2){$c=New-Object Windows.Controls.ColumnDefinition;$c.Width='*';[void]$grid.ColumnDefinitions.Add($c)};$r=New-Object Windows.Controls.RowDefinition;$r.Height='Auto';[void]$grid.RowDefinitions.Add($r);[Windows.Controls.Grid]::SetColumn($UI.restoredMetrics,0);[Windows.Controls.Grid]::SetRow($UI.restoredMetrics,0);[Windows.Controls.Grid]::SetColumn($UI.restoredActions,1);[Windows.Controls.Grid]::SetRow($UI.restoredActions,0);$UI.restoredMetrics.Margin='0,0,14,0';$UI.chart.Height=86}
 else{$c=New-Object Windows.Controls.ColumnDefinition;$c.Width='*';[void]$grid.ColumnDefinitions.Add($c);foreach($x in 1..2){$r=New-Object Windows.Controls.RowDefinition;$r.Height='Auto';[void]$grid.RowDefinitions.Add($r)};[Windows.Controls.Grid]::SetColumn($UI.restoredMetrics,0);[Windows.Controls.Grid]::SetRow($UI.restoredMetrics,0);[Windows.Controls.Grid]::SetColumn($UI.restoredActions,0);[Windows.Controls.Grid]::SetRow($UI.restoredActions,1);$UI.restoredMetrics.Margin='0';$UI.chart.Height=92}
 [Windows.Controls.Grid]::SetColumn($UI.confirm,0);[Windows.Controls.Grid]::SetColumnSpan($UI.confirm,2);[Windows.Controls.Grid]::SetRow($UI.confirm,0);[Windows.Controls.Grid]::SetRowSpan($UI.confirm,2);[Windows.Controls.Grid]::SetColumn($UI.toast,0);[Windows.Controls.Grid]::SetColumnSpan($UI.toast,2);[Windows.Controls.Grid]::SetRow($UI.toast,0);[Windows.Controls.Grid]::SetRowSpan($UI.toast,2);$chartCard.Visibility='Visible';$script:chartSig=$null
}
$script:alertSeen = @{}; $script:alertSeeded = $false; $script:lastCompactAlert = ''
function Notify-Important([string]$message) {
    if ($script:S.muteAlerts -or -not $script:tray -or -not $script:tray.Visible) { return }
    try {
        $script:tray.BalloonTipTitle = 'مرصد الجهاز'
        $script:tray.BalloonTipText = $message.Substring(0, [Math]::Min(240, $message.Length))
        $script:tray.BalloonTipIcon = 'Warning'
        $script:tray.ShowBalloonTip(5000)
    } catch { Log ('notification: ' + $_.Exception.Message) }
}
function Update-Compact($d) {
    if (-not $d) { return }
    $m = $d.m; $cpu = $d.cpuNow
    $age = if ($script:sync.lastAt -and $script:sync.lastAt -ne [datetime]::MinValue) { [int]((Get-Date) - $script:sync.lastAt).TotalSeconds } else { $null }
    $sourceAge = $null
    if ($d.now) { try { $sourceAge = [Math]::Max(0, ((Get-Date) - [datetime]::Parse([string]$d.now)).TotalSeconds) } catch {} }
    $fresh = ($null -ne $age -and $age -lt 30 -and $null -ne $sourceAge -and $sourceAge -lt 30 -and -not $script:sync.err)
    $hasCpu = ($cpu -and $null -ne $cpu.pct -and $null -ne $cpu.age -and $null -ne $sourceAge -and ([double]$cpu.age + $sourceAge) -lt 180)
    $hasRam = ($null -ne $m.ramPct); $hasDisk = ($null -ne $m.diskFreeGB)
    # Expanded details use the same source/receipt decision as the compact view.
    if ($UI.historyNotice) {
        $UI.historyNotice.Text=if(-not $fresh){'آخر قراءة محفوظة — البيانات قديمة'}elseif(-not $hasCpu -or -not $hasRam -or -not $hasDisk){'تغطية القياسات غير مكتملة — بعض القراءات غير متاحة'}else{''}
        $UI.historyNotice.Visibility=if($UI.historyNotice.Text){'Visible'}else{'Collapsed'}
    }
    if (-not $fresh) { foreach($ring in @($UI.rCommit,$UI.rRam,$UI.rDisk)){if($ring -and $ring.Arc){$ring.Arc.Stroke=Brush $C.muted}} }
    foreach($spec in @(@('rCommit','commitPct'),@('rRam','ramPct'),@('rDisk','diskFreeGB'))){$ring=$UI[$spec[0]];if($ring -and $ring.Arc -and $null -eq $m.($spec[1])){$ring.Arc.Stroke=Brush $C.muted;$ring.Val.Text='—'}}
    $UI.compactcpu.Text = if ($hasCpu) { ('{0:N0}%' -f $cpu.pct) } else { '—' }
    $UI.compactram.Text = if ($null -ne $m.ramPct) { ('{0:N0}%' -f $m.ramPct) } else { '—' }
    $UI.compactdisk.Text = if ($null -ne $m.diskFreeGB) { ('{0:N0} GB' -f $m.diskFreeGB) } else { '—' }
    $UI.compactdisk.ToolTip = if ($null -ne $m.diskFreeGB) { ('المساحة المتاحة: {0:N2} GB' -f $m.diskFreeGB) } else { 'قياس المساحة غير متاح' }
    if ($UI.compactBars -and $UI.compactBars.Count -gt 0) {
        $ratios = @{ cpu=if ($hasCpu) {[double]$cpu.pct} else {0}; ram=if ($null -ne $m.ramPct) {[double]$m.ramPct} else {0}; disk=if ($null -ne $m.diskPct) {100-[double]$m.diskPct} else {0} }
        foreach ($key in @('cpu','ram','disk')) {
            $bar=$UI.compactBars[$key]; $pct=[Math]::Max(0,[Math]::Min(100,$ratios[$key])); $bar.Fill.Width=[Math]::Max(0,$bar.Track.ActualWidth * $pct / 100)
            $bar.Fill.Background=if (-not $fresh -or ($key -eq 'cpu' -and -not $hasCpu)) { Brush $C.muted } elseif ($key -eq 'ram') { Brush $C.violet } else { Brush $C.accent }
        }
    }
    $UI.sub.Text = if (-not $fresh) { 'بيانات قديمة أو اتصال منقطع' } else { ('اتصال {0} ث · عمر القياس {1:N0} ث' -f $age, $sourceAge) }
    $UI.sub.ToolTip = if (-not $fresh) { 'قياسات قديمة أو اتصال منقطع؛ افتح اللوحة للتحديث. آخر عينة: ' + [string]$d.now } else { $UI.sub.Text }
    $alert = if (-not $fresh) { 'تحتاج البيانات إلى تحديث؛ القيم ليست حالة حية مؤكدة.' } elseif (-not $hasCpu) { 'قياس المعالج غير متاح أو قديم.' } elseif (-not $hasRam -or -not $hasDisk) { 'بعض القياسات غير متاحة؛ راجع التغطية في اللوحة.' } elseif ($m.diskFreeGB -lt $script:THR.dB) { 'المساحة الحرة منخفضة؛ عاين المساحة قبل التنظيف.' } elseif ($m.ramPct -ge $script:THR.ramB) { 'ضغط مرتفع على الذاكرة؛ راجع التطبيقات.' } else { 'القياسات المتاحة ضمن الحدود؛ التفاصيل في اللوحة.' }
    $important = $fresh -and (($hasDisk -and $m.diskFreeGB -lt $script:THR.dB) -or ($hasRam -and $m.ramPct -ge $script:THR.ramB))
    $UI.compactAlert.Text = $alert; $UI.compactAlert.Foreground = if ($important) { Brush $C.warn } else { Brush $C.muted }
    # Announce only a new important state; the same deduplication and mute also
    # apply to the native tray notification. Windows may suppress it in Focus Assist.
    if ($script:alertSeeded -and $important -and -not $script:S.muteAlerts -and $script:lastCompactAlert -ne $alert -and (-not $script:alertSeen.ContainsKey($alert) -or ((Get-Date) - $script:alertSeen[$alert]).TotalMinutes -ge 30)) { $script:alertSeen[$alert] = Get-Date; $UI.sub.Text = 'تنبيه جديد · ' + $alert; Notify-Important $alert }
    if (-not $fresh -or -not $hasCpu -or -not $hasRam -or -not $hasDisk) { $UI.pillText.Text = if (-not $fresh) { 'قديم' } else { 'ناقص' }; $UI.pill.ToolTip = 'بيانات قديمة أو تغطية قياسات غير مكتملة'; $UI.pill.Background = Brush $C.muted }
    $script:lastCompactAlert = $alert; $script:alertSeeded = $true
}

# ------------------------------------------------------------------ backend access
$script:pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $script:pyw) { $script:pyw = "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe" }
$script:tok = (Get-Content "$Root\.token" -ErrorAction SilentlyContinue | Select-Object -First 1)
$script:sync = [hashtable]::Synchronized(@{ data = $null; err = $null; stamp = 0; lastAt = [datetime]::MinValue; appearance=$null })
$script:bgPost = {
    param($tok, $path, $body)
    $uri = "http://127.0.0.1:47832$path"; $h = @{ 'X-Token' = $tok }
    try {
        Invoke-RestMethod $uri -Method Post -Headers $h -ContentType 'application/json' -Body ($body | ConvertTo-Json -Compress) -TimeoutSec 6 -NoProxy
    } catch {
        # destructive calls answer 428 + a one-time token; the user already confirmed in the widget dialog, so resend once with it
        $resp = $_.Exception.Response
        if (-not $resp -or [int]$resp.StatusCode -ne 428) { throw }
        $j = $null; try { $j = $_.ErrorDetails.Message | ConvertFrom-Json } catch {}
        if (-not $j -or -not $j.confirm) { throw }
        $b2 = @{}; foreach ($k in $body.Keys) { $b2[$k] = $body[$k] }; $b2.confirm = $j.confirm
        Invoke-RestMethod $uri -Method Post -Headers $h -ContentType 'application/json' -Body ($b2 | ConvertTo-Json -Compress) -TimeoutSec 6 -NoProxy
    }
}
$script:bgJobs = New-Object Collections.ArrayList
$bgTimer = New-Object Windows.Threading.DispatcherTimer; $bgTimer.Interval = [TimeSpan]::FromMilliseconds(150)
$bgTimer.Add_Tick({
    foreach ($j in @($script:bgJobs)) {
        if (-not $j.ar.IsCompleted) { continue }
        $script:bgJobs.Remove($j)
        $res = $null; $err = $null
        try {
            $res = @($j.ps.EndInvoke($j.ar))[0]
            if ($j.ps.HadErrors) { $err = [string]$j.ps.Streams.Error[0] }
        } catch { $err = $_.Exception.Message } finally { $j.ps.Dispose() }
        try { if ($null -ne $err) { & $j.err $err } else { & $j.ok $res } } catch { Log ('bg callback: ' + $_.Exception.Message) }
    }
    if ($script:bgJobs.Count -eq 0) { $this.Stop() }
})
function Start-Bg([string]$path, $body, [scriptblock]$ok, [scriptblock]$err) {
    $ps = [powershell]::Create(); [void]$ps.AddScript($script:bgPost).AddArgument($script:tok).AddArgument($path).AddArgument($body)
    [void]$script:bgJobs.Add(@{ ps = $ps; ar = $ps.BeginInvoke(); ok = $ok; err = $err })
    $bgTimer.Start()
}
$poll = {
    param($sync, $Root, $pyw)
    $tok = (Get-Content "$Root\.token" | Select-Object -First 1); $h = @{ 'X-Token' = $tok }; $lastStart = [datetime]::MinValue
    while ($true) {
        try { $sync.data = Invoke-RestMethod 'http://127.0.0.1:47832/api/mini' -Headers $h -TimeoutSec 4 -NoProxy; $sync.err = $null; $sync.lastAt = Get-Date; $sync.stamp = $sync.stamp + 1 }
        catch {
            $sync.err = $_.Exception.Message
            # الخلفية (الوكيل ثم الخادم) تعمل بصلاحية مسؤول من مهمة مجدولة؛ الويجت يطلبها عند الإقلاع وعند أي انقطاع (تجاهل التكرار مضمون في المهمة)
            if (([datetime]::Now - $lastStart).TotalSeconds -gt 45) { $lastStart = [datetime]::Now; try { Start-ScheduledTask -TaskName 'MonitorWidget-Agent' -ErrorAction Stop } catch {} }
        }
        Start-Sleep -Seconds 2
    }
}
$script:ps = [powershell]::Create(); [void]$script:ps.AddScript($poll).AddArgument($script:sync).AddArgument($Root).AddArgument($script:pyw); [void]$script:ps.BeginInvoke()
$appearancePoll = {
    param($sync, $Root)
    $token = Get-Content -LiteralPath (Join-Path $Root '.token') | Select-Object -First 1
    while ($true) {
        try { $sync.appearance = Invoke-RestMethod 'http://127.0.0.1:47832/api/appearance' -Headers @{ 'X-Token'=$token } -TimeoutSec 1 -NoProxy } catch {}
        Start-Sleep -Milliseconds 650
    }
}
$script:appearancePs = [powershell]::Create(); [void]$script:appearancePs.AddScript($appearancePoll).AddArgument($script:sync).AddArgument($Root); [void]$script:appearancePs.BeginInvoke()

# ------------------------------------------------------------------ rendering
function Ago($iso) {
    if (-not $iso) { return 'لم تعمل بعد' }
    $m = [int]((Get-Date) - [datetime]::Parse([string]$iso)).TotalMinutes
    if ($m -lt 1) { 'الآن' } elseif ($m -lt 60) { "قبل $m د" } elseif ($m -lt 1440) { "قبل $([int]($m / 60)) س" } else { "قبل $([int]($m / 1440)) يوم" }
}
function Fmt-Bytes($n) { $n = [double]$n; if ($n -ge 1GB) { '{0:N2} GB' -f ($n / 1GB) } elseif ($n -ge 1MB) { '{0:N1} MB' -f ($n / 1MB) } elseif ($n -ge 1KB) { '{0:N0} KB' -f ($n / 1KB) } else { "$([int]$n) B" } }

function Draw-Chart($commit, $ram) {
    $cv = $UI.chart; $cv.Children.Clear(); $w = $cv.ActualWidth; $h = $cv.ActualHeight
    if ($w -le 48 -or $h -le 24) { return }
    $left=32.0; $right=$w-8.0; $plotBottom=$h-18.0; $plotTop=4.0
    $commit = @($commit); $ram = @($ram); $n = $commit.Count
    if ($n -lt 2) { $t = New-Text 'جارٍ جمع البيانات…' 11 $C.muted; [Windows.Controls.Canvas]::SetLeft($t, $left + ($right-$left) / 2 - 50); [Windows.Controls.Canvas]::SetTop($t, $h / 2 - 8); $cv.Children.Add($t) | Out-Null; return }
    $all = $commit + $ram; $lo = [Math]::Max(0, [Math]::Floor((($all | Measure-Object -Minimum).Minimum - 6) / 10) * 10); $hi = 100
    $Y = { param($v) $plotBottom - ([double]$v - $lo) / ($hi - $lo) * ($plotBottom - $plotTop) }
    foreach ($ref in @(@($script:THR.cmW, $C.warn), @($script:THR.cmB, $C.bad))) {
        if ($ref[0] -gt $lo) {
            $ln = New-Object Windows.Shapes.Line; $ln.X1 = $left; $ln.X2 = $right; $ln.Y1 = (& $Y $ref[0]); $ln.Y2 = $ln.Y1
            $ln.Stroke = Brush $ref[1]; $ln.StrokeThickness = 1; $ln.StrokeDashArray = [Windows.Media.DoubleCollection]::new([double[]]@(3, 4)); $ln.Opacity = 0.45; $cv.Children.Add($ln) | Out-Null
        }
    }
    function Add-Series($vals, [string]$color, [double]$thick, [bool]$area, [bool]$dashed=$false) {
        $pts = New-Object Windows.Media.PointCollection
        for ($i = 0; $i -lt $vals.Count; $i++) { $pts.Add([Windows.Point]::new($left + $i / ($vals.Count - 1) * ($right-$left), (& $Y $vals[$i]))) }
        if ($area) {
            $ap = New-Object Windows.Media.PointCollection; $ap.Add([Windows.Point]::new($left, $plotBottom)); foreach ($p in $pts) { $ap.Add($p) }; $ap.Add([Windows.Point]::new($right, $plotBottom))
            $poly = New-Object Windows.Shapes.Polygon; $poly.Points = $ap
            $gb = New-Object Windows.Media.LinearGradientBrush; $gb.StartPoint = [Windows.Point]::new(0, 0); $gb.EndPoint = [Windows.Point]::new(0, 1)
            $col = [Windows.Media.ColorConverter]::ConvertFromString($color)
            $gb.GradientStops.Add((New-Object Windows.Media.GradientStop ([Windows.Media.Color]::FromArgb(110, $col.R, $col.G, $col.B), 0.0)))
            $gb.GradientStops.Add((New-Object Windows.Media.GradientStop ([Windows.Media.Color]::FromArgb(0, $col.R, $col.G, $col.B), 1.0)))
            $poly.Fill = $gb; $cv.Children.Add($poly) | Out-Null
        }
        $pl = New-Object Windows.Shapes.Polyline; $pl.Points = $pts; $pl.Stroke = Brush $color; $pl.StrokeThickness = $thick; $pl.StrokeLineJoin = 'Round'; if($dashed){$pl.StrokeDashArray=[Windows.Media.DoubleCollection]::new([double[]]@(5,4))}; $cv.Children.Add($pl) | Out-Null
        $last = $pts[$pts.Count - 1]
        $dot = New-Object Windows.Shapes.Ellipse; $dot.Width = 7; $dot.Height = 7; $dot.Fill = Brush $color
        [Windows.Controls.Canvas]::SetLeft($dot, $last.X - 4.5); [Windows.Controls.Canvas]::SetTop($dot, $last.Y - 3.5); $cv.Children.Add($dot) | Out-Null
    }
    Add-Series $ram $C.violet 2 $false $false
    Add-Series $commit $C.accent 2 $false $true
    $lt = New-Text "$lo%" 11 $C.muted 'Normal' $true; [Windows.Controls.Canvas]::SetLeft($lt, 0); [Windows.Controls.Canvas]::SetTop($lt, $h - 18); $cv.Children.Add($lt) | Out-Null
    $ht = New-Text '100%' 11 $C.muted 'Normal' $true; [Windows.Controls.Canvas]::SetLeft($ht, 0); [Windows.Controls.Canvas]::SetTop($ht, 0); $cv.Children.Add($ht) | Out-Null
}

$script:lastStamp = -1
$script:chartSig = $null
function Update-Widget {
    Set-RestoredLayout
    if ($UI.toast -and $UI.toast.Visibility -eq 'Visible' -and (Get-Date) -ge $script:toastUntil) { $UI.toast.Visibility='Collapsed' }
    if ($script:sync.appearance) { Receive-Appearance $script:sync.appearance }
    elseif ($script:sync.data.appearance) { Receive-Appearance $script:sync.data.appearance }
    elseif ($script:appearanceRevision -lt 0 -and $script:sync.data.theme) { Set-Theme ([string]$script:sync.data.theme) }
    if ($script:S.theme -eq 'auto' -and ((Get-SysLight) -ne $script:isLight)) { Set-Theme 'auto' }
    $d = $script:sync.data
    if (-not $d) { $UI.sub.Text = if ($script:sync.err) { 'غير متصل بالخلفية — يُعاد التشغيل…' } else { 'جارٍ الاتصال…' }; return }
    Update-Compact $d
    if ($script:sync.stamp -eq $script:lastStamp) { return }
    $script:lastStamp = $script:sync.stamp
    $m = $d.m
    $UI.adm.Visibility = if ($d.admin) { 'Visible' } else { 'Collapsed' }
    Load-Thr $d.thr
    $lc = Lvl $m.commitPct $script:THR.cmW $script:THR.cmB; $lr = Lvl $m.ramPct $script:THR.ramW $script:THR.ramB
    $ld = if ($m.diskFreeGB -lt $script:THR.dB) { 'bad' } elseif ($m.diskFreeGB -lt $script:THR.dW) { 'warn' } else { 'ok' }
    Set-Ring $UI.rCommit $m.commitPct $lc ([string][Math]::Round($m.commitPct)) '%' "$($m.commitGB) من $($m.limitGB) GB (الذاكرة + ملف الصفحات)"
    Set-Ring $UI.rRam $m.ramPct $lr ([string][Math]::Round($m.ramPct)) '%' "متاح $($m.availGB) GB من $($m.totalGB) GB"
    Set-Ring $UI.rDisk $m.diskPct $ld ([string][Math]::Round($m.diskFreeGB)) 'GB حر' "حر $($m.diskFreeGB) GB من $($m.diskTotalGB) GB"
    $sig = '{0}|{1}|{2}|{3}' -f @($d.hist.commit).Count, @($d.hist.commit)[-1], @($d.hist.ram)[-1], $script:isLight
    if ($sig -ne $script:chartSig) { $script:chartSig = $sig; Draw-Chart $d.hist.commit $d.hist.ram }
    # composition of commit
    $cl = if ($m.claudeGB) { [double]$m.claudeGB } else { 0 }; $ws = if ($m.wslGB) { [double]$m.wslGB } else { 0 }
    $other = [Math]::Max(0, $m.commitGB - $cl - $ws); $free = [Math]::Max(0.2, $m.limitGB - $m.commitGB)
    $vals = @([Math]::Max(0.2, $cl), [Math]::Max(0.1, $ws), [Math]::Max(0.1, $other), $free)
    for ($i = 0; $i -lt 4; $i++) { $UI.barCols[$i].Width = [Windows.GridLength]::new($vals[$i], 'Star') }
    $UI.compLegend.Text = ('Claude {0:N1} · WSL {1:N1} · أخرى {2:N1} · حر {3:N1}  (GB)' -f $cl, $ws, $other, ($m.limitGB - $m.commitGB))
    Set-Chip $UI.cClaude ([string]$m.claude); Set-Chip $UI.cShell ([string]$m.shells); Set-Chip $UI.cConhost ([string]$m.conhost)
    $UI.cClaude.Val.Foreground = Level-Brush (Lvl $m.claude 20 30); $UI.cShell.Val.Foreground = Level-Brush (Lvl $m.shells 25 40); $UI.cConhost.Val.Foreground = Level-Brush (Lvl $m.conhost 150 250)
    # tiers
    $anyFail = $false
    foreach ($t in $TIERS) {
        $k = $t[0]; $u = $UI.tiers[$k]; $ti = $d.tiers.$k
        $paused = [bool]$ti.paused; $u.Paused = $paused; Set-Switch $u.Switch (-not $paused)
        $u.Name.Foreground = if ($paused) { Brush $C.muted } else { Brush $C.ink }
        if ($ti.last) {
            $fail = ($ti.last.failed -gt 0); if ($fail) { $anyFail = $true }
            $freedTxt = if ($ti.last.freed -gt 0) { Fmt-Bytes $ti.last.freed } else { 'بلا حذف' }
        $u.S1.Text = ('آخر: {0} · {1} · {2}' -f (Ago $ti.last.ts), $(if ($fail) { 'فشل جزئي' } else { 'نجح' }), $freedTxt)
            $u.S1.Foreground = if ($fail) { Brush $C.warn } else { Brush $C.muted }
        } else { $u.S1.Text = 'لم تعمل فعلياً بعد'; $u.S1.Foreground = Brush $C.muted }
        $u.S2.Text = if ($paused) { 'موقوفة بالسياسة — لن تعمل في موعدها' } elseif ($ti.next) { 'التالي: ' + ([datetime]::Parse([string]$ti.next)).ToString('dd/MM HH:mm') } else { '' }
        $u.S2.Foreground = if ($paused) { Brush $C.warn } else { Brush $C.muted }
        $u.S1.ToolTip = $u.S1.Text; $u.S2.ToolTip = if ($u.S2.Text) { $u.S2.Text } else { $null }   # النص يُقصّ بـ CharacterEllipsis فيبقى كاملًا في التلميح
        $running = ($d.manual.running -and $d.manual.tier -eq $k)
        $u.RunText.Text = if ($running) { '… يعمل' } else { '▶ تشغيل' }
    }
    $tr = $d.trim
    if ($tr) {
        if ($tr.running) { $UI.trimText.Text = '… جارٍ تنظيف الذاكرة' }
        elseif ($tr.err) { $UI.trimText.Text = '⚡ تنظيف الذاكرة الآن · تعذّر' }
        elseif ($null -ne $tr.before -and $tr.ts) { $UI.trimText.Text = ('⚡ تنظيف الذاكرة الآن · {0}% ← {1}% ({2})' -f $tr.before, $tr.after, (Ago $tr.ts)) }
        else { $UI.trimText.Text = '⚡ تنظيف الذاكرة الآن' }
    }
    $guardOk = ($d.guard -and $d.guard.applyInArgs -and $d.guard.lastMode -ne 'DRYRUN')
    $worst = Get-SummaryLevel @($lc, $lr, $ld)
    if ($worst -eq 'ok' -and ($anyFail -or ($d.guard -and -not $guardOk))) { $worst = 'warn' }
    try {
        $ca = $d.cpuAlert
        if ($ca -and $ca.name) {
            $UI.cpuAlert.Text = ('⚠ المعالج: {0} يستهلك {1:N1} نواة منذ ~{2} دقيقة' -f $ca.name, [double]$ca.avg, [int]$ca.minutes); $UI.cpuAlert.Visibility = 'Visible'
            if ($worst -eq 'ok') { $worst = 'warn' }
        } else { $UI.cpuAlert.Visibility = 'Collapsed' }
    } catch {}
    $UI.pill.Background = Level-Brush $worst; $UI.pillText.Text = switch ($worst) { 'bad' { 'حرج' } 'warn' { 'تنبيه' } default { 'مستقر' } }
    Update-Compact $d
    if ($d.guard) { $UI.guard.Text = if ($guardOk) { 'حارس الذاكرة: ● تنفيذ' } else { 'حارس الذاكرة: ○ تجربة فقط' }; $UI.guard.Foreground = if ($guardOk) { Brush $C.muted } else { Brush $C.warn } } else { $UI.guard.Text = '' }
}

# ------------------------------------------------------------------ interactions
$script:pending = $null
function Get-SummaryLevel($levels) { $worst='ok'; foreach($level in $levels) { if($level -eq 'bad') {return 'bad'};if($level -eq 'warn') {$worst='warn'} };return $worst }
function Show-Toast([string]$msg) { $script:toastUntil=(Get-Date).AddSeconds(7); if ($UI.toast) { $UI.toast.Text=$msg; $UI.toast.ToolTip=$msg; $UI.toast.Visibility='Visible' } else { $UI.sub.Text=$msg } }
foreach ($t in $TIERS) {
    $u = $UI.tiers[$t[0]]
    $u.Switch.Add_Click({
        $tier = $this.Tag; $uu = $UI.tiers[$tier]; $newPaused = (-not $uu.Paused); Set-Switch $uu.Switch (-not $uu.Paused); $uu.Switch.IsEnabled = $false
        Start-Bg '/api/tier-pause' @{ tier = $tier; paused = $newPaused } ({ param($r) $uu.Switch.IsEnabled = $true; $uu.Paused = $newPaused; Set-Switch $uu.Switch (-not $newPaused); $script:lastStamp = -1; Show-Toast ($(if ($newPaused) { 'أُوقفت جدولة التنظيف ' } else { 'فُعّلت جدولة التنظيف ' }) + $uu.Label) }.GetNewClosure()) ({ param($m) $uu.Switch.IsEnabled = $true; Set-Switch $uu.Switch (-not $uu.Paused); Show-Toast ('تعذّر: ' + $m) }.GetNewClosure())
    })
    $u.Run.Add_Click({
        $tier = $this.Tag; $uu = $UI.tiers[$tier]
        $script:pending = $tier; $UI.confirmText.Text = 'تشغيل تنظيف ' + $uu.Label + ' الآن؟ التنفيذ الفعلي يحذف الملفات المطابقة للقواعد المفعّلة.'; $UI.confirm.Visibility = 'Visible'; $UI.cfNo.Focus() | Out-Null
    })
}
function Start-Clean([bool]$dry) {
    $tier = $script:pending; $UI.confirm.Visibility = 'Collapsed'; $script:pending = $null
    if (-not $tier) { return }
    Start-Bg '/api/run-cleanup' @{ tier = $tier; dry = $dry } ({ param($r) Show-Toast ($(if ($dry) { 'بدأت التجربة: ' } else { 'بدأ التنفيذ: ' }) + $UI.tiers[$tier].Label) }.GetNewClosure()) { param($m) Show-Toast ('تعذّر: ' + $m) }
}
# trim runs in a background runspace: the UI thread never waits on the server
$script:trimJob = $null
function Start-Trim([string]$mode) {
    if ($script:trimJob) { return }
    if ($mode -eq 'deep' -and [Windows.MessageBox]::Show('الوضع العميق قد يجمّد الجهاز لدقائق. متابعة؟', 'مرصد الجهاز', 'YesNo', 'Warning') -ne 'Yes') { return }
    $script:trimPendingAt = Get-Date
    $UI.trimText.Text = '… جارٍ تنظيف الذاكرة'; $script:lastStamp = -1
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $ps = [powershell]::Create()
    [void]$ps.AddScript({ param($tok, $body) Invoke-RestMethod 'http://127.0.0.1:47832/api/trim-memory' -Method Post -Headers @{ 'X-Token' = $tok } -ContentType 'application/json' -Body $body -TimeoutSec 6 -NoProxy }).AddArgument($script:tok).AddArgument((@{ mode = $mode } | ConvertTo-Json -Compress))
    $script:trimJob = @{ ps = $ps; ar = $ps.BeginInvoke(); sw = $sw }
    $trimTimer.Start()
    Log ('trim: pressed ' + $mode)
}
# الزر يفتح قائمة بالأعماق الثلاثة بدل أن ينفّذ مباشرة
$trimMenu = New-Object Windows.Controls.ContextMenu; $trimMenu.FlowDirection = 'RightToLeft'; $trimMenu.FontFamily = $FONTS.list
foreach ($m in @(@('light', 'بسيط (آمن)'), @('medium', 'متوسط (العمليات الخاملة)'), @('deep', 'عميق (بتأكيد)'))) {
    $mi = New-Object Windows.Controls.MenuItem; $mi.Header = $m[1]; $mi.Tag = $m[0]
    $mi.Add_Click({ param($sender, $e) Start-Trim ([string]$sender.Tag) }.GetNewClosure())
    [void]$trimMenu.Items.Add($mi)
}
$UI.trim.Add_Click({
    if ($script:trimJob) { return }
    $r = New-WidgetMenuStyles
    $trimMenu.Style = $r['WidgetContextMenu']; $trimMenu.Resources[[Windows.Controls.MenuItem]] = $r['WidgetMenuItem']; Set-WidgetMenuItems $trimMenu.Items $r
    $trimMenu.PlacementTarget = $UI.trim; $trimMenu.Placement = 'Top'; $trimMenu.IsOpen = $true
})
$trimTimer = New-Object Windows.Threading.DispatcherTimer; $trimTimer.Interval = [TimeSpan]::FromMilliseconds(200)
$trimTimer.Add_Tick({
    $j = $script:trimJob; if (-not $j) { $this.Stop(); return }; if (-not $j.ar.IsCompleted) { return }
    $script:trimJob = $null; $this.Stop()
    try {
        $null = $j.ps.EndInvoke($j.ar)
        if ($j.ps.HadErrors) { throw ([string]$j.ps.Streams.Error[0]) }
        Log ('trim: answered in {0} ms' -f $j.sw.ElapsedMilliseconds)
    } catch {
        Log ('trim: failed after {0} ms: {1}' -f $j.sw.ElapsedMilliseconds, $_.Exception.Message)
        Show-Toast ('تعذّر: ' + $_.Exception.Message)
    } finally { $j.ps.Dispose() }
})
$UI.cfReal.Add_Click({ Start-Clean $false })
$UI.cfDry.Add_Click({ Start-Clean $true })
$UI.cfNo.Add_Click({ $script:pending = $null; $UI.confirm.Visibility = 'Collapsed' })
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Diagnostics;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class WF {
    public delegate bool CB(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(CB callback, IntPtr parameter);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder text, int length);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint message, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern bool MoveWindow(IntPtr h, int x, int y, int width, int height, bool repaint);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr h, int command);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    public static bool IsDashboardCandidate(string expectedTitle, string actualTitle, string processName, bool visible) {
        return visible && !String.IsNullOrWhiteSpace(expectedTitle)
            && String.Equals(expectedTitle, actualTitle, StringComparison.Ordinal)
            && (String.Equals(processName, "msedge", StringComparison.OrdinalIgnoreCase)
                || String.Equals(processName, "powershell", StringComparison.OrdinalIgnoreCase)
                || String.Equals(processName, "pwsh", StringComparison.OrdinalIgnoreCase));  // الغلاف app_shell.ps1 يستضيف الواجهة
    }
    public static long PickOne(long[] candidates, long foreground) {
        if (candidates == null || candidates.Length == 0) return 0;
        foreach (long h in candidates) if (h == foreground) return h;
        return candidates[0];
    }
    static long[] Candidates(string title) {
        var found = new List<long>();
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            var text = new StringBuilder(1024); GetWindowText(h, text, text.Capacity);
            if (!String.Equals(title, text.ToString(), StringComparison.Ordinal)) return true;
            try {
                uint pid; GetWindowThreadProcessId(h, out pid);
                using (var process = Process.GetProcessById((int)pid)) {
                    if (IsDashboardCandidate(title, text.ToString(), process.ProcessName, true)) found.Add(h.ToInt64());
                }
            } catch { /* A vanished or inaccessible process is never a close target. */ }
            return true;
        }, IntPtr.Zero);
        return found.ToArray();
    }
    public static bool Exists(string title) { return Candidates(title).Length > 0; }
    public static int Close(string title) {
        long target = PickOne(Candidates(title), GetForegroundWindow().ToInt64());
        if (target == 0) return 0;
        // Close exactly one dashboard window. Never terminate its process or services.
        return PostMessage(new IntPtr(target), 0x0010, IntPtr.Zero, IntPtr.Zero) ? 1 : 0;
    }
    public static bool Place(string title, int x, int y, int width, int height) {
        long target = PickOne(Candidates(title), GetForegroundWindow().ToInt64());
        if (target == 0) return false;
        return MoveWindow(new IntPtr(target), x, y, width, height, true);
    }
    public static bool Activate(string title) {
        long target = PickOne(Candidates(title), GetForegroundWindow().ToInt64());
        if (target == 0) return false;
        var h = new IntPtr(target);
        if (IsIconic(h)) ShowWindow(h, 9);
        return SetForegroundWindow(h);
    }
}
'@
$UI.open.Add_Click({
    try {
        $m = [regex]::Match([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'web\index.html'), [Text.Encoding]::UTF8), '<title>(.*?)</title>')
        if ($m.Success -and [WF]::Exists($m.Groups[1].Value.Trim())) {
            [void][WF]::Activate($m.Groups[1].Value.Trim())
            return
        }
    } catch { Show-Toast 'تعذّر تحديد نافذة التطبيق بأمان'; return }
    # one-time ticket: the token never goes on the command line
    Start-Bg '/api/ticket' @{} { param($r) Open-Dash ([string]$r.ticket) } { param($m) Show-Toast 'الخادم غير متصل' }
})
function Open-Dash([string]$tk) {
    if (-not $tk) { Show-Toast 'الخادم غير متصل'; return }
    $url = "http://127.0.0.1:47832/?k=$tk"
    $edge = @('C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', 'C:\Program Files\Microsoft\Edge\Application\msedge.exe') | Where-Object { Test-Path $_ } | Select-Object -First 1
    $UI.open.Content = $script:txtClose
    try {
        $shell = Join-Path $PSScriptRoot 'app_shell.ps1'
        $vend = Join-Path $env:LOCALAPPDATA 'MonitorWidget\webview2\Microsoft.Web.WebView2.Wpf.dll'
        $haveVend = (Test-Path $vend) -or (Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Microsoft\TeamsMeetingAdd-in') -Recurse -Filter 'Microsoft.Web.WebView2.Wpf.dll' -ErrorAction SilentlyContinue | Select-Object -First 1)
        $why = if (Test-Path $shell) { 'no_webview2' } else { 'no_shell' }   # سبب السقوط المباشر؛ الغلاف نفسه يسجّل سقوطه عند فشل التهيئة
        if ((Test-Path $shell) -and $haveVend) {
            # نافذة بلا إطار بحواف مستديرة مثل الويدجت (WebView2)؛ المقاس والموضع بالبكسل الفعلي
            $wa = [System.Windows.SystemParameters]::WorkArea
            $sc = [Windows.PresentationSource]::FromVisual($win).CompositionTarget.TransformToDevice.M11
            $pw = [int]([Math]::Max(900, $wa.Width * $sc - 490)); $ph = [int]($wa.Height * $sc)
            $px = [int]($wa.Left * $sc); $py = [int]($wa.Top * $sc)
            Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', "`"$shell`"", '-Url', "`"$url`"", '-X', $px, '-Y', $py, '-W', $pw, '-H', $ph
            Log ('open: shell at ' + $px + ',' + $py + ' size ' + $pw + 'x' + $ph)
        } elseif ($edge) {
            # نفس منطق الخادم: منطقة العمل الفعلية ناقص مساحة الويدجت، والبدء من الركن الأعلى الأيسر (بالبكسل الفعلي)
            $wa = [System.Windows.SystemParameters]::WorkArea
            $sc = [Windows.PresentationSource]::FromVisual($win).CompositionTarget.TransformToDevice.M11
            $pw = [int]([Math]::Max(900, $wa.Width * $sc - 490)); $ph = [int]($wa.Height * $sc - 40)  # 40: شريط العنوان
            $px = [int]($wa.Left * $sc); $py = [int]($wa.Top * $sc)
            $prof = Join-Path $env:LOCALAPPDATA 'MonitorWidget\appwin'; New-Item -ItemType Directory -Force -Path $prof | Out-Null
            Start-Bg '/api/shell' @{ engine = 'edge'; reason = $why } {} { param($m) Log ('open: shell state ' + $m) }   # صفحة الصحة تعرض سبب السقوط
            Start-Process $edge -ArgumentList "--app=$url", "--user-data-dir=$prof", "--window-size=$pw,$ph", "--window-position=$px,$py"
            # Edge قد يتجاهل المقاس ويفتح ملء الشاشة: نثبت الإطار مباشرة بعد ظهور النافذة
            $tt = if ($script:dashTitle) { $script:dashTitle } else { 'مرصد الجهاز' }
            # Retry placement without blocking the widget's input/scroll dispatcher.
            if ($script:dashPlaceTimer) { $script:dashPlaceTimer.Stop() }
            $placementState = @{ Attempts=0 }
            $script:dashPlaceTimer = New-Object Windows.Threading.DispatcherTimer
            $script:dashPlaceTimer.Interval = [TimeSpan]::FromMilliseconds(150)
            $script:dashPlaceTimer.Add_Tick(({
                $placementState.Attempts++
                $ok = [WF]::Place($tt, $px, $py, $pw, $ph)
                if ($ok -or $placementState.Attempts -ge 40) { $this.Stop(); Log ('open: placed=' + $ok + ' at ' + $px + ',' + $py + ' size ' + $pw + 'x' + $ph) }
            }.GetNewClosure()))
            $script:dashPlaceTimer.Start()
        } else { Start-Bg '/api/shell' @{ engine = 'browser'; reason = $why } {} { param($m) Log ('open: shell state ' + $m) }; Start-Process $url }
    } catch { Log ('open: '+$_.Exception.Message);Show-Toast 'تعذّر فتح التطبيق؛ تحقّق من تشغيل الخادم.' }
}


$script:dashTitle = $null
try { $mt = [regex]::Match([IO.File]::ReadAllText((Join-Path $PSScriptRoot 'web\index.html'), [Text.Encoding]::UTF8), '<title>(.*?)</title>'); if ($mt.Success) { $script:dashTitle = $mt.Groups[1].Value.Trim() } } catch {}
$dashTimer = New-Object Windows.Threading.DispatcherTimer; $dashTimer.Interval = [TimeSpan]::FromMilliseconds(1000)
$dashTimer.Add_Tick({ try { if ($script:dashTitle) { $t = if ([WF]::Exists($script:dashTitle)) { $script:txtClose } else { $script:txtOpen }; if ($UI.open.Content -ne $t) { $UI.open.Content = $t } } } catch {} })
$dashTimer.Start()

# menu
$menu = New-Object Windows.Controls.ContextMenu; $menu.FlowDirection = 'RightToLeft'; $menu.FontFamily = $FONTS.list   # قائمة الويدجت بخط القوائم (--f-list) كقائمة التنقل في الواجهة
# opacity slider 10%..100% (saved on release)
$opPanel = New-Object Windows.Controls.StackPanel; $opPanel.Orientation = 'Horizontal'; $opPanel.FlowDirection = 'RightToLeft'
$opLbl = New-Object Windows.Controls.TextBlock; $opLbl.Text = 'عتامة الويدجت'; $opLbl.Margin = '0,0,10,0'; $opLbl.VerticalAlignment = 'Center'
$opSl = New-Object Windows.Controls.Slider; $opSl.Minimum = 0.1; $opSl.Maximum = 1.0; $opSl.SmallChange = 0.01; $opSl.LargeChange = 0.05; $opSl.IsMoveToPointEnabled = $true; $opSl.Width = 140; $opSl.VerticalAlignment = 'Center'
$opSl.FlowDirection = 'LeftToRight'; $opSl.Value = [Math]::Min(1.0, [Math]::Max(0.1, [double]$script:S.opacity))
$opSl.ToolTip='100% معتم تمامًا، و10% أكثر شفافية. تشمل الشفافية كل محتويات الويدجت: الخلفية والنص والأزرار والرسم.'
[Windows.Automation.AutomationProperties]::SetName($opSl, 'عتامة الويدجت'); [Windows.Automation.AutomationProperties]::SetHelpText($opSl, $opSl.ToolTip)
$opVal = New-Object Windows.Controls.TextBlock; $opVal.FontFamily = $FONTS.num; $opVal.Width = 44; $opVal.Margin = '8,0,0,0'; $opVal.VerticalAlignment = 'Center'; $opVal.FlowDirection = 'LeftToRight'
$opVal.Text = ('{0}%' -f [int][Math]::Round($opSl.Value * 100))
$opSl.Add_ValueChanged({ $v = [Math]::Round($this.Value, 2); $opVal.Text = ('{0}%' -f [int][Math]::Round($v * 100)); Set-CardOpacity $v })
$opSl.Add_PreviewMouseLeftButtonUp({ Save-Settings })
$opSl.Add_LostKeyboardFocus({ Save-Settings })
$opPanel.Children.Add($opLbl) | Out-Null; $opPanel.Children.Add($opSl) | Out-Null; $opPanel.Children.Add($opVal) | Out-Null
$opItem = New-Object Windows.Controls.MenuItem; $opItem.Header = $opPanel; $menu.Items.Add($opItem) | Out-Null
$menu.Items.Add((New-Object Windows.Controls.Separator)) | Out-Null
$miTop = New-Object Windows.Controls.MenuItem; $miTop.Header = 'فوق النوافذ'; $miTop.IsCheckable = $true; $miTop.IsChecked = [bool]$script:S.topmost
$miTop.Add_Click({ $win.Topmost = $this.IsChecked; $script:S.topmost = $this.IsChecked; Save-Settings }); $menu.Items.Add($miTop) | Out-Null
$miMute = New-Object Windows.Controls.MenuItem; $miMute.Header = 'كتم التنبيهات الجديدة'; $miMute.IsCheckable = $true; $miMute.IsChecked = [bool]$script:S.muteAlerts
$miMute.Add_Click({ $script:S.muteAlerts = $this.IsChecked; Save-Settings }); [void]$menu.Items.Add($miMute)
$miPosition = New-Object Windows.Controls.MenuItem; $miPosition.Header = 'حفظ موضع الويدجت'; $miPosition.IsCheckable = $true; $miPosition.IsChecked = [bool]$script:S.restorePos
$miPosition.Add_Click({ $script:S.restorePos = $this.IsChecked; Save-Settings }); [void]$menu.Items.Add($miPosition)
$startupLnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'MiniWidget.lnk'
$miAuto = New-Object Windows.Controls.MenuItem; $miAuto.Header = 'تشغيل مع الدخول'; $miAuto.IsCheckable = $true; $miAuto.IsChecked = (Test-Path $startupLnk)
$miAuto.Add_Click({
    if ($this.IsChecked) {
        $s = (New-Object -ComObject WScript.Shell).CreateShortcut($startupLnk); $s.TargetPath = "$env:WINDIR\System32\wscript.exe"; $s.Arguments = "`"$Root\launch_mini.vbs`""; $s.WorkingDirectory = $Root; $s.WindowStyle = 7; $s.Save()
    } else { Remove-Item $startupLnk -Force -ErrorAction SilentlyContinue }
}); $menu.Items.Add($miAuto) | Out-Null
$menu.Items.Add((New-Object Windows.Controls.Separator)) | Out-Null
$script:appearanceMenuItems = @()
$appearanceMenu = New-Object Windows.Controls.MenuItem; $appearanceMenu.Header = 'المظهر الموحد للتطبيق والويدجت'
foreach ($th in @(@('auto', 'حسب النظام'), @('light', 'فاتح'), @('dark', 'داكن'))) {
    $mt = New-Object Windows.Controls.MenuItem; $mt.Header = $th[1]; $mt.Tag = 'mode:' + $th[0]; $mt.IsCheckable = $true; $mt.IsChecked = ($script:S.theme -eq $th[0])
    $mt.Add_Click({
        $this.IsChecked = $false; $modeName = ([string]$this.Tag).Substring(5); Request-Appearance $modeName $script:S.palette
    }); $appearanceMenu.Items.Add($mt) | Out-Null; $script:appearanceMenuItems += $mt
}
[void]$appearanceMenu.Items.Add((New-Object Windows.Controls.Separator))
foreach ($pal in @(@('teal','أخضر بحري'),@('blue','أزرق'),@('violet','بنفسجي'),@('graphite','رمادي'))) {
    $mt=New-Object Windows.Controls.MenuItem; $mt.Header='لون الهوية: '+$pal[1]; $mt.Tag='palette:'+$pal[0]; $mt.IsCheckable=$true; $mt.IsChecked=($script:S.palette -eq $pal[0])
    $mt.Add_Click({ $this.IsChecked=$false; $paletteName=([string]$this.Tag).Substring(8); Request-Appearance $script:S.theme $paletteName }); [void]$appearanceMenu.Items.Add($mt); $script:appearanceMenuItems += $mt
}
$restoreAppearance = New-Object Windows.Controls.MenuItem; $restoreAppearance.Header='استعادة المظهر الافتراضي'; $restoreAppearance.Add_Click({ Request-Appearance 'auto' 'teal' }); [void]$appearanceMenu.Items.Add($restoreAppearance)
[void]$menu.Items.Add($appearanceMenu)
$menu.Items.Add((New-Object Windows.Controls.Separator)) | Out-Null
$miClose = New-Object Windows.Controls.MenuItem; $miClose.Header = 'إغلاق الويدجت'; $miClose.Add_Click({ $win.Close() }); $menu.Items.Add($miClose) | Out-Null
$UI.menuBtn.Add_Click({ $menu.PlacementTarget = $UI.menuBtn; $menu.IsOpen = $true })
$card.ContextMenu = $menu
Set-WidgetMenuTheme
# ✕ hides the widget (the process stays alive); "إغلاق الويدجت" in the menu exits it
$UI.closeBtn.Add_Click({ $script:S.visible = $false; Save-Settings; $win.Hide() })

function Get-WidgetWorkArea {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $handle=(New-Object Windows.Interop.WindowInteropHelper $win).Handle
        $screen=[System.Windows.Forms.Screen]::FromHandle($handle)
        $bounds=$screen.WorkingArea
        $presentation=[Windows.PresentationSource]::FromVisual($win)
        if (-not $presentation -or -not $presentation.CompositionTarget) { return [Windows.SystemParameters]::WorkArea }
        $matrix=$presentation.CompositionTarget.TransformFromDevice
        # Convert the selected monitor's physical work rectangle using the window's actual DPI.
        $a=$matrix.Transform([Windows.Point]::new($bounds.Left,$bounds.Top)); $b=$matrix.Transform([Windows.Point]::new($bounds.Right,$bounds.Bottom))
        return [Windows.Rect]::new($a,$b)
    } catch { return [Windows.SystemParameters]::WorkArea }
}
function Get-WidgetPlacement([Windows.Rect]$work, [double]$width, [double]$height, [double]$left, [double]$top) {
    return @{Left=[Math]::Max($work.Left,[Math]::Min($left,$work.Right-$width));Top=[Math]::Max($work.Top,[Math]::Min($top,$work.Bottom-$height))}
}
function Fit-WidgetBounds {
    if($script:placingWidget -or -not $win.IsLoaded -or $win.ActualHeight -le 0){return};$script:placingWidget=$true
    try{$work=Get-WidgetWorkArea;Set-RestoredLayout $work.Height;$position=Get-WidgetPlacement $work $win.ActualWidth $win.ActualHeight $win.Left $win.Top;if([Math]::Abs($win.Left-$position.Left) -gt .5){$win.Left=$position.Left};if([Math]::Abs($win.Top-$position.Top) -gt .5){$win.Top=$position.Top}}finally{$script:placingWidget=$false}
}
$card.Add_MouseLeftButtonDown({ try { $win.DragMove(); Fit-WidgetBounds; Save-Settings } catch {} })
$win.Add_SizeChanged({ Fit-WidgetBounds })
$win.Add_LocationChanged({ $script:S.left = [int]$win.Left; $script:S.top = [int]$win.Top })
$win.Add_SourceInitialized({
    $h = (New-Object Windows.Interop.WindowInteropHelper $win).Handle
    [void][W32]::SetWindowLong($h, -20, ([W32]::GetWindowLong($h, -20) -bor 0x80))   # tool window: no Alt-Tab entry
})
$win.Add_Loaded({
    $script:S.visible = $true; Save-Settings
    $wa = [Windows.SystemParameters]::WorkArea
    $vs = @([Windows.SystemParameters]::VirtualScreenLeft, [Windows.SystemParameters]::VirtualScreenTop, [Windows.SystemParameters]::VirtualScreenWidth, [Windows.SystemParameters]::VirtualScreenHeight)
    $okPos = ($null -ne $script:restoreLeft -and $null -ne $script:restoreTop -and $script:restoreLeft -gt $vs[0] - 50 -and $script:restoreLeft -lt $vs[0] + $vs[2] - 100 -and $script:restoreTop -gt $vs[1] - 20 -and $script:restoreTop -lt $vs[1] + $vs[3] - 100)
    # First use: top-right. Preserve an explicitly configured restorePos preference; new installs restore position.
    if ($okPos -and $script:S.restorePos -eq $true) { $nl = [double]$script:restoreLeft; $nt = [double]$script:restoreTop }
    else { $nl = $wa.Right - $win.ActualWidth; $nt = $wa.Top }
    # clamp inside the virtual screen (DIPs, same unit as Left/Top) so a removed monitor can never strand the widget
    $win.Left = [Math]::Min([Math]::Max($nl, $vs[0]), $vs[0] + $vs[2] - $win.ActualWidth)
    $win.Top = [Math]::Min([Math]::Max($nt, $vs[1]), $vs[1] + $vs[3] - $win.ActualHeight)
    Fit-WidgetBounds
    Save-Settings
})
$win.Add_Closing({ Save-Settings; if ($script:dashPlaceTimer) { $script:dashPlaceTimer.Stop() }; try { $script:ps.Stop(); $script:ps.Dispose() } catch {}; try { $script:appearancePs.Stop(); $script:appearancePs.Dispose() } catch {} })

# Appearance is consumed by the existing UI timer; local geometry writes never override it.
$script:trimPendingAt = [datetime]::MinValue
function Update-Countdown {
    # runs every second (not gated by the poll stamp), so the countdown ticks each second
    $d = $script:sync.data; if (-not $d) { return }
    try {
        $busy = ($d.trim -and $d.trim.running) -or ($d.guardNext -and $d.guardNext.running) -or (((Get-Date) - $script:trimPendingAt).TotalSeconds -lt 8)
        if ($busy) { $UI.trimCd.Text = 'التنظيف جارٍ · العداد متوقف'; $UI.trimCd.Foreground = Brush $C.accentText; return }
        if ($d.guardNext -and $d.guardNext.next) {
            $rem = [int][Math]::Ceiling(([datetime]::Parse([string]$d.guardNext.next) - (Get-Date)).TotalSeconds)
            if ($rem -le 0) { $UI.trimCd.Text = 'التنظيف التلقائي الآن…'; $UI.trimCd.Foreground = Brush $C.accentText }
            else { $UI.trimCd.Text = ('التنظيف التلقائي بعد {0:00}:{1:00}' -f [Math]::Floor($rem / 60), ($rem % 60)); $UI.trimCd.Foreground = Brush $C.muted }
        } else { $UI.trimCd.Text = '' }
    } catch { $UI.trimCd.Text = '' }
}
$timer = New-Object Windows.Threading.DispatcherTimer; $timer.Interval = [TimeSpan]::FromMilliseconds(1000)
$timer.Add_Tick({ try { Update-Countdown; Update-Widget } catch { Log ('update: ' + $_.Exception.Message + ' @ ' + $_.InvocationInfo.PositionMessage) } })
$timer.Start()

try {
    # Tray icon: restores the widget after it was hidden or closed to the tray, without the dashboard.
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $script:tray = New-Object System.Windows.Forms.NotifyIcon
        $script:tray.Icon = [System.Drawing.SystemIcons]::Application
        try { $icoPath = Join-Path $PSScriptRoot 'assets\icon.ico'; if (Test-Path -LiteralPath $icoPath) { $script:tray.Icon = New-Object System.Drawing.Icon($icoPath) } } catch {}
        $script:tray.Text = 'مرصد الجهاز'
        $script:tray.Visible = $true
        $trayToggle = {
            if ($win.IsVisible) { $script:S.visible = $false; Save-Settings; $win.Hide() }
            else { $script:S.visible = $true; Save-Settings; $win.Show(); $win.Activate() | Out-Null }
        }
        $tm = New-Object System.Windows.Forms.ContextMenuStrip
        # Native paired OS colors keep selected/disabled tray items readable, including Windows High Contrast.
        $tm.RenderMode=[System.Windows.Forms.ToolStripRenderMode]::System
        $tmShow = $tm.Items.Add('إظهار / إخفاء الويدجت')
        $tmShow.Add_Click($trayToggle)
        $tmAppearance = New-Object System.Windows.Forms.ToolStripMenuItem('المظهر الموحد للتطبيق والويدجت')
        foreach ($entry in @(@('mode:auto','حسب النظام'),@('mode:light','فاتح'),@('mode:dark','داكن'),@('palette:teal','أخضر بحري'),@('palette:blue','أزرق'),@('palette:violet','بنفسجي'),@('palette:graphite','رمادي'))) {
            $item=New-Object System.Windows.Forms.ToolStripMenuItem($entry[1]); $item.Tag=$entry[0]
            $item.Add_Click({ param($sender,$ev) $choice=[string]$sender.Tag; if ($choice.StartsWith('mode:')) { Request-Appearance $choice.Substring(5) $script:S.palette } else { Request-Appearance $script:S.theme $choice.Substring(8) } })
            [void]$tmAppearance.DropDownItems.Add($item)
        }
        [void]$tm.Items.Add($tmAppearance)
        $tmExit = $tm.Items.Add('إغلاق الويدجت')
        $tmExit.Add_Click({ $win.Close() })
        $script:tray.ContextMenuStrip = $tm
        $script:tray.Add_MouseClick({ param($snd, $ev) if ($ev.Button -eq [System.Windows.Forms.MouseButtons]::Left) { & $trayToggle } })
        $win.Add_Closed({ try { $script:tray.Visible = $false; $script:tray.Dispose() } catch {} })
    } catch { Log ('tray: ' + $_.Exception.Message) }    $app = New-Object Windows.Application; $app.ShutdownMode = 'OnMainWindowClose'
    [void]$app.Run($win)
} catch { Log ('fatal: ' + $_.Exception.Message + ' @ ' + $_.InvocationInfo.PositionMessage) }
finally { try { $script:mutex.ReleaseMutex() } catch {} }
