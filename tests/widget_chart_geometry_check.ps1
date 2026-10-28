$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework
$source=Get-Content -LiteralPath (Join-Path (Split-Path $PSScriptRoot -Parent) 'widget.ps1') -Raw
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors.Message -join '; ')}
. (Join-Path $PSScriptRoot 'widget_test_identity.ps1')   # الهوية المشتركة من جذر المستودع قبل تحميل الدوال المستخرجة
foreach($name in @('Brush','Get-Palette','New-Text','Get-ChartSummary','Draw-Chart')){
    $fn=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}.GetNewClosure(),$true)
    if(-not $fn){throw "Missing $name"};. ([scriptblock]::Create((Resolve-WidgetFunctionText $fn.Extent.Text)))
}
$script:S=@{palette='teal'};$script:C=Get-Palette $false;$script:brushCache=@{};$script:THR=@{cmW=85;cmB=95}
$UI=@{chart=New-Object Windows.Controls.Canvas;chartTitle=New-Object Windows.Controls.TextBlock};$UI.chart.Height=96;$UI.chart.FlowDirection='LeftToRight'
foreach($width in @(220,242,264,284)){
    $UI.chart.Width=$width;$UI.chart.Measure([Windows.Size]::new($width,96));$UI.chart.Arrange([Windows.Rect]::new(0,0,$width,96));$UI.chart.UpdateLayout()
    Draw-Chart @(20,25,35,40,30,28) @(34,38,39,40,38,37)
    $UI.chart.InvalidateMeasure();$UI.chart.Measure([Windows.Size]::new($width,96));$UI.chart.Arrange([Windows.Rect]::new(0,0,$width,96));$UI.chart.UpdateLayout()
    $labels=@($UI.chart.Children|Where-Object {$_ -is [Windows.Controls.TextBlock]})
    if($labels.Count -ne 2 -or -not ($labels.Text -contains '100%') -or -not ($labels.Text -contains '10%')){throw 'Axis labels lost their full percentage'}
    foreach($label in $labels){$x=[Windows.Controls.Canvas]::GetLeft($label);$y=[Windows.Controls.Canvas]::GetTop($label);if($x -lt 0 -or $y -lt 0 -or $x+$label.ActualWidth -gt $width+.5 -or $y+$label.ActualHeight -gt 96.5 -or $label.FontSize -lt 11){throw 'Chart percentage label clipped or too small'}}
    foreach($line in @($UI.chart.Children|Where-Object {$_ -is [Windows.Shapes.Polyline]})){foreach($point in $line.Points){if($point.X -lt 32 -or $point.X -gt $width-8+.5 -or $point.Y -lt -.5 -or $point.Y -gt 96.5){throw 'Chart data exceeds available plot or intrudes on labels'}}}
    # المحوران مرسومان لقارئ العين فقط، والبديل النصي للرسم كله اسم العنوان
    if(@($labels|Where-Object {$_ -isnot [QuietText]}).Count){throw 'Axis labels exposed to screen readers'}
    if([Windows.Automation.AutomationProperties]::GetName($UI.chartTitle) -ne 'آخر 30 دقيقة: التزام الذاكرة بين 20% و40%، الآن 28%؛ الذاكرة الفعلية بين 34% و40%، الآن 37%'){throw 'Chart text alternative missing or wrong'}
}
if($source -match '\$UI.chart.Width = \$IW - 20' -or $source -notmatch '\$UI.bar.Add_SizeChanged\(' -or $source -match '\$UI.bar.Clip = [^\r\n]*\$IW - 20'){throw 'Chart/composition retained fixed inner width outside scrollbar viewport'}
Write-Output 'PASS native chart at220/242/264/284 DIP: full percentage labels inside canvas, >=11DIP text, data reserves32DIP axis space; quiet axis labels and a text alternative on the chart title; chart and composition width follow available viewport'
