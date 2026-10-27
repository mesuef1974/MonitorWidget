---
updated: 2026-10-27T15:20:35+03:00
head: 6955f7f
branch: main
dirty: 1
dirty_files: ocs/HANDOFF.md
---
# مرصد الجهاز — بطاقة التسليم

## الحالة
- main عند 6955f7f = PR 32 (wintemp وswdist وDISM عبر مهام مجدولة مرتفعة ثابتة MonitorWidget-Clean-WinTemp/-SwDist/-Dism من elevated_clean.ps1 في المجلد المحمي، مع مفتاح تفعيل لكل فعل مرتفع في جدول تنظيف المساحة: الإعداد space.elevated، /api/space action=elevated). قبله PR 30 و31 (إصلاح سكربت الإرجاع). لا PR مفتوح. الشجرة نظيفة عدا docs/HANDOFF.md.
- فصل الصلاحيات حيّ: الخادم بصلاحية عادية (admin=false) منذ ~15:00، أُعيد تشغيله بـ restart_agent.ps1 بعد PR 32 ويحمل الكود الجديد؛ /api/space يعيد elevated لكل نوع (winold ready=true؛ الثلاثة الجديدة ready=false لأن المثبّت لم يُشغَّل بعد). مفتاح التفعيل مُجرَّب حيًّا (تعطيل/تفعيل dism).
- ينتظر المالك كمسؤول مرة واحدة: install_secure_2026-10-24.ps1 (المولَّد من build_secure_install.py، يحوي تجزئة elevated_clean.ps1) لتسجيل المهام الثلاث. بعدها يصبح wintemp وswdist وDISM قابلة للتنفيذ من الواجهة بلا خادم مرتفع.
- فشلات سابقة على main (ليست من هذه التغييرات): tests/appearance_regression.py (NameError: re في do_GET المستخرج، من مسار /fonts في PR 26)، tests/widget_close_check.ps1 (سطر 10)، tests/widget_button_contrast_check.ps1 (سطر 37). تُصلح في PR مستقل.
- ملاحظة: uninstall_admin_backend.ps1 طبع «المشغّلات: 1 (المطلوب 0)»؛ غير ضار.
- check_all يمر. origin/fix/gpu-page2-density فرع قديم غير مدمج؛ مساحة .claude/worktrees/compassionate-ptolemy-07d621 لم تُحذف. tests/report.txt وtests/live_report.json لا يُلتزمان. وكيل التطوير على 47900 يعمل.
## الخطوة الجارية
- بانتظار المالك تشغيل المثبّت المحمي كمسؤول لتسجيل مهام Clean الثلاث.
## الخطوة التالية (أمر قابل للتنفيذ)
- بعد تشغيل المالك للمثبّت: تحقق أن /api/space يعيد elevated.*.ready=true للثلاثة، وجرّب معاينة ثم تنفيذ wintemp من الواجهة وتأكد من صف actions وملف ProgramData\MonitorWidget\out\clean_wintemp_result.json.
- ثم إصلاح الفشلات الثلاث السابقة في run_all (PR صغير)، ثم البند 4 من الخارطة: Pester لدوال widget.ps1 + Playwright للصفحات في CI مبني على tour.cjs.
## خارطة الطريق المتفق عليها (2026-10-27)
- المنتج أداة شخصية: لا تحزيم ولا توقيع ولا ويدجت .NET.
- 1) إغلاق المعلّق حيًّا وإصلاح ما ينكسر (أزرار المساحة، قائمة الويدجت وألوانه، فتح الغلاف من الويدجت، نقل إخفاء/تأجيل التوصيات) + إصلاح وصف delivery-optimization (القاعدة صحيحة).
- 2) تحذير ظاهر في صفحة الصحة عند سقوط WebView2 إلى Edge.
- 3) مكتمل في الكود 27/10: الخادم بصلاحية عادية دائمًا؛ الأفعال المرتفعة (purge، Windows.old، wintemp، swdist، DISM) مهام مجدولة ثابتة بقائمة بيضاء مع مفتاح تفعيل لكل فعل؛ يتبقى تسجيل مهام Clean بالمثبّت.
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
- لا تقتل عمليات PowerShell بالجملة (قد تقتل الويدجت). إعادة التشغيل: restart_agent.ps1 (المهمة المحدودة)؛ restart_admin.ps1 يُطلق خادمًا مرتفعًا ولا يُستعمل بعد فصل الصلاحيات.

## أوامر التحقق
- python tests/check_all.py
- gh pr view 14 --json mergeStateStatus,statusCheckRollup
- الاختبار الحي: python web/dev_proxy.py 47900 ثم shots/lay/tour.cjs (غير متتبَّع)
