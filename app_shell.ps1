# غلاف نافذة التطبيق: نافذة بلا إطار ويندوز بحواف مستديرة مثل الويدجت، تستضيف الواجهة عبر WebView2.
# يُشغَّل بـ Windows PowerShell (STA). إن لم تتوفر مكتبات WebView2 أو فشلت تهيئته يسقط الغلاف بنفسه إلى Edge --app
# بالتذكرة نفسها، ويسجّل المحرك والسبب في shell_state.json ليعرض الخادم التحذير في صفحة الصحة (/api/health → shell).
param(
    [Parameter(Mandatory)][string]$Url,
    [int]$X = 0, [int]$Y = 0, [int]$W = 1280, [int]$H = 900,
    [string]$Title = 'مرصد الجهاز'
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$vend = Join-Path $env:LOCALAPPDATA 'MonitorWidget\webview2'
$stateFile = Join-Path $env:LOCALAPPDATA 'MonitorWidget\shell_state.json'
$errorLog = Join-Path $env:LOCALAPPDATA 'MonitorWidget\shell_error.log'
$script:fallbackReason = $null

function ConvertTo-ShellStateJson([string]$engine, [string]$reason, [long]$ts) {
    # مخطط الخادم نفسه (_record_shell في server.py): engine / reason / ts
    return ([ordered]@{ engine = $engine; reason = $reason; ts = $ts } | ConvertTo-Json -Compress)
}
function Write-ShellState([string]$engine, [string]$reason, [string]$path = $stateFile) {
    # كتابة ذرية (ملف مؤقت ثم إعادة تسمية) بلا BOM؛ الخادم يقرأ الملف في /api/health. الفشل لا يعطّل النافذة.
    try {
        $dir = Split-Path $path -Parent; New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $tmp = Join-Path $dir ('.shell_state.' + [IO.Path]::GetRandomFileName() + '.tmp')
        [IO.File]::WriteAllText($tmp, (ConvertTo-ShellStateJson $engine $reason ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())), (New-Object Text.UTF8Encoding $false))
        Move-Item -LiteralPath $tmp -Destination $path -Force
        return $true
    } catch { return $false }
}
function Write-ShellError([string]$reason, [string]$detail, [string]$path = $errorLog) {
    try { [IO.File]::WriteAllText($path, ('{0:s} {1}: {2}' -f (Get-Date), $reason, $detail), (New-Object Text.UTF8Encoding $false)) } catch {}
}
function Get-EdgeWindowHeight([int]$h) { return [Math]::Max(480, $h - 40) }   # 40: شريط العنوان الذي يضيفه Edge فوق المقاس
function Get-EdgeFallbackArgs([string]$url, [int]$x, [int]$y, [int]$w, [int]$h, [string]$prof) {
    # وسائط الخادم والويدجت نفسها: نافذة تطبيق بملف تعريف منفصل، بالمقاس والموضع المطلوبين بالبكسل الفعلي
    return @("--app=$url", "--user-data-dir=$prof", ('--window-size={0},{1}' -f $w, (Get-EdgeWindowHeight $h)), ('--window-position={0},{1}' -f $x, $y))
}
function Find-EdgeExe {
    foreach ($p in @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe")) { if ($p -and (Test-Path $p)) { return $p } }
    return $null
}
function Start-EdgeFallback([string]$reason) {
    # السقوط إلى Edge --app بالتذكرة نفسها (لم تُستهلك لأن WebView2 لم يبحر)، مع تسجيل المحرك لصفحة الصحة وتثبيت الإطار
    $edgeExe = Find-EdgeExe
    if (-not $edgeExe) { [void](Write-ShellState 'browser' $reason); Start-Process $Url; return }
    [void](Write-ShellState 'edge' $reason)
    $prof = Join-Path $env:LOCALAPPDATA 'MonitorWidget\appwin'; New-Item -ItemType Directory -Force -Path $prof | Out-Null
    Start-Process $edgeExe -ArgumentList (Get-EdgeFallbackArgs $Url $X $Y $W $H $prof)
    # Edge قد يتجاهل المقاس ويفتح ملء الشاشة: نثبت الإطار بعد ظهور النافذة (منطق الويدجت والخادم نفسه)
    try {
        Add-Type -TypeDefinition @'
using System; using System.Diagnostics; using System.Runtime.InteropServices; using System.Text;
public static class ShellWin {
    public delegate bool CB(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(CB callback, IntPtr parameter);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder text, int length);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool MoveWindow(IntPtr h, int x, int y, int width, int height, bool repaint);
    public static bool Place(string title, int x, int y, int width, int height) {
        IntPtr target = IntPtr.Zero;
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            var text = new StringBuilder(1024); GetWindowText(h, text, text.Capacity);
            if (!String.Equals(title, text.ToString(), StringComparison.Ordinal)) return true;
            try {
                uint pid; GetWindowThreadProcessId(h, out pid);
                using (var p = Process.GetProcessById((int)pid)) { if (String.Equals(p.ProcessName, "msedge", StringComparison.OrdinalIgnoreCase)) { target = h; return false; } }
            } catch { }
            return true;
        }, IntPtr.Zero);
        return target != IntPtr.Zero && MoveWindow(target, x, y, width, height, true);
    }
}
'@
        $eh = Get-EdgeWindowHeight $H
        for ($i = 0; $i -lt 40; $i++) { if ([ShellWin]::Place($Title, $X, $Y, $W, $eh)) { break }; Start-Sleep -Milliseconds 150 }
    } catch {}
}

