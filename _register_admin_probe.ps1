# One-time elevated registration. Creates a READ-ONLY scheduled task that runs healthprobe.ps1 with highest privileges
# every 30 minutes and writes admin_probe.json. Nothing is changed on the system beyond this task.
$root = 'C:\Users\mesue\Documents\Scripts\MonitorWidget'
$pwsh = 'C:\Program Files\PowerShell\7\pwsh.exe'
$user = "$env:USERDOMAIN\$env:USERNAME"
$cmd  = "& '$root\healthprobe.ps1' -SkipUpdates | Set-Content -LiteralPath '$root\admin_probe.json' -Encoding UTF8"
$a = New-ScheduledTaskAction -Execute $pwsh -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$cmd`"" -WorkingDirectory $root
$t1 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$p = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'MonitorWidget-AdminProbe' -Action $a -Trigger @($t1, $t2) -Principal $p -Settings $s -Description 'Read-only hardware probe with elevated rights (disk wear, battery). Writes admin_probe.json.' -Force | Out-Null
Start-ScheduledTask -TaskName 'MonitorWidget-AdminProbe'
"registered"
