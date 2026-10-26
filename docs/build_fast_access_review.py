"""Build local review from the frozen source and completed test evidence."""
from pathlib import Path
import hashlib, html, json, re

root = Path(__file__).resolve().parent.parent
report = (root / 'tests/fast-access-checks-20261026.txt').read_text(encoding='utf-8')
assert 'المراحل الفاشلة: 0 من 38' in report
assert 'الفحوص المتخطاة: 0' in report
runtime = json.loads((root / 'tests/fast-access-runtime-20261026.json').read_text(encoding='utf-8'))
sha = hashlib.sha256((root / 'index.html').read_bytes()).hexdigest()
assert runtime['sourceSha256'].lower() == sha
assert runtime['status'] == 'pass' and runtime['servedSourceMatches'] and runtime['cspHashesMatch']
tracker_path = root / 'tests/fast-access-tracker-20261026.json'
tracker = json.loads(tracker_path.read_text(encoding='utf-8'))
tracker.update(status='implemented_automated_checks_passed_visual_pending', sourceSha=sha,
               testGroups=38, failed=0, skipped=0, browserRendered=False,
               evidence=['fast-access-checks-20261026.txt','fast-access-runtime-20261026.json'])
for page in tracker['pages']:
    page['status'] = 'source_and_boot_checked_visual_pending'
tracker_path.write_text(json.dumps(tracker, ensure_ascii=False, indent=2), encoding='utf-8')
cases = re.findall(r'RESULT (\d+) fast-access behavior cases', report)
count = cases[-1] if cases else '—'
document = '''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>تحسين الوصول — مرصد الجهاز</title>
<style>*{box-sizing:border-box}body{margin:0;background:#edf3f4;color:#173039;font:16px/1.9 "Segoe UI",Tahoma,sans-serif}main{max-width:1040px;margin:auto;padding:28px}header{background:#113b42;color:white;padding:28px;border-radius:16px}section{background:white;border:1px solid #cad9dc;border-radius:12px;padding:22px;margin:18px 0}h1{font-size:28px}h2{font-size:21px}table{border-collapse:collapse;width:100%}th,td{text-align:right;border:1px solid #cbdadd;padding:12px;vertical-align:top}th{background:#e9f3f2}a{color:#006e66}code{overflow-wrap:anywhere;direction:ltr;display:block;font-size:12px}.note{border-right:5px solid #b98222}@media(max-width:600px){main{padding:12px}header,section{padding:16px}table{font-size:14px}}@media print{body{background:white}main{max-width:none;padding:0}section{break-inside:avoid}header{color:#173039;background:white;border:1px solid}}</style>
<main><header><p>مرصد الجهاز · 26 أكتوبر 2026 · المواصفة 3.0.0</p><h1>وصول أسرع ومعلومات مترابطة</h1><p>أُلغي تقسيم المؤشرات والحقول والنصوص إلى صفحات صغيرة؛ التمرير الرأسي متاح في تفاصيل التطبيق، والويدجت واجهة واحدة.</p></header>
<section><h2>ما تغيّر</h2><table><tr><th>المجال</th><th>السلوك المعتمد</th></tr>
<tr><td>محتوى الصفحات</td><td>المؤشرات والرسوم والقوائم والإجراءات المرتبطة متاحة معًا، دون التنقل بين أجزاء صغيرة من الصفحة.</td></tr>
<tr><td>التنقل</td><td>وجهات التطبيق التسع عشرة متاحة مباشرة؛ في القائمة المدمجة: فتح القائمة ثم اختيار الوجهة.</td></tr>
<tr><td>السجلات</td><td>الترقيم للبيانات فقط، بسعة ثابتة عملية 10–12 سجلًا؛ التحديث وتغيير الحجم يحفظان موضع السجلات.</td></tr>
<tr><td>قراءة التفاصيل</td><td>النص الكامل في نافذة واحدة قابلة للتمرير ولوحة المفاتيح، مع بقاء أزرار التأكيد والإغلاق منفصلة.</td></tr>
<tr><td>الويدجت</td><td>لم يتغير في هذه الدفعة: مدمج، صفحة واحدة بلا تمرير، ويحافظ على الهوية المشتركة.</td></tr></table></section>
<section><h2>التحقق المنفذ</h2><p>نجحت 38 مجموعة فحص معزولة، دون فشل أو تخطٍّ. منها __CASES__ حالة سلوك للوصول السريع، وتحميل المسارات التسعة عشر باستخدام DOM وبيانات مصطنعة، وحماية المسودات والطلبات المتأخرة والمظهر المشترك والويدجت.</p><p>تحققت مطابقة الملف الذي يقدمه الخادم للمصدر، وبصمات JavaScript في سياسة CSP. هذه فحوص مصدر وسلوك معزول وتشغيل؛ ليست قياسًا بصريًا في المتصفح.</p><p><a href="../tests/fast-access-checks-20261026.txt">نتائج الفحوص</a> · <a href="../tests/fast-access-runtime-20261026.json">مطابقة الخادم</a> · <a href="../tests/fast-access-tracker-20261026.json">سجل المتابعة</a> · <a href="معايير_تصميم_التطبيق_والويدجت.html">وثيقة المعايير وقائمة التحقق</a></p></section>
<section class="note"><h2>ما بقي للتحقق البصري</h2><p>تعذّر فحص الرسم والتداخل والتباين الفعلي بمختلف أحجام النوافذ داخل المتصفح بسبب حظر الأداة المحفوظ. لذلك لم تُعتمد المعايير البصرية على أساس اختبارات DOM. يلزم تحديث نافذة التطبيق الأصلية بـ F5 لتحميل الواجهة الجديدة.</p></section>
<section><h2>هوية النسخة المفحوصة</h2><code>SHA-256: __SHA__</code><p>تقرير محلي؛ لم يُنشر على الإنترنت.</p></section></main></html>'''
(root / 'docs/تحسين_الوصول_2026-10-26.html').write_text(document.replace('__SHA__',html.escape(sha)).replace('__CASES__',count),encoding='utf-8')
print('PASS local fast-access report and tracker match frozen source')
