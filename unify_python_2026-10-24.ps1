# Run as Administrator (prepared, NOT run by the assistant). Repoints the two Python tasks from 3.13 to 3.12
# (the project is stdlib-only; widget.ps1 and the shortcut already use 3.12). Re-run is harmless.
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe"
if (-not (Test-Path $py)) { throw "missing $py" }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
foreach ($t in @(@{ n = 'MonitorWidget-Agent'; s = 'agent.py' }, @{ n = 'MonitorWidget-Collector'; s = 'collector.py' })) {
    $act = New-ScheduledTaskAction -Execute $py -Argument ('"' + (Join-Path $root $t.s) + '"') -WorkingDirectory $root
    Set-ScheduledTask -TaskName $t.n -Action $act | Out-Null
    "{0} -> {1}" -f $t.n, $py
}
