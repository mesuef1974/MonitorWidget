# يجعل الويجت يعمل بصلاحية مسؤول عند الدخول (المهمة MonitorWidget-Launch → Highest). يُشغَّل مرة واحدة كمسؤول.
# تراجع: Register-ScheduledTask -Xml (Get-Content backups\MonitorWidget-Launch.pre_admin.xml -Raw) -TaskName MonitorWidget-Launch -Force
$ErrorActionPreference = 'Stop'
$id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'شغّل هذا السكربت من PowerShell بصلاحية مسؤول.' }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$name = 'MonitorWidget-Launch'
$old = Get-ScheduledTask -TaskName $name -ErrorAction Stop
New-Item -ItemType Directory -Force -Path (Join-Path $root 'backups') | Out-Null
Export-ScheduledTask -TaskName $name | Set-Content -LiteralPath (Join-Path $root 'backups\MonitorWidget-Launch.pre_admin.xml') -Encoding Unicode
$user = "$env:USERDOMAIN\$env:USERNAME"
$pr = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
$trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$task = New-ScheduledTask -Action ($old.Actions | Select-Object -First 1) -Trigger $trg -Principal $pr -Settings $set -Description 'Mini widget at logon, elevated. The widget then starts MonitorWidget-Agent (server) on demand.'
$wp = [regex]::Escape((Join-Path $root 'widget.ps1'))
Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'powershell.exe', 'pwsh.exe' -and $_.CommandLine -match $wp } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep 1
Register-ScheduledTask -TaskName $name -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $name
Start-Sleep 8
$w = Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'powershell.exe', 'pwsh.exe' -and $_.CommandLine -match $wp }
'الويجت يعمل: ' + [bool]$w
(Get-ScheduledTask -TaskName $name).Principal.RunLevel
'المشغّلات: ' + @((Get-ScheduledTask -TaskName $name).Triggers).Count + ' (المطلوب 1: عند الدخول)'
