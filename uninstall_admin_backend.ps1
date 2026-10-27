# إرجاع الخلفية (الوكيل + الخادم) إلى صلاحية عادية: عكس install_admin_backend.ps1.
# قرار المالك 27/10/2026: الخادم بصلاحية عادية دائمًا؛ ما يحتاج المسؤول يمرّ عبر مهام مجدولة مرتفعة ثابتة
# (MemoryPurge-* وCleanup_v2_* وMonitorWidget-WinOld*) يستدعيها الخادم بـ schtasks /run.
# يعمل في Windows PowerShell 5.1 (بلا -NoProxy؛ المعامل غير موجود فيها ويُسقط كل محاولة بصمت). يُشغَّل مرة واحدة من PowerShell «كمسؤول» لأن المهمة الحالية Highest ولا يمكن تعديلها أو إيقاف عملياتها من جلسة عادية.
$ErrorActionPreference = 'Stop'
$id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'شغّل هذا السكربت من PowerShell بصلاحية مسؤول.' }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$name = 'MonitorWidget-Agent'
$port = 47832
$old = Get-ScheduledTask -TaskName $name -ErrorAction Stop
New-Item -ItemType Directory -Force -Path (Join-Path $root 'backups') | Out-Null
Export-ScheduledTask -TaskName $name | Set-Content -LiteralPath (Join-Path $root 'backups\MonitorWidget-Agent.pre_limited.xml') -Encoding Unicode

$act = $old.Actions | Select-Object -First 1     # نفس مسار بايثون والوسائط الحالية
$user = "$env:USERDOMAIN\$env:USERNAME"
$pr = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
$task = New-ScheduledTask -Action $act -Principal $pr -Settings $set -Description 'Always-on monitor agent + server, limited rights. Elevated work runs through fixed scheduled tasks. Started on demand by the widget (no logon trigger).'

$before = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
# يجب قتل الوكيل القديم أيضًا لا الخادم وحده: الوكيل يحمل mutex النسخة الواحدة فيُخرج الوكيل المحدود الجديد بصمت،
# ثم يعيد تشغيل الخادم بصلاحيته هو (المرتفعة). restart_admin.ps1 القديم كان يطلقه بسطر «agent.py» نسبي بلا مسار،
# لذا نطابق المسار الكامل تحت $root أو agent.py عاريًا، ونقتل كذلك أبَ العملية المستمعة إن كان بايثون.
$all = Get-CimInstance Win32_Process
$ap = '(' + [regex]::Escape($root) + '[\\/](agent|server)\.py)|(^(\S+|"[^"]+")\s+"?agent\.py"?\s*$)'
$parent = if ($before) { ($all | Where-Object ProcessId -eq $before).ParentProcessId } else { 0 }
$victims = @($all | Where-Object {
    ($_.Name -in 'python.exe', 'pythonw.exe' -and $_.CommandLine -match $ap) -or
    ($before -and $_.ProcessId -eq $before) -or
    ($parent -and $_.ProcessId -eq $parent -and $_.Name -match '^pythonw?\.exe$')
})
$victims | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1
$left = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'python.exe', 'pythonw.exe' -and $_.CommandLine -match $ap })
if ($left.Count) { throw ('بقي وكيل/خادم قديم حيًّا: ' + (($left | ForEach-Object ProcessId) -join ', ')) }
Start-Sleep 2
Register-ScheduledTask -TaskName $name -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $name

$tok = (Get-Content (Join-Path $root '.token') | Select-Object -First 1)
$m = $null
for ($i = 0; $i -lt 40 -and -not $m; $i++) {
    Start-Sleep 1
    try { $m = Invoke-RestMethod "http://127.0.0.1:$port/api/mini" -Headers @{ 'X-Token' = $tok } -TimeoutSec 3 } catch {}
}
if (-not $m) { throw 'لم يستجب الخادم خلال 40 ثانية؛ راجع monitor.log' }
$after = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
if ($before -and $after -eq $before) { throw "العملية القديمة $before ما زالت تستمع؛ لم تُستبدل." }
if ($m.admin) { throw 'الخادم الجديد ما زال مرتفعًا؛ راجع مبدأ المهمة.' }
'تم: الخادم يعمل بصلاحية عادية (PID ' + $after + '). RunLevel = ' + (Get-ScheduledTask -TaskName $name).Principal.RunLevel
'المشغّلات: ' + (@((Get-ScheduledTask -TaskName $name).Triggers).Count) + ' (المطلوب 0)'
