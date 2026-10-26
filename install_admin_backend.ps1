# تثبيت الخلفية (الوكيل + الخادم) لتعمل دائمًا بصلاحية مسؤول، دون سؤال UAC في كل مرة.
# يُشغَّل مرة واحدة من PowerShell «كمسؤول». بعده: الإقلاع يشغّل الويجت فقط (MonitorWidget-Launch)،
# والويجت يطلب هذه المهمة (عند الإقلاع وعند أي انقطاع)، فيفتح التطبيق مرفوعًا.
# تراجع: Register-ScheduledTask -Xml (Get-Content backups\MonitorWidget-Agent.pre_admin.xml -Raw) -TaskName MonitorWidget-Agent -Force
$ErrorActionPreference = 'Stop'
$id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'شغّل هذا السكربت من PowerShell بصلاحية مسؤول.' }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$name = 'MonitorWidget-Agent'
$old = Get-ScheduledTask -TaskName $name -ErrorAction Stop
New-Item -ItemType Directory -Force -Path (Join-Path $root 'backups') | Out-Null
Export-ScheduledTask -TaskName $name | Set-Content -LiteralPath (Join-Path $root 'backups\MonitorWidget-Agent.pre_admin.xml') -Encoding Unicode
$act = $old.Actions | Select-Object -First 1     # نفس مسار بايثون والوسائط الحالية
$user = "$env:USERDOMAIN\$env:USERNAME"
$pr = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
$task = New-ScheduledTask -Action $act -Principal $pr -Settings $set -Description 'Always-on monitor agent + server, elevated. Started on demand by the widget (no logon trigger).'
Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
$ap = [regex]::Escape($root) + '[\/](agent|server)\.py'
Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'python.exe', 'pythonw.exe' -and $_.CommandLine -match $ap } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep 2
Register-ScheduledTask -TaskName $name -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $name
$tok = (Get-Content (Join-Path $root '.token') | Select-Object -First 1)
$ok = $false
for ($i = 0; $i -lt 40 -and -not $ok; $i++) {
    Start-Sleep 1
    try { $m = Invoke-RestMethod 'http://127.0.0.1:47832/api/mini' -Headers @{ 'X-Token' = $tok } -TimeoutSec 3 -NoProxy; $ok = $true } catch {}
}
if ($ok) { "الخادم يعمل. admin = " + $m.admin } else { 'لم يستجب الخادم خلال 40 ثانية؛ راجع monitor.log' }
(Get-ScheduledTask -TaskName $name | Select-Object -ExpandProperty Principal | Select-Object RunLevel | Out-String).Trim()
'المشغّلات: ' + (@((Get-ScheduledTask -TaskName $name).Triggers).Count) + ' (المطلوب 0)'
