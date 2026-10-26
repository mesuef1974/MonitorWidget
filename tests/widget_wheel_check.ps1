$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
$source=Get-Content -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) 'widget.ps1') -Raw
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors.Message -join '; ')}
$factory=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'New-ActionButton'},$true)
. ([scriptblock]::Create($factory.Extent.Text))
$details=New-Object Windows.Controls.StackPanel
$chart=New-Object Windows.Controls.Canvas;$chart.Height=96
$mark=New-Object Windows.Shapes.Rectangle;$mark.Width=200;$mark.Height=40;$mark.Fill=[Windows.Media.Brushes]::Green;[void]$chart.Children.Add($mark)
$button=New-ActionButton 'زر اختبار';$button.Content='تشغيل';$button.Height=40
$toggle=New-ActionButton 'مفتاح اختبار' $true;$toggle.Content='جدولة';$toggle.Height=40
$script:clicks=0;$button.Add_Click({$script:clicks++});$toggle.Add_Click({$script:clicks++})
foreach($child in @($chart,$button,$toggle)){[void]$details.Children.Add($child)}
1..20 | ForEach-Object {$block=New-Object Windows.Controls.TextBlock;$block.Text='صف تفاصيل';$block.Height=30;[void]$details.Children.Add($block)}
# Execute only the real ScrollViewer construction; all data, windows and actions stay isolated.
$start=$source.IndexOf('$scroll = New-Object Windows.Controls.ScrollViewer;')
$end=$source.IndexOf('$UI.expander = New-Object Windows.Controls.Expander;',$start)
if($start -lt 0 -or $end -lt 0){throw 'Actual details scroll construction unavailable'}
. ([scriptblock]::Create($source.Substring($start,$end-$start)))
$scroll.Width=284;$scroll.Height=140;$scroll.MaxHeight=140
$null=$scroll.ApplyTemplate();$scroll.Measure([Windows.Size]::new(284,140));$scroll.Arrange([Windows.Rect]::new(0,0,284,140));$scroll.UpdateLayout()
if($scroll.ScrollableHeight -le 0){throw 'Actual ScrollViewer does not expose overflow'}
foreach($target in @($details,$chart,$mark,$button,$toggle)){
    $scroll.ScrollToVerticalOffset(0);$scroll.UpdateLayout()
    $down=New-Object Windows.Input.MouseWheelEventArgs([Windows.Input.Mouse]::PrimaryDevice,[Environment]::TickCount,-120);$down.RoutedEvent=[Windows.Input.Mouse]::MouseWheelEvent
    $target.RaiseEvent($down);$scroll.UpdateLayout();$afterDown=$scroll.VerticalOffset
    if($afterDown -le 0){throw ('Mouse wheel down did not scroll over '+$target.GetType().Name)}
    $up=New-Object Windows.Input.MouseWheelEventArgs([Windows.Input.Mouse]::PrimaryDevice,[Environment]::TickCount,120);$up.RoutedEvent=[Windows.Input.Mouse]::MouseWheelEvent
    $target.RaiseEvent($up);$scroll.UpdateLayout()
    if($scroll.VerticalOffset -ge $afterDown){throw ('Mouse wheel up did not scroll over '+$target.GetType().Name)}
}
if($script:clicks -ne 0 -or $toggle.IsChecked -eq $true){throw 'Mouse wheel activated a control'}
Write-Output 'PASS actual native WPF ScrollViewer: wheel down/up over content, chart canvas, chart mark, action button and toggle; no commands activated'
Write-Output 'Synthetic routed input only; no native window shown, desktop input injected or real operation executed.'
