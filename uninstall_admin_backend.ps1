# إرجاع الخلفية كلها إلى صلاحية عادية: عكس install_admin_backend.ps1.
# كان هذا السكربت يرجع مهمة MonitorWidget-Agent وحدها، فتبقى MonitorWidget-Server-Logon (تشغّل server.py عند الدخول)
# وMonitorWidget-Collector بصلاحية مسؤول، ويعود الخادم مرتفعًا من مهمة الدخول. صار غلافًا لـ set_backend_privilege.ps1 -Level Limited
# الذي يعدّل مستوى التشغيل وحده في المهام الثلاث، ويحفظ نسخة XML لكل منها في backups، ويستبدل العمليات القديمة ويتحقق من admin=False.
# المهام المرتفعة الثابتة (MemoryPurge-* وMonitorWidget-WinOld* وMonitorWidget-Clean-* وMonitorWidget-Gpu-Dwm وMonitorWidget-AdminProbe)
# تبقى مرتفعة عمدًا: يستدعيها الخادم المحدود بـ schtasks /run وتعمل من النسخة المحمية في ProgramData.
# يرفع نفسه بطلب UAC واحد بلا نافذة إن لم يُشغَّل كمسؤول. يشغّله المالك؛ لا تشغّله جلسة نموذج.
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'set_backend_privilege.ps1') -Level Limited
exit $LASTEXITCODE
