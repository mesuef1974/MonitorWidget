# يُشغَّل مرة واحدة من PowerShell «كمسؤول». ينشئ مهمة عند الإقلاع تشغّل boot_trace.ps1 بحساب SYSTEM.
# المهمة تعمل من النسخة المحمية ProgramData\MonitorWidget\bin\boot_trace.ps1 (المسؤولون وSYSTEM كتابةً، المستخدمون قراءةً فقط)
# لا من مجلد المستخدم: سكربت يكتبه المستخدم العادي ويشغّله SYSTEM ثغرة رفع صلاحية. المخرجات في ProgramData\MonitorWidget\out\boot_trace.
# المجلد المحمي وصلاحياته ينشئها install_secure_2026-10-24.ps1؛ هذا السكربت ينسخ boot_trace.ps1 إليه ويتحقق من بصمته بعد النسخ.
# الإزالة لاحقًا: Unregister-ScheduledTask -TaskName MonitorWidget-BootTrace -Confirm:$false
$ErrorActionPreference = 'Stop'
$id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'شغّل هذا السكربت من PowerShell بصلاحية مسؤول.' }
$src = Join-Path $PSScriptRoot 'boot_trace.ps1'
$secure = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MonitorWidget'   # مجلد الجهاز لا متغير البيئة الموروث
$bin = Join-Path $secure 'bin'
$out = Join-Path $secure 'out'
if (-not (Test-Path -LiteralPath (Join-Path $out 'install_manifest.json') -PathType Leaf)) {
    throw "المجلد المحمي غير مثبّت ($secure). شغّل install_secure_2026-10-24.ps1 كمسؤول أولًا."
}
$dest = Join-Path $bin 'boot_trace.ps1'
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $src).Hash
Copy-Item -LiteralPath $src -Destination $dest -Force   # يرث صلاحيات bin المحمية
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $dest).Hash -ne $hash) { throw 'النسخة المحمية تختلف عن المصدر بعد النسخ؛ لم تُسجَّل المهمة.' }
$logs = Join-Path $out 'boot_trace'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File "{0}" -OutDir "{1}"' -f $dest, $logs) -WorkingDirectory $bin
$t = New-ScheduledTaskTrigger -AtStartup
$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 6) -StartWhenAvailable
Register-ScheduledTask -TaskName 'MonitorWidget-BootTrace' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
Get-ScheduledTask -TaskName 'MonitorWidget-BootTrace' | Select-Object TaskName, State
'المخرجات: ' + (Join-Path $logs 'boot_trace.csv') + '  (SHA256 ' + $hash + ')'
