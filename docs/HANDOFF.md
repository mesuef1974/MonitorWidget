---
updated: 2026-10-27T14:00:34+03:00
head: d7fdfe4
branch: main
dirty: 1
dirty_files: ocs/HANDOFF.md
---
# مرصد الجهاز — بطاقة التسليم

## الحالة
- main عند d7fdfe4. اليوم 27/10 دُمجت PR 26 (الخطوط)، PR 27 (أحجام Amiri)، PR 28 (uninstall_admin_backend.ps1 لإرجاع مهمة MonitorWidget-Agent إلى Limited + تحقق حقيقي في restart_admin/restart_agent)، PR 29 (حذف Windows.old عبر مهمتَي MonitorWidget-WinOld/-Early المرتفعتين الثابتتين من المثبّت المحمي؛ الخادم لا يستدعي takeown بعد الآن). لا PR مفتوح.
- البند 3 المعاد صياغته (الخادم بصلاحية عادية دائمًا) اكتمل في الكود. purge standby كان أصلًا مهمة MemoryPurge-Standby. المتبقي أفعال المالك كمسؤول: (أ) تشغيل install_secure_2026-10-24.ps1 المولَّد ليسجّل مهمتَي WinOld وينسخ winold_delete.ps1؛ (ب) تشغيل uninstall_admin_backend.ps1 ليعود الوكيل Limited؛ ثم التحقق أن /api/mini يعيد admin=false وأن /fonts يعيد 200.
- ما زال يحتاج خادمًا مرتفعًا بعد ذلك (لم يُحوَّل): extras بـ admin=True (wintemp) وDISM (_admin_or_err في extras.py) وربما swdist. تُحوَّل لاحقًا على النمط نفسه إن أراد المالك.
- الخادم الحي حتى 13:35 ما زال العملية المرتفعة من 9:22؛ restart_admin.ps1 القديم كان يعلن النجاح زورًا (أُصلح في PR 28).
- check_all وrun_all تمرّ. origin/fix/gpu-page2-density فرع قديم غير مدمج؛ مساحة .claude/worktrees/compassionate-ptolemy-07d621 لم تُحذف. tests/report.txt وtests/live_report.json لا يُلتزمان. وكيل التطوير على 47900 قد يكون ما زال يعمل.
## الخطوة الجارية
- لا عمل جارٍ؛ بانتظار المالك لتشغيل المثبّتين كمسؤول (انظر الحالة).
## الخطوة التالية (أمر قابل للتنفيذ)
- بعد تشغيل المالك للسكربتين: تحقق بـ python -c "import space;print(space._winold_task_ready())" (يجب True) وبـ /api/mini admin=false؛ ثم البند 4 من الخارطة: Pester لدوال widget.ps1 + Playwright للصفحات في CI مبني على tour.cjs. إن أراد المالك استكمال فصل الصلاحيات: wintemp وDISM في extras.py على نمط WinOld.
## خارطة الطريق المتفق عليها (2026-10-27)
- المنتج أداة شخصية: لا تحزيم ولا توقيع ولا ويدجت .NET.
- 1) إغلاق المعلّق حيًّا وإصلاح ما ينكسر (أزرار المساحة، قائمة الويدجت وألوانه، فتح الغلاف من الويدجت، نقل إخفاء/تأجيل التوصيات) + إصلاح وصف delivery-optimization (القاعدة صحيحة).
- 2) تحذير ظاهر في صفحة الصحة عند سقوط WebView2 إلى Edge.
- 3) الخادم بصلاحية عادية دائمًا؛ الأفعال المرتفعة (purge، Windows.old، لاحقًا wintemp/DISM) مهام مجدولة ثابتة بقائمة بيضاء على نمط Cleanup_v2 (أُعيدت صياغته 27/10: nvidia-smi لا يحتاج مسؤولًا).
- 4) فحص مرئي: Pester لدوال widget.ps1 + Playwright للصفحات في CI مبني على tour.cjs.
- 5) عدّاد فتح لكل صفحة لأسبوعين ثم قصّ غير المستخدم.
- 6) تجزئة server.py وحدة في كل PR بدءًا بالمسارات الهدّامة مع فحص 428.

## ما بُني (مختصر)
- الغلاف app_shell.ps1 (WPF بلا إطار، WebView2 من مكتبات Teams، يرجع لـ Edge).
- الهوية المشتركة identity.ps1 تقرأ web/css/tokens.css للويدجت والغلاف.
- الواجهة web/index.html + web/css (tokens/base/layout/components/utilities) يقدّمها server.py بـ ETag.

## معلّق / غير مُتحقق
- أزرار معاينة/تنظيف المساحة بعد التقسيم لم تُجرَّب فعليًا.
- القائمة الجديدة للويدجت وألوانه من tokens لم تُجرَّب على الويدجت الحي؛ ولم يُفتح الغلاف من زر الويدجت.
- إخفاء/تأجيل التوصيات: لم يُنقل من الصفحة القديمة إلى web/index.html.

## قواعد ثابتة
- العربية الفصحى والإيجاز؛ لا رسائل إلى جلسة «خفيفة 0101»؛ الدمج بـ squash بعد نجاح CI دون سؤال (تكليف المالك 27/10).
- فرع من main ← PR ← squash بعد CI؛ تحديث الفرع المحمي من main قبل الدمج.
- الأنماط في web/css بمتغيرات tokens.css فقط؛ المضمّن للقيم القادمة من البيانات.
- لا تقتل عمليات PowerShell بالجملة (قد تقتل الويدجت). إعادة التشغيل: restart_admin.ps1 بصلاحية مسؤول.

## أوامر التحقق
- python tests/check_all.py
- gh pr view 14 --json mergeStateStatus,statusCheckRollup
- الاختبار الحي: python web/dev_proxy.py 47900 ثم shots/lay/tour.cjs (غير متتبَّع)
