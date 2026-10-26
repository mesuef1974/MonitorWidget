from pathlib import Path
import datetime, hashlib, json
from html import escape

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT/'docs'
runtime = json.loads((ROOT/'tests/ctrl-alerts-runtime-20261025.json').read_text(encoding='utf-8'))
sha = hashlib.sha256((ROOT/'index.html').read_bytes()).hexdigest()
assert runtime['status'] == 'pass' and runtime['sourceSha256'] == sha
checks = (ROOT/'tests/ctrl-alerts-checks-20261025.txt').read_text(encoding='utf-8')
assert 'المراحل الفاشلة: 0' in checks and 'الفحوص المتخطاة: 0' in checks
tracker_path = ROOT/'tests/no-scroll-tracker-20261025.json'
tracker = json.loads(tracker_path.read_text(encoding='utf-8'))
for page in tracker['application']['pages']:
    if page['id'] in ('ctrl','alerts'):
        page.update(status='implemented_with_runtime_fit; visual_acceptance_pending',sourceSha256=sha,
                    note='Isolated DOM checks passed. Actual browser no-scroll/geometry acceptance remains unverified.')
tracker['date'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
tracker_path.write_text(json.dumps(tracker,ensure_ascii=False,indent=2),encoding='utf-8')
record = {'date':tracker['date'],'sourceSha256':sha,'status':'implemented; visual acceptance pending',
          'scope':['ctrl','alerts'],'domCases':18,'runtimeIntegrity':runtime,'rendered':False,
          'limits':['Saved tool permission blocks live browser inspection.','Other application pages are not declared no-scroll compliant.']}
(DOC/'ctrl-alerts-review-20261025.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
heading = '\n'.join(checks.splitlines()[:3])
html = f'''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>التحكم والتنبيهات — متابعة منع التمرير</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f1f6f5;color:#173633;font:16px/1.9 "Segoe UI",Tahoma,sans-serif}}main{{max-width:1000px;margin:auto;padding:28px}}header,section{{background:#fff;border:1px solid #d5e1de;border-radius:14px;padding:22px;margin-bottom:18px}}h1{{font-size:28px}}h2{{font-size:21px}}a{{color:#006e64}}pre,code{{direction:ltr;unicode-bidi:isolate;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}.pending{{border-right:5px solid #a56a00}}@media(max-width:600px){{main{{padding:12px}}header,section{{padding:16px}}}}@media print{{body{{background:white}}main{{max-width:none;padding:0}}section{{break-inside:avoid}}}}</style><main>
<header><p>مرصد الجهاز · متابعة الإصدار2.0.0 · 25 أكتوبر2026</p><h1>تنظيم التحكم والتنبيهات دون إخفاء الحقول</h1><p>نُفذ تجميع وظيفي للصفحتين، مع قياس مساحة المحتوى قبل تثبيت غياب التمرير. اعتماد الرسم الفعلي ما زال معلقًا.</p></header>
<section><h2>ما تغير</h2><p>التحكم يجمع الذاكرة والويدجت والصيانة والتنظيف المجدول، مع اختيار طبقة التنظيف بدل تكرار ضوابط الطبقات جميعًا. التنبيهات تفصل الحدود عن السجل وتتيح اختيار فئة الحدود. تبقى الحقول الأصلية والحفظ والتأكيدات متاحة.</p><p>يظهر المحتوى الزائد بتنقل صريح بعد قياس الحدود. لا يُخفى التمرير قبل نجاح قياس الملاءمة؛ إذا تعذر استيعاب عنصر منفرد تبقى العناصر ظاهرة وتظهر رسالة توسيع النافذة. لا يُحسب هذا المسار نجاحًا لمعيار غياب التمرير.</p></section>
<section><h2>سلامة المدخلات والنصوص</h2><p>حُفظت المسودات عند تحديث البيانات والتحديث العام، ودُمج تجاهل برنامج من السجل دون استبدال التعديلات غير المحفوظة. تحرير قائمة التجاهل يحافظ على جميع الأسماء واللصق متعدد الأسطر. حفظ مرفوض أو تعديل أثناء الطلب لا يُعلَن محفوظًا.</p><p>قارئ النص الكامل يقيس المقاطع داخل المساحة الفعلية، ويحافظ على الموضع عند تغيير الحجم. يلتقط قياس مجموعات الحقول التركيز والتحديد قبل التجريب ثم يعيدهما.</p></section>
<section><h2>التحقق</h2><pre>{escape(heading)}</pre><p>18 حالة سلوك DOM معزول، إلى جانب فحوص المظهر والتباين والواجهات الخلفية والويدجت. هذه اختبارات منطق ومصدر وعناصر أصلية حيث ينطبق؛ ليست صورًا من متصفح التطبيق.</p><p>تحقق القراءة فقط: الملف المخدوم وبصمات CSP مطابقان، وLF محفوظ، والخادم والويدجت يعملان. تحتاج النافذة المفتوحة إلى تحديث لتحميل HTML الجديد؛ لم يُعد تشغيل الخادم.</p><p><a href="../tests/ctrl-alerts-checks-20261025.txt">الفحوص</a> · <a href="ctrl-alerts-review-20261025.json">سجل الأدلة</a></p></section>
<section class="pending"><h2>حدود القبول والعمل المتبقي</h2><p>أداة المتصفح محجوبة بإذن محفوظ. لم يُنفذ فحص بصري للصفحتين عند480×640 أو جميع درجات تكبير Windows؛ لذلك يبقى الاعتماد البصري غير مكتمل. سائر صفحات التطبيق لم تُعلن مستوفية لطلب منع التمرير، وتُعالج على دفعات.</p><p><a href="../tests/no-scroll-tracker-20261025.json">جرد الصفحات وحالتها</a> · <a href="معايير_تصميم_التطبيق_والويدجت.html">المعايير وقائمة التحقق</a> · <a href="ويدجت_واحد_مدمج_2026-10-25.html">دفعة الويدجت الواحد</a></p></section>
<section><h2>بصمة الإصدار المفحوص</h2><code>{sha}</code></section></main></html>'''
(DOC/'التحكم_والتنبيهات_دون_تمرير_2026-10-25.html').write_text(html,encoding='utf-8')
print('Created scoped Control/Alerts review; visual acceptance remains pending.')
