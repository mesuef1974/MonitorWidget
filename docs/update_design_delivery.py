"""Build the local Arabic design delivery report from saved verification evidence."""
from pathlib import Path
from html import escape
import datetime
import hashlib
import json
import re
import shutil

ROOT=Path(__file__).resolve().parents[1]
DOC=ROOT/'docs'
ART=Path(r'C:/Users/mesue/.codex/visualizations/2026/10/25/01a17266-b23c-7271-993a-11ccd8b89286')
stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).isoformat(timespec='seconds')
runtime_path=ROOT/'tests/design-runtime-check-20261025.json'
runtime=json.loads(runtime_path.read_text(encoding='utf-8')) if runtime_path.exists() else {'status':'pending'}
active=runtime.get('status')=='pass'
builder=DOC/'build_design_standard.py'
s=builder.read_text(encoding='utf-8')
s=s.replace("'version':'1.1.0'","'version':'1.1.1'").replace('الإصدار 1.1.0 ·','الإصدار 1.1.1 ·')
s=s.replace("'status':'مواصفة مقترحة للاعتماد؛ لم تُختبر على التصميم الجديد'","'status':'اتجاه التصميم معتمد؛ تنفيذ فعلي وتقييم قبول مستمر'")
s=s.replace('الحالة: مقترح للاعتماد والتطوير','الحالة: الاتجاه معتمد؛ التقييم مستمر')
current=('تم تفعيل الخادم والويدجت والمظهر المحفوظ على الجهاز.' if active else 'التنفيذ المصدري جاهز؛ التفعيل الحي لم يُؤكد بعد.')
panel=f'<section class="panel"><h2>حالة الدفعة الحالية · التنفيذ الفعلي</h2><p>اعتمد المستخدم اتجاه مساحة عمل هادئة. نُفذت الهوية المشتركة والتخطيط العام والرسوم المدمجة وصفحة المظهر الموحد وWPF، مع حفظ ذري ومنع تعارض المراجعات. {current} فحوص المصدر والمنطق والصور المحلية لا تعادل اكتمال فحص المتصفح وDPI وقارئ الشاشة.</p><p><a href="تقرير_تنفيذ_التصميم_2026-10-25.html">تقرير التنفيذ والأدلة والفجوات الحالية</a></p></section>'
s=re.sub(r'<section class="panel"><h2>حالة الدفعة الحالية.*?</section>',lambda m:panel,s,count=1,flags=re.S)
s=s.replace('المعاينة الحالية تطبق الاختيار فورًا على واجهتي العرض باستخدام حالة مشتركة، وتحفظه ضمن حالة المحادثة عند دعم المضيف. الحفظ الدائم ومزامنة عمليات التطبيق والويدجت الفعلية ما زالا ضمن مرحلة التنفيذ والتحقق.','التنفيذ الفعلي يستخدم appearance.json عبر واجهة محلية مصادق عليها، برقم مراجعة وكتابة ذرية. يقرأ التطبيق والويدجت الإعداد المشترك دوريًا؛ زمن الانعكاس المرئي الفعلي ما زال يحتاج قياسًا. المعاينة السابقة منفصلة وتحتفظ بحالتها داخل المحادثة عند دعم المضيف.')
if '<td>1.1.1</td>' not in s:
 s=s.replace('<tr><td>1.1.0</td>','<tr><td>1.1.1</td><td>2026-10-25</td><td>تسجيل اعتماد الاتجاه وتنفيذ الهوية المشتركة والحفظ الفعلي وأدلة الويدجت وحدود التحقق.</td></tr><tr><td>1.1.0</td>')
