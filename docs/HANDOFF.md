---
updated: 2026-10-27T10:22:20+03:00
head: 0ae3f25
branch: feat/handoff-automation
dirty: 0
dirty_files: 
---
# مرصد الجهاز — بطاقة التسليم

## الحالة
- الفرع الحالي feat/handoff-automation · PR 15 مفتوح (ملفات آلية التسليم). PR 14 (fix/equal-rows) مفتوح، CI ناجح، MERGEABLE. لا يُدمج أيّ منهما دون طلب صريح من المالك.
- main عند PR 13. PR 5–13 مدموجة، PR 4 مغلق بلا دمج.
- الفحص tests/check_all.py يمرّ كله عدا delivery-optimization (وصف القاعدة يقول DISABLED وهي مفعّلة؛ متروك عمدًا حتى يقرر المالك).

## الخطوة الجارية
- تقرير SWOT docs/تقرير_SWOT_2026-10-27.html ملتزم في PR 15 (0ae3f25) ومنشور كـ Artifact.
- آلية التسليم الآلي بُنيت ورُبطت: docs/HANDOFF.md + ~/.claude/hooks/handoff_guard.py (Stop/SessionStart) + handoff_card.py (المرآة) + .claude/handoff.json (العتبة 190k). الملفات الجديدة غير ملتزمة بعد (تخص PR مستقل لا PR 14).

## الخطوة التالية (أمر قابل للتنفيذ)
- انتظار قرار المالك في PR 15 وPR 14؛ وإن طُلب الدمج: gh pr update-branch ثم squash بعد CI.

## ما بُني (مختصر)
- الغلاف app_shell.ps1 (WPF بلا إطار، WebView2 من مكتبات Teams، يرجع لـ Edge).
- الهوية المشتركة identity.ps1 تقرأ web/css/tokens.css للويدجت والغلاف.
- الواجهة web/index.html + web/css (tokens/base/layout/components/utilities) يقدّمها server.py بـ ETag.

## معلّق / غير مُتحقق
- قياسات كرت الشاشة الحية تحتاج الخادم بصلاحية مسؤول.
- أزرار معاينة/تنظيف المساحة بعد التقسيم لم تُجرَّب فعليًا.
- القائمة الجديدة للويدجت وألوانه من tokens لم تُجرَّب على الويدجت الحي؛ ولم يُفتح الغلاف من زر الويدجت.
- إخفاء/تأجيل التوصيات: لم يُنقل من الصفحة القديمة إلى web/index.html.

## قواعد ثابتة
- العربية الفصحى والإيجاز؛ لا رسائل إلى جلسة «خفيفة 0101»؛ لا دمج PR دون طلب.
- فرع من main ← PR ← squash بعد CI؛ تحديث الفرع المحمي من main قبل الدمج.
- الأنماط في web/css بمتغيرات tokens.css فقط؛ المضمّن للقيم القادمة من البيانات.
- لا تقتل عمليات PowerShell بالجملة (قد تقتل الويدجت). إعادة التشغيل: restart_admin.ps1 بصلاحية مسؤول.

## أوامر التحقق
- python tests/check_all.py
- gh pr view 14 --json mergeStateStatus,statusCheckRollup
- الاختبار الحي: python web/dev_proxy.py 47900 ثم shots/lay/tour.cjs (غير متتبَّع)
