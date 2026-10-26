"""Local Arabic coverage report; never executes application actions."""
from pathlib import Path
import json, html, hashlib

root = Path(__file__).resolve().parent
folder = root / 'design-skills-preview-20261026'
manifest = folder / 'coverage.json'
if not manifest.exists():
    raise SystemExit('Preview coverage.json is required')
data = json.loads(manifest.read_text(encoding='utf-8'))
esc = lambda x: html.escape(str(x))
def render(value):
    if isinstance(value, dict):
        return '<dl>' + ''.join('<dt>'+esc(k)+'</dt><dd>'+render(v)+'</dd>' for k,v in value.items()) + '</dl>'
    if isinstance(value, list):
        return '<ul>' + ''.join('<li>'+render(v)+'</li>' for v in value) + '</ul>'
    return esc(value)
body = render(data)
page = '''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>تغطية معاينة صفحات مرصد الجهاز</title><style>
body{font:16px/1.8 'Segoe UI',Tahoma,sans-serif;background:#eef2f4;color:#172d38;margin:0}
main{max-width:1050px;margin:auto;padding:32px}article{background:white;border:1px solid #cbd5da;border-radius:12px;padding:24px;margin:18px 0}
h1{font-size:28px}h2{font-size:21px}a{color:#00665b}dd{margin:0 16px 12px;overflow-wrap:anywhere}dt{font-weight:600}li{margin-block:8px}ul{padding-inline-start:22px}
@media print{body{background:white}main{padding:0}article{break-inside:avoid}}
</style><main><h1>معاينة محتويات التطبيق والويدجت</h1><p>26 أكتوبر 2026 — معاينة محلية ببيانات عينة</p>
<article><h2>المراجعة</h2><p><a href="design-skills-preview-20261026/index.html">فتح المعاينة الكاملة</a></p>
<p>استخدم التنقل الجانبي والأقسام الفرعية، ثم عجلة الماوس أو السابق والتالي أسفل المحتوى. يمكن تغيير المظهر واللون المشتركين للتطبيق والويدجت.</p></article>
<article><h2>نطاق التحقق</h2><p>تُختبر تغطية المسارات والوصول إلى المحتوى وتفاعلات المعاينة محليًا. لم يُنفّذ فحص بصري داخل متصفح؛ سياسة الأداة حجبت فتح الملف المحلي. لذلك لا تُعد نتائج الاختبارات إثباتًا لسلامة القص أو الملاءمة البصرية عند كل حجم نافذة.</p>
<p>المعلومات عينة توضيحية، والإجراءات داخل المعاينة لا تنفّذ أوامر على الجهاز. اعتماد نقل التصميم إلى التطبيق يبقى بعد مراجعة المعاينة.</p></article>
<article><h2>خريطة المحتوى والتغطية</h2>MANIFEST</article></main></html>'''.replace('MANIFEST', body)
(root / 'تغطية_معاينة_الصفحات_2026-10-26.html').write_text(page, encoding='utf-8')
print('Coverage report generated')
