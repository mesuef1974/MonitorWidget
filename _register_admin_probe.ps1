# تسجيل مهمة المسبار المرتفع MonitorWidget-AdminProbe من جديد (للقراءة فقط: تآكل القرص والبطارية، كل 30 دقيقة وعند الدخول).
# يُشغَّل من PowerShell «كمسؤول». يسجّل المسبار دائمًا من النسخة المحمية ProgramData\MonitorWidget\bin\healthprobe.ps1
# بسياسة RemoteSigned، ويكتب النتيجة في ProgramData\MonitorWidget\out\admin_probe.json، تمامًا كما يسجّله install_secure_2026-10-24.ps1.
# لا يسجّل أبدًا من مجلد المستخدم: ملفاته يكتبها المستخدم العادي، فتعديلها كان يعمل مسؤولًا في التشغيل التالي.
# لا ينسخ شيئًا إلى ProgramData: النسخة المحمية ينشئها ويتحقق من بصمتها install_secure_2026-10-24.ps1 وحده.
$ErrorActionPreference = 'Stop'
$id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'شغّل هذا السكربت من PowerShell بصلاحية مسؤول.' }
$secure = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MonitorWidget'   # مجلد الجهاز لا متغير البيئة الموروث
$bin = Join-Path $secure 'bin'
$out = Join-Path $secure 'out'
$probe = Join-Path $bin 'healthprobe.ps1'
if (-not (Test-Path -LiteralPath $probe -PathType Leaf) -or -not (Test-Path -LiteralPath (Join-Path $out 'install_manifest.json') -PathType Leaf)) {
    throw "النسخة المحمية غير مثبّتة ($probe). شغّل install_secure_2026-10-24.ps1 كمسؤول أولًا؛ لا تسجيل من مجلد المستخدم."
}
$pwsh = Join-Path $env:ProgramFiles 'PowerShell\7\pwsh.exe'
$user = "$env:USERDOMAIN\$env:USERNAME"
$cmd = "& '$probe' -SkipUpdates | Set-Content -LiteralPath '$out\admin_probe.json' -Encoding UTF8"
$a = New-ScheduledTaskAction -Execute $pwsh -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy RemoteSigned -Command `"$cmd`"" -WorkingDirectory $bin
$t1 = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$t2 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$p = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'MonitorWidget-AdminProbe' -Action $a -Trigger @($t1, $t2) -Principal $p -Settings $s -Description 'Read-only hardware probe, runs from the protected folder' -Force | Out-Null
Start-ScheduledTask -TaskName 'MonitorWidget-AdminProbe'
'سُجّل المسبار من النسخة المحمية: ' + $probe
