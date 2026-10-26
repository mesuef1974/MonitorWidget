# Run once as Administrator in Windows PowerShell. Creates a startup task that runs boot_trace.ps1 as SYSTEM.
# Remove later with:  Unregister-ScheduledTask -TaskName MonitorWidget-BootTrace -Confirm:$false
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}\boot_trace.ps1"' -f $dir)
$t = New-ScheduledTaskTrigger -AtStartup
$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 6) -StartWhenAvailable
Register-ScheduledTask -TaskName 'MonitorWidget-BootTrace' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
Get-ScheduledTask -TaskName 'MonitorWidget-BootTrace' | Select-Object TaskName, State