builder.write_text(s,encoding='utf-8')
exec(compile(s,str(builder),'exec'),{'__file__':str(builder),'__name__':'__main__'})
spec=json.loads((DOC/'design-standards-v1.json').read_text(encoding='utf-8'))
files=sorted(set([ROOT/'index.html',ROOT/'appearance.py',ROOT/'server.py',ROOT/'widget.ps1',ROOT/'atomic_json.py',ROOT/'install_protected_app_20261025.ps1',ROOT/'tests/run_all.py',ROOT/'tests/appearance_regression.py',ROOT/'tests/frontend_appearance_check.js',ROOT/'tests/frontend_contract_check.js',ROOT/'tests/widget_appearance_check.ps1',ROOT/'tests/widget_visual_fixture.ps1',ROOT/'tests/window_security_check.py']))
manifest={str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
revision=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
(DOC/'design-source-manifest-20261025.json').write_text(json.dumps({'timestamp':stamp,'revision':revision,'files':manifest},indent=2),encoding='utf-8')
evidence={
'ID-01':'اللوحات الأربعة مشتركة في المصدر؛ صور WPF لثماني حالات. تطابق كل المكونات في المتصفح يحتاج مراجعة.',
'ID-04':'مراجعة مستقلة لـ72 زوجًا لونيًا من المصدر؛ الحد الأدنى 4.76:1. لا يشمل الفحص الألوان المحسوبة أو الشفافية أو حالات التركيز.',
'ID-06':'حفظ ذري واختبار تعارض ونتائج قديمة ناجح. '+('تطابق مراجعة mini وواجهة appearance حيًا؛ ' if active else '')+'قياس وصول التغيير بصريًا في نافذتين وزمنه وإعادة تشغيل كليهما يحتاج فحصًا.',
'ID-08':'اختبارات القيم غير الصالحة والتوافق ومنع الكتابة فوق ملف تالف أو مخطط أحدث ناجحة؛ اختبار تبديل مظهر Windows الفعلي لم ينفذ.',
'RS-04':'الويدجت يستخدم مؤشرات تفاصيل فعلية 64 DIP ونص وحدة 11.5 دون تصغير بتحويل. يلزم فحص جميع مؤشرات التطبيق فعليًا.',
'WG-01':'الرسم الأصلي يعرض بطاقة 312×238 DIP؛ النافذة مع هامش الظل 340×266. يلزم حسم احتساب هامش الظل بالنسبة لميزانية الارتفاع 260 قبل اعتماد البند.',
'WG-02':'رسم WPF من المصدر: 340×476 ضمن مساحة 600، و340×618 ضمن مساحة 768؛ التذييل ظاهر. فحص 1080 والتشغيل الفعلي المتعدد لم يكتمل.',
'QA-06':'اختبارات وحفظ بصمات وتقارير مستقلة وصور WPF موثقة؛ ما زالت أدلة المتصفح وDPI والوصول الفعلي غير مكتملة.'}
review={'date':stamp,'specVersion':spec['version'],'approvedDirection':'مساحة عمل هادئة','sourceRevision':revision,'runtime':runtime,'results':{c['id']:{'result':'pending','evidence':evidence.get(c['id'],'منفذ أو متأثر بالتخطيط المشترك؛ يلزم استكمال طريقة الفحص المحددة قبل اعتماد المعيار كاملًا.')} for c in spec['criteria']},'openIssues':['فحص المتصفح الفعلي غير متاح بسبب رفض صلاحية الوصول سابقًا؛ أداة سطح المكتب لم تعرض نافذة الويدجت المستهدفة.','قياس زمن المزامنة المرئية الفعلية وDPI ولوحة المفاتيح وقارئ الشاشة.','ارتفاع النافذة المدمجة 266 مع الظل مقابل هدف 260؛ البطاقة نفسها 238.','عند تلف إعداد المظهر، التطبيق يعطل التعديل؛ الويدجت يعرض فشل الحفظ دون تعطيل القائمة صراحةً.']}
(DOC/'design-review-implementation-1.json').write_text(json.dumps(review,ensure_ascii=False,indent=2),encoding='utf-8')
assets=DOC/'design-assets-20261025';assets.mkdir(exist_ok=True)
for name in ['widget-redesign-light-graphite-compact.png','widget-redesign-dark-teal-expanded-600.png']:
 shutil.copy2(ART/'qa-shots'/name,assets/name)
test_text=(ROOT/'tests/design-isolated-20261025.txt').read_text(encoding='utf-8-sig')
test_header='\n'.join(test_text.splitlines()[:3])
issue_rows=''.join('<li>'+escape(x)+'</li>' for x in review['openIssues'])
evidence_rows=''.join(f'<tr><td><bdi>{k}</bdi></td><td>{escape(v)}</td><td>تحقق جزئي؛ لم يعتمد كاملًا</td></tr>' for k,v in evidence.items())
runtime_rows=''.join(f'<tr><td><bdi>{escape(x["name"])}</bdi></td><td>{escape(json.dumps(x["value"],ensure_ascii=False))}</td></tr>' for x in runtime.get('steps',[]))
html=f'''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>تنفيذ التصميم — مرصد الجهاز</title><style>body{{font:15px/1.9 "Segoe UI",Tahoma,sans-serif;background:#eef3f1;color:#20383f;margin:0}}main{{max-width:1080px;margin:auto;padding:28px}}header,section{{padding:22px;background:#fff;border:1px solid #cedbd5;border-radius:12px;margin-bottom:18px}}header{{border-top:5px solid #187660}}h1{{font-size:27px}}h2{{font-size:21px}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:right;padding:9px;border-bottom:1px solid #d8e2dd;vertical-align:top}}th{{background:#f1f6f4}}.note{{padding:12px;background:#fff0d8;border-right:4px solid #9b6a18}}.shots{{display:flex;gap:18px;align-items:start;flex-wrap:wrap}}figure{{margin:0;max-width:100%}}img{{max-width:100%;height:auto;width:340px}}figcaption{{max-width:340px;font-size:13px}}pre{{white-space:pre-wrap}}code{{overflow-wrap:anywhere}}a{{color:#086f64}}@media(max-width:600px){{main{{padding:12px}}section,header{{padding:15px}}}}@media print{{body{{background:#fff}}main{{padding:0}}img{{max-height:160mm;width:auto}}@page{{size:A4;margin:13mm}}}}</style><main><header><p>مرصد الجهاز · {stamp} · المواصفة {spec['version']}</p><h1>تنفيذ الهوية المشتركة والمظهر الموحد</h1><p>{current} الاعتماد البصري النهائي مشروط باستكمال الفحوص المبينة أدناه.</p></header><section><h2>ما تغير</h2><ul><li>نظام ألوان وأسطح وخطوط متناسق، وتخطيط هادئ وتنقل جانبي وشريط أدوات مختصر، مع إبقاء الوظائف متاحة.</li><li>بطاقات ورسوم مدمجة قابلة لإعادة التدفق، وصفحة المظهر الموحد بأربع لوحات وفاتح وداكن وحسب النظام.</li><li>ويدجت WPF بثلاثة مؤشرات وأشرطة صغيرة، مع تفاصيل قابلة للتوسيع والتمرير.</li><li>حفظ مركزي في appearance.json، ومصادقة وكتابة ذرية ومراجعة تمنع الطلب القديم؛ توافق مع إعداد المظهر السابق.</li><li>فصل ألوان الحالات عن لون الهوية، ومعالجة فشل الحفظ والإعداد التالف.</li></ul><p>صححت المراجعة المستقلة ثلاثة عيوب قبل التفعيل: نص وحدات المؤشرات بعد التصغير، والتحقق من مراجعة إعداد الويدجت، ومعالجة ملف المظهر التالف في التطبيق.</p></section><section><h2>الفحوص والأدلة</h2><pre>{escape(test_header)}</pre><p>تضم المجموعة اختبارات الخلفية والحفظ والتعارض والواجهة والويدجت وCSP والتثبيت المعزول. التخطيط بالمتصفح لم يُفحص؛ النجاح هنا لا يعني اعتماد كل معايير التصميم.</p><p>مراجعة مستقلة للألوان: 72 زوجًا من قيم المصدر فوق الهدف الداخلي 4.5:1؛ الأدنى 4.76:1. صور WPF من المصدر، ببيانات اختبار قديمة وموسومة، وليست تصويرًا للقياسات الحالية على الجهاز.</p><div class="shots"><figure><img src="design-assets-20261025/widget-redesign-light-graphite-compact.png" alt="ويدجت مدمج فاتح رمادي من المصدر"><figcaption>المظهر الفاتح الرمادي · البطاقة312×238 والنافذة340×266 DIP بما فيها الظل.</figcaption></figure><figure><img src="design-assets-20261025/widget-redesign-dark-teal-expanded-600.png" alt="ويدجت موسع داكن داخل مساحة600 DIP"><figcaption>موسع داكن · ارتفاع476 ضمن مساحة600 DIP؛ التذييل ظاهر.</figcaption></figure></div></section><section><h2>التحقق التشغيلي</h2><p>الحالة: <bdi>{escape(runtime['status'])}</bdi>. إعادة تشغيل المكونات عبر واجهات التطبيق الحالية، دون تغيير تعريفات مهام Windows أو تفعيل التثبيت المحمي.</p><table><tr><th>الخطوة</th><th>الدليل المسجل</th></tr>{runtime_rows}</table><p>أظهر التفعيل الأول سباقًا بين وجود mutex واكتمال ظهور الويدجت؛ لم يكن تعطلًا للنافذة، وصُحح انتظار الجاهزية وأضيف اختبار له. سجل المحاولة السابقة محفوظ داخل سجل التحقق. نجح التحقق الأخير بعد إعادة تشغيل الخادم مع بقاء المظهر المحفوظ. وجود الويدجت حيًا مؤكد من mutex وعلامة الظهور المحفوظة؛ لم يتم فحص نافذته مباشرةً بأداة سطح المكتب. أُعيد فتح التطبيق بتذكرة جديدة لأن إعادة تشغيل الخادم تنهي جلسات المتصفح السابقة.</p></section><section><h2>حالة معايير القبول</h2><p>عدد المعايير37. الأدلة أدناه جزئية؛ لا تُحسب المعايير المركبة مكتملة قبل تنفيذ جميع فحوصها.</p><table><tr><th>المعيار</th><th>التقدم المثبت</th><th>النتيجة</th></tr>{evidence_rows}</table></section><section><h2>الفجوات والخطوة التالية</h2><ul>{issue_rows}</ul><p>المراجعة التالية تستكمل مصفوفة النوافذ والتباين الفعلي والوصول والأداء. تعارض وصف سياسة التنظيف الخارجية مع enabled بقي ملاحظة تاريخية منفصلة؛ لم تُعدّل السياسة ضمن تصميم الواجهة.</p><p><a href="معايير_تصميم_التطبيق_والويدجت.html">المواصفة وقائمة التحقق</a> · <a href="design-review-implementation-1.json">سجل التقييم</a> · <a href="design-source-manifest-20261025.json">بصمات المصدر</a></p><p>بصمة حزمة المصدر: <code>{revision}</code>. النسخ السابقة تحت backups/design_20261025.</p></section></main></html>'''
(DOC/'تقرير_تنفيذ_التصميم_2026-10-25.html').write_text(html,encoding='utf-8')
contract=json.loads((DOC/'design-appearance-contract.json').read_text(encoding='utf-8'))
contract['status']='implemented-runtime' if active else 'implemented-source; runtime activation pending'
(DOC/'design-appearance-contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
print('Updated design standards 1.1.1 and delivery report; runtime='+runtime['status'])
