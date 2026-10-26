"""Local Arabic delivery record; requires evidence from the final compact source."""
from pathlib import Path
import datetime
import hashlib
import json
import shutil
from html import escape

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / 'docs'
runtime = json.loads((ROOT/'tests/compact-runtime-20261025.json').read_text(encoding='utf-8'))
assert runtime['status'] == 'pass'
checks = (ROOT/'tests/compact-checks-20261025.txt').read_text(encoding='utf-8')
assert 'المراحل الفاشلة: 0' in checks
assert 'الفحوص المتخطاة: 0' in checks
source = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('index.html','widget.ps1')}
native_dir = Path(r'C:/Users/mesue/.codex/visualizations/2026/10/25/01a17266-b23c-7271-993a-11ccd8b89286/qa-compact-independent')
native = json.loads((native_dir/'measurements.json').read_text(encoding='utf-8-sig'))
assert native['sourceSha256'] == source['widget.ps1']
assets = DOC/'compact-assets-20261025'
assets.mkdir(exist_ok=True)
for name in ('review.json','measurements.json','stale.png','missing.png','warning.png'):
    shutil.copy2(native_dir/name,assets/name)
record = {
    'date': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'specVersion': '2.0.0',
    'source': source,
    'runtime': runtime,
    'native': native,
    'scope': 'Single compact widget; no detailed pages or scrolling in widget.',
    'applicationNoScroll': 'incomplete; index.html kept unchanged to preserve functionality',
    'testSuiteChange': {
        'previousGroups': 26,
        'currentGroups': 20,
        'reason': 'Seven detailed-widget checks replaced by one compact-widget check; removed chart/cleanup controls are historical, not skipped coverage of existing controls.',
        'retained': ['widget_close_check.ps1','widget_menu_contrast_check.ps1'],
        'replacement': 'widget_compact_check.ps1',
    },
    'limits': ['Live browser inspection is blocked by saved tool permission.',
               'Native isolated fixtures are not live desktop screenshots.',
               'All-page application no-scroll redesign is not delivered in this phase.'],
}
(DOC/'compact-review-20261025.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
heading = '\n'.join(checks.splitlines()[:3])
html = f'''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ويدجت واحد مدمج — مرصد الجهاز</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#f2f6f6;color:#173633;font:16px/1.9 "Segoe UI",Tahoma,sans-serif}}main{{max-width:1000px;margin:auto;padding:28px}}header,section{{background:white;border:1px solid #d2dfdd;border-radius:14px;padding:22px;margin-bottom:18px}}h1{{font-size:28px}}h2{{font-size:21px}}a{{color:#006f66}}pre,code{{direction:ltr;unicode-bidi:isolate;overflow-wrap:anywhere;white-space:pre-wrap;font-size:13px}}.note{{border-right:5px solid #a56a00}}@media(max-width:600px){{main{{padding:12px}}header,section{{padding:16px}}}}@media print{{body{{background:white}}main{{max-width:none;padding:0}}section{{break-inside:avoid}}}}</style>
<main><header><p>مرصد الجهاز · دفعة تصحيح الويدجت · 25 أكتوبر 2026</p><h1>واجهة واحدة مدمجة، بلا صفحات تفاصيل</h1><p>أُلغي اتجاه الويدجت متعدد الصفحات بعد ملاحظتك. وظيفته الملخص السريع؛ التفاصيل والرسوم والجدولة والصيانة داخل التطبيق.</p></header>
<section><h2>التغيير</h2><p>حالة الاتصال وحداثة القياس، المعالج والذاكرة والمساحة، أهم تنبيه، وزر واضح لفتح التطبيق. لا توسعة ولا أزرار السابق والتالي ولا منطقة تمرير داخل الويدجت. تبقى خيارات المظهر والسلوك في قوائم قصيرة مجمعة.</p><p>الهوية المشتركة والشفافية ووضوح النصوص وحفظ الموضع وسلوك الإخفاء محفوظة ضمن الفحوص المسجلة.</p></section>
<section><h2>دليل من المصدر الفعلي — رسم معزول</h2><p>إطار الويدجت في الحالات الثلاث المقاسة340×248 DIP، وثمانية عناصر داخل حدودها، وثلاثة أزرار فقط. الاختبار عند96 DPI وبيانات مصطنعة؛ ليس لقطة من نافذة المستخدم أو دليلًا على جميع درجات تكبير Windows.</p><figure><img src="compact-assets-20261025/warning.png" width="340" height="248" style="max-width:100%;height:auto" alt="الويدجت الواحد المدمج في اختبار تنبيه المساحة والذاكرة"><figcaption>تنبيه واضح دون توسعة أو صفحات. <a href="compact-assets-20261025/review.json">حكم المراجعة المستقلة</a>.</figcaption></figure></section>
<section><h2>التحقق والتفعيل</h2><pre>{escape(heading)}</pre><p>استُبدلت سبع مجموعات مرتبطة ببنية الويدجت التفصيلية المحذوفة باختبار جامع للويدجت المدمج، مع إبقاء فحص أمان النوافذ والقوائم الأصلية. اختبار شارت الويدجت القديم تاريخي لأن الشارت لم يعد داخل الويدجت؛ فحوص رسوم التطبيق باقية.</p><p>أُعيد تشغيل الويدجت بعد التحقق؛ حُفظت إعدادات المظهر والعتامة. سجل التفعيل: <a href="../tests/compact-runtime-20261025.json">الأدلة التشغيلية</a>. سجل الفحوص: <a href="../tests/compact-checks-20261025.txt">النتائج</a>.</p></section>
<section class="note"><h2>ما لم يكتمل</h2><p>منع التمرير داخل جميع صفحات التطبيق لم يكتمل في هذه الدفعة. بقي مصدر التطبيق على نسخة التباين السابقة، ولم يُدمج حل عام يغيّر بنيته أو يحجب البيانات. يحتاج ذلك معالجة داخل كل صفحة مع الحفاظ على الحقول والتركيز والرسوم.</p><p>فحص المتصفح الفعلي محجوب بإذن الأداة المحفوظ؛ لا تعني فحوص المصدر والعناصر الأصلية المعزولة اعتمادًا بصريًا لكل صفحات التطبيق أو لقطة من سطح المكتب الحي.</p></section>
<section><h2>المعايير والتتبع</h2><p><a href="معايير_تصميم_التطبيق_والويدجت.html">المعايير وقائمة التحقق — الإصدار2.0.0</a> · <a href="compact-review-20261025.json">سجل هذه الدفعة</a></p><p>قائمة التحقق لا تبدأ بحالات نجاح تلقائية. نتائج الإصلاحات السابقة تبقى سجلات تاريخية بإصداراتها.</p><p>بصمة الويدجت:<br><code>{source['widget.ps1']}</code></p><p>بصمة التطبيق:<br><code>{source['index.html']}</code></p></section></main></html>'''
(DOC/'ويدجت_واحد_مدمج_2026-10-25.html').write_text(html,encoding='utf-8')
print('Created local compact-widget review; application no-scroll remains explicitly incomplete.')
