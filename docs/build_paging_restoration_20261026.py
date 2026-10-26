"""Record the user's explicit paging/full-widget restoration without rewriting history."""
from pathlib import Path
import hashlib,json,re
root=Path(__file__).resolve().parent.parent
checks=(root/'tests/paging-restoration-checks-20261026.txt').read_text(encoding='utf-8')
m=re.search(r'المراحل الفاشلة: (\d+) من (\d+)',checks)
assert m and m[1]=='0' and 'الفحوص المتخطاة: 0' in checks
runtime=json.loads((root/'tests/paging-restoration-runtime-20261026.json').read_text(encoding='utf-8'))
widget=json.loads((root/'tests/paging-restoration-widget-20261026.json').read_text(encoding='utf-8'))
sha=hashlib.sha256((root/'index.html').read_bytes()).hexdigest()
wsha=hashlib.sha256((root/'widget.ps1').read_bytes()).hexdigest()
assert runtime['status']=='pass' and runtime['sourceSha256']==sha
assert widget['status']=='pass' and widget['widgetSha256']==wsha
record=dict(date='2026-10-26',specVersion='3.2.0',groups=int(m[2]),failed=0,skipped=0,
 sourceSha256=sha,widgetSha256=wsha,browserRendered=False,
 policy='Restore paging without main-content scrolling and the complete original widget; supersedes compact/fast-access direction.',
 status='implemented_automated_checks_passed_visual_pending')
(root/'tests/paging-restoration-tracker-20261026.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
page='''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>استعادة التقليب والويدجت الكامل</title>
<style>*{box-sizing:border-box}body{margin:0;font:16px/1.9 "Segoe UI",Tahoma,sans-serif;background:#edf3f4;color:#173039}main{max-width:980px;margin:auto;padding:26px}header{background:#123b43;color:white;border-radius:16px;padding:26px}section{background:white;border:1px solid #cad9dc;border-radius:12px;padding:22px;margin:18px 0}h1{font-size:27px}h2{font-size:21px}a{color:#006e66}code{direction:ltr;display:block;overflow-wrap:anywhere;font-size:12px}@media(max-width:600px){main{padding:12px}section,header{padding:16px}}@media print{body{background:white}main{padding:0}header{background:white;color:#173039}section{break-inside:avoid}}</style><main>
<header><p>مرصد الجهاز · 26 أكتوبر2026 · المواصفة3.2.0</p><h1>استعادة تقليب الصفحات والويدجت الكامل</h1><p>تنفيذ الطلب الأخير: العودة إلى التقليب بدل التمرير، وإلغاء اختصار الويدجت. يحل هذا القرار محل اتجاه3.0/3.1؛ تبقى تقاريرهما سجلًا تاريخيًا.</p></header>
<section><h2>التطبيق</h2><p>أُعيدت نسخة التقليب المحفوظة مع عداد الصفحات السفلي وعجلة الماوس في الاتجاهين، مع الحفاظ على إصلاحات ملكية القوائم والطلبات المتأخرة وتحديث صفوف المساحة وتغيير حجم النافذة. لم تُستعد أخطاء الإغلاق أو تمدد الويدجت داخل لوحة التحكم.</p></section>
<section><h2>الويدجت</h2><p>استُعيد المحتوى الكامل: المؤشرات والدوائر والرسم وتوزيع الالتزام وأعداد العمليات، ومفاتيح الصيانة الأربعة وأزرار تشغيلها، ونتيجة تنظيف الذاكرة وعدّاده وزر لوحة التحكم. أُبقي المظهر المشترك والتباين والشفافية والإخفاء الآمن وفتح نافذة لوحة التحكم المحددة.</p></section>
<section><h2>التحقق والتفعيل</h2><p>نجحت __COUNT__ مجموعة فحص معزولة دون فشل أو تخطٍّ. طابق الخادم مصدرHTML وبصماتCSP، وفُعّلت نسخة الويدجت الجديدة وحدها مع حفظ المظهر ودون إعادة تشغيل الخادم. اختبارات المؤشرات والمفاتيح لا تشغّل تنظيفًا أو مهام Windows فعلية.</p><p><a href="../tests/paging-restoration-checks-20261026.txt">الفحوص</a> · <a href="../tests/paging-restoration-runtime-20261026.json">مطابقة الخادم</a> · <a href="../tests/paging-restoration-widget-20261026.json">تفعيل الويدجت</a> · <a href="../tests/paging-restoration-tracker-20261026.json">بصمات النسخة</a> · <a href="معايير_تصميم_التطبيق_والويدجت.html">المعايير المحدثة</a></p></section>
<section><h2>حدود التحقق</h2><p>بقي فحص التطبيق بالمتصفح محجوبًا بإذن الأداة المحفوظ. فحوص المصدر وDOM وWPF المعزولة لا تثبت جودة الرسم في كل نافذة حية؛ لا يُدّعى قبول بصري شامل. لتحميل صفحات التطبيق المستعادة اضغطF5 في النافذة الأصلية.</p></section>
<section><h2>المصدر المفحوص</h2><code>HTML: __SHA__</code><code>Widget: __WSHA__</code><p>تقرير محلي، لم يُنشر على الإنترنت.</p></section></main></html>'''
(root/'docs/استعادة_التقليب_والويدجت_2026-10-26.html').write_text(page.replace('__COUNT__',m[2]).replace('__SHA__',sha).replace('__WSHA__',wsha),encoding='utf-8')
print('PASS restoration report and tracker match checked/activated source')