function Initialize-Vendor {
    if (Test-Path (Join-Path $vend 'Microsoft.Web.WebView2.Wpf.dll')) { return $true }
    # نسخة محلية من مكتبات WebView2 الموجودة أصلًا على الجهاز (لا تنزيل ولا إضافتها إلى المستودع)
    $src = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Microsoft\TeamsMeetingAdd-in') -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'x64' } |
        Where-Object { Test-Path (Join-Path $_ 'Microsoft.Web.WebView2.Wpf.dll') } | Select-Object -First 1
    if (-not $src) { return $false }
    New-Item -ItemType Directory -Force -Path $vend | Out-Null
    foreach ($f in 'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.Wpf.dll') { Copy-Item (Join-Path $src $f) $vend -Force }
    $ld = Join-Path $src 'runtimes\win-x64\native\WebView2Loader.dll'
    if (Test-Path $ld) { Copy-Item $ld $vend -Force }
    return (Test-Path (Join-Path $vend 'WebView2Loader.dll'))
}
if (-not (Initialize-Vendor)) { Write-ShellError 'no_webview2' 'مكتبات WebView2 غير متوفرة في webview2 ولا في إضافة Teams'; Start-EdgeFallback 'no_webview2'; exit 3 }

Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public static class Dpi { [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(System.IntPtr v); [DllImport("user32.dll")] public static extern uint GetDpiForSystem(); [DllImport("shell32.dll")] public static extern int SetCurrentProcessExplicitAppUserModelID([MarshalAs(UnmanagedType.LPWStr)] string id); }'
[void][Dpi]::SetCurrentProcessExplicitAppUserModelID('MonitorWidget.App')   # هوية خاصة: زر شريط المهام لا يُجمَّع مع PowerShell
[void][Dpi]::SetProcessDpiAwarenessContext([IntPtr]-4)   # per-monitor v2: بدونه يرسم WebView2 بمقياس يخالف النافذة
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Xaml
try { Add-Type -Path (Join-Path $vend 'Microsoft.Web.WebView2.Core.dll'); Add-Type -Path (Join-Path $vend 'Microsoft.Web.WebView2.Wpf.dll') }
catch { Write-ShellError 'no_webview2' ([string]$_.Exception.Message); Start-EdgeFallback 'no_webview2'; exit 3 }   # مكتبات تالفة
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class RoundRgn {
    [DllImport("gdi32.dll")] static extern IntPtr CreateRoundRectRgn(int l, int t, int r, int b, int w, int h);
    [DllImport("user32.dll")] static extern int SetWindowRgn(IntPtr hwnd, IntPtr rgn, bool redraw);
    public static void Apply(IntPtr hwnd, int width, int height, int radius) {
        SetWindowRgn(hwnd, CreateRoundRectRgn(0, 0, width + 1, height + 1, radius * 2, radius * 2), true);
    }
    public static void Clear(IntPtr hwnd) { SetWindowRgn(hwnd, IntPtr.Zero, true); }
}
'@

function Brush([string]$hex) { [Windows.Media.BrushConverter]::new().ConvertFromString($hex) }
# ألوان الويدجت الداكنة (نفس edge/base/card/ink)
. (Join-Path $root 'identity.ps1')
# المظهر المعتمد (appearance.json) والألوان من web\css	okens.css: النافذة تطابق الصفحة والويدجت
$mode = 'auto'; $palName = 'teal'
try { $ap = Get-Content -LiteralPath (Join-Path $root 'appearance.json') -Raw -Encoding UTF8 | ConvertFrom-Json; if ($ap.mode) { $mode = $ap.mode }; if ($ap.palette) { $palName = $ap.palette } } catch {}
$sysLight = $false; try { $sysLight = ((Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -ErrorAction Stop).AppsUseLightTheme -eq 1) } catch {}
$isLight = switch ($mode) { 'light' { $true } 'dark' { $false } default { $sysLight } }
$P = Get-IdentityPalette $root $isLight $palName @{ base = '101F1D'; edge = '#344947'; ink = '#E5EEED'; muted = '#A8BFBC'; hover = '#203D35'; bad = '#B42318' }
$base = '#' + $P.base; $edge = $P.edge; $ink = $P.ink; $muted = $P.muted; $hover = $P.hover; $bad = $P.bad
$R = 14

$win = New-Object Windows.Window
$win.Title = $Title; $win.WindowStyle = 'None'; $win.ResizeMode = 'CanResize'; $win.AllowsTransparency = $false
$SF = Get-IdentityFonts $root   # خطوط الهوية نفسها التي يستخدمها الويدجت والواجهة
$win.Background = Brush $base; $win.FlowDirection = 'LeftToRight'; $win.FontFamily = $SF.ui
$sc = [Dpi]::GetDpiForSystem() / 96.0   # الوسائط بالبكسل الفعلي (كما يمررها الخادم والويدجت)
$win.Left = $X / $sc; $win.Top = $Y / $sc; $win.Width = $W / $sc; $win.Height = $H / $sc; $win.MinWidth = 640; $win.MinHeight = 480
$win.UseLayoutRounding = $true; $win.SnapsToDevicePixels = $true
$ico = Join-Path $root 'assets\icon.ico'
if (Test-Path $ico) { $win.Icon = [Windows.Media.Imaging.BitmapFrame]::Create((New-Object Uri $ico)) }
$chrome = New-Object Windows.Shell.WindowChrome
$chrome.CaptionHeight = 0; $chrome.ResizeBorderThickness = 6; $chrome.GlassFrameThickness = 0; $chrome.CornerRadius = 0; $chrome.UseAeroCaptionButtons = $false
[Windows.Shell.WindowChrome]::SetWindowChrome($win, $chrome)

$frame = New-Object Windows.Controls.Border
$frame.CornerRadius = $R; $frame.BorderThickness = 1; $frame.BorderBrush = Brush $edge; $frame.Background = Brush $base; $frame.ClipToBounds = $true
$grid = New-Object Windows.Controls.Grid
foreach ($rh in @('34', '*')) { $rd = New-Object Windows.Controls.RowDefinition; $rd.Height = $rh; [void]$grid.RowDefinitions.Add($rd) }

# شريط عنوان خفيف: سحب النافذة + تصغير/تكبير/إغلاق
$bar = New-Object Windows.Controls.Grid; $bar.Background = Brush '#00000000'
$tb = New-Object Windows.Controls.TextBlock; $tb.Text = $Title; $tb.FontFamily = $SF.head; $tb.Foreground = Brush $muted; $tb.FontSize = 12; $tb.VerticalAlignment = 'Center'; $tb.HorizontalAlignment = 'Right'; $tb.FlowDirection = 'RightToLeft'; $tb.Margin = '0,0,16,0'
[void]$bar.Children.Add($tb)
$btns = New-Object Windows.Controls.StackPanel; $btns.Orientation = 'Horizontal'; $btns.HorizontalAlignment = 'Left'; $btns.FlowDirection = 'LeftToRight'
function New-Cap([string]$glyph, [string]$hoverBg) {
    $b = New-Object Windows.Controls.Button; $b.Content = $glyph; $b.Width = 40; $b.Height = 28; $b.Margin = '0,3,2,0'; $b.FontSize = 13
    $b.Foreground = Brush $ink; $b.Background = Brush '#00000000'; $b.BorderThickness = 0; $b.Cursor = 'Hand'; $b.Focusable = $false
    $t = [xml]('<ControlTemplate xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" TargetType="Button"><Border x:Name="b" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" CornerRadius="7" Background="{TemplateBinding Background}"><ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/></Border><ControlTemplate.Triggers><Trigger Property="IsMouseOver" Value="True"><Setter TargetName="b" Property="Background" Value="' + $hoverBg + '"/></Trigger></ControlTemplate.Triggers></ControlTemplate>')
    $b.Template = [Windows.Markup.XamlReader]::Load((New-Object Xml.XmlNodeReader $t))
    [Windows.Shell.WindowChrome]::SetIsHitTestVisibleInChrome($b, $true)
    $b
}
$bMin = New-Cap ([string][char]0xE921) $hover; $bMax = New-Cap ([string][char]0xE922) $hover; $bCls = New-Cap ([string][char]0xE8BB) $bad
foreach ($b in @($bMin, $bMax, $bCls)) { $b.FontFamily = New-Object Windows.Media.FontFamily 'Segoe MDL2 Assets'; $b.FontSize = 10; [void]$btns.Children.Add($b) }
[void]$bar.Children.Add($btns)
$bar.Add_MouseLeftButtonDown({ param($s, $e) if ($e.ClickCount -eq 2) { $bMax.RaiseEvent((New-Object Windows.RoutedEventArgs ([Windows.Controls.Primitives.ButtonBase]::ClickEvent))) } else { try { $win.DragMove() } catch {} } })
$bMin.Add_Click({ $win.WindowState = 'Minimized' })
$bMax.Add_Click({ $win.WindowState = if ($win.WindowState -eq 'Maximized') { 'Normal' } else { 'Maximized' } })
$bCls.Add_Click({ $win.Close() })
[Windows.Controls.Grid]::SetRow($bar, 0); [void]$grid.Children.Add($bar)

$wv = New-Object Microsoft.Web.WebView2.Wpf.WebView2
$wv.DefaultBackgroundColor = [Drawing.ColorTranslator]::FromHtml($base)
$env2 = $null
$prof = Join-Path $env:LOCALAPPDATA 'MonitorWidget\shellwin'; New-Item -ItemType Directory -Force -Path $prof | Out-Null
$wv.CreationProperties = New-Object Microsoft.Web.WebView2.Wpf.CoreWebView2CreationProperties
$wv.CreationProperties.UserDataFolder = $prof
[Windows.Controls.Grid]::SetRow($wv, 1); [void]$grid.Children.Add($wv)
$frame.Child = $grid; $win.Content = $frame

$wv.Add_CoreWebView2InitializationCompleted({
    param($s, $e)
    if (-not $e.IsSuccess) { Write-ShellError 'init_failed' ([string]$e.InitializationException); $script:fallbackReason = 'init_failed'; $win.Close(); return }   # السقوط بعد إغلاق النافذة
    [void](Write-ShellState 'webview2' '')   # نجح المحرك: يزول تحذير صفحة الصحة
    $c = $wv.CoreWebView2
    $c.Settings.AreDefaultContextMenusEnabled = $false; $c.Settings.IsStatusBarEnabled = $false; $c.Settings.AreDevToolsEnabled = $false
    $c.Settings.IsPasswordAutosaveEnabled = $false; $c.Settings.IsGeneralAutofillEnabled = $false
    # التنقل داخل المرصد فقط؛ أي رابط خارجي يُفتح في المتصفح الافتراضي
    $c.Add_NewWindowRequested({ param($s2, $a) $a.Handled = $true; if ($a.Uri -match '^https?://') { Start-Process $a.Uri } })
    $c.Add_NavigationStarting({ param($s2, $a) if ($a.Uri -notmatch '^http://127\.0\.0\.1:47832/') { $a.Cancel = $true; if ($a.Uri -match '^https?://') { Start-Process $a.Uri } } })
    $c.Add_DocumentTitleChanged({ })    # العنوان ثابت: يعتمد عليه الويدجت لإيجاد النافذة
    $c.Add_NavigationCompleted({ if ($env:MW_SHELL_DEBUG) { $r = $wv.ExecuteScriptAsync('innerWidth+"x"+innerHeight+" dpr="+devicePixelRatio'); $r.ContinueWith([Action[object]]{ param($t) Set-Content -LiteralPath $env:MW_SHELL_DEBUG -Value ($t.ToString() + " ctl=" + $wv.ActualWidth + "x" + $wv.ActualHeight) }) | Out-Null } })
    $c.Navigate($Url)
})

function Set-Corners {
    $hw = (New-Object Windows.Interop.WindowInteropHelper $win).Handle
    if ($hw -eq [IntPtr]::Zero) { return }
    if ($win.WindowState -eq 'Maximized') { [RoundRgn]::Clear($hw); $frame.CornerRadius = 0; return }
    $frame.CornerRadius = $R
    $src = [Windows.PresentationSource]::FromVisual($win); $sc = if ($src) { $src.CompositionTarget.TransformToDevice.M11 } else { 1.0 }
    [RoundRgn]::Apply($hw, [int]($win.ActualWidth * $sc), [int]($win.ActualHeight * $sc), [int]($R * $sc))
}
$win.Add_SourceInitialized({ Set-Corners })
$win.Add_SizeChanged({ Set-Corners })
$win.Add_StateChanged({ Set-Corners })
$win.Add_Loaded({ $win.Activate(); try { [void]$wv.EnsureCoreWebView2Async() } catch { Write-ShellError 'init_failed' ([string]$_.Exception.Message); $script:fallbackReason = 'init_failed'; $win.Close() } })
$win.Add_Closed({ try { $wv.Dispose() } catch {} })
[void]$win.ShowDialog()
if ($script:fallbackReason) { Start-EdgeFallback $script:fallbackReason }
