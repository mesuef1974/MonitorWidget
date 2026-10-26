$d = Split-Path -Parent $MyInvocation.MyCommand.Path
$user = "$env:USERDOMAIN\$env:USERNAME"
$act = New-ScheduledTaskAction -Execute "$d\purge.exe" -WorkingDirectory $d
$pr = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -MultipleInstances IgnoreNew -Hidden
Register-ScheduledTask -TaskName 'MemoryPurge-OnDemand' -Action $act -Principal $pr -Settings $set -Description 'On-demand RAM purge (standby/modified/working sets) - no console window' -Force | Out-Null
'ok2' | Set-Content "$d\install_purge.ok"
