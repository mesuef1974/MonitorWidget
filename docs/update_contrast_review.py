from pathlib import Path
import datetime
import hashlib
import json
import re
import shutil
from html import escape

ROOT=Path(__file__).resolve().parents[1]
DOC=ROOT/'docs'
EVIDENCE=Path(r'C:/Users/mesue/.codex/visualizations/2026/10/25/01a17266-b23c-7271-993a-11ccd8b89286/qa-menu')
web=json.loads((ROOT/'tests/control-contrast-evidence-20261025.json').read_text(encoding='utf-8'))
menu=json.loads((EVIDENCE/'widget-menu-contrast.json').read_text(encoding='utf-8-sig'))
buttons=json.loads((EVIDENCE/'widget-button-contrast.json').read_text(encoding='utf-8-sig'))
native=json.loads((EVIDENCE/'widget-menu-evidence-manifest.json').read_text(encoding='utf-8-sig'))
runtime=json.loads((ROOT/'tests/contrast-runtime-20261025.json').read_text(encoding='utf-8'))
front_hash=hashlib.sha256((ROOT/'index.html').read_bytes()).hexdigest()
widget_hash=hashlib.sha256((ROOT/'widget.ps1').read_bytes()).hexdigest()
assert web['sourceSha256']==front_hash
assert menu['sourceSha256']==buttons['sourceSha256']==native['sourceSha256']==widget_hash
assert runtime['status']=='pass'
assets=DOC/'contrast-assets-20261025';assets.mkdir(exist_ok=True)
for a in native['artifacts']:
    p=Path(a['path'])
    expected=a.get('sha256') or a.get('Sha256')
    if expected:assert hashlib.sha256(p.read_bytes()).hexdigest().lower()==expected.lower(),p.name
    shutil.copy2(p,assets/p.name)
stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).isoformat(timespec='seconds')
builder=DOC/'build_design_standard.py'
s=builder.read_text(encoding='utf-8').replace("'version':'1.2.0'","'version':'1.2.1'").replace('الإصدار 1.2.0 ·','الإصدار 1.2.1 ·')
panel='<section class="panel"><h2>حالة الدفعة الحالية · تباين المفاتيح والقوائم الفرعية</h2><p>صُححت حالات النص الأبيض على خلفية بيضاء، ووراثة ألوان القوائم الفرعية، ونص زر التحذير، وتلاشي العناصر المعطلة. التقييم الحالي يقيس ألوان العناصر الناتجة عن حالات التنسيق والشفافية؛ لا يكتفي بألوان الهوية الأساسية.</p><p><a href="تقرير_تباين_الأزرار_والقوائم_2026-10-25.html">نتائج التباين والأدلة وحدود التحقق</a></p></section>'
s=re.sub(r'<section class="panel"><h2>حالة الدفعة الحالية.*?</section>',lambda _:panel,s,count=1,flags=re.S)
if '<td>1.2.1</td>' not in s:s=s.replace('<tr><td>1.2.0</td>','<tr><td>1.2.1</td><td>2026-10-25</td><td>تصحيح تباين حالات عناصر التحكم والقوائم الفرعية، وتوسيع طريقة تحقق ID-04 إلى الألوان الفعلية بعد أولوية التنسيق والوراثة والشفافية.</td></tr><tr><td>1.2.0</td>')
builder.write_text(s,encoding='utf-8')
exec(compile(s,str(builder),'exec'),{'__file__':str(builder),'__name__':'__main__'})
record={'date':stamp,'specVersion':'1.2.1','source':{'index.html':front_hash,'widget.ps1':widget_hash},'web':web,'nativeMenus':menu,'nativeButtons':buttons,'nativeManifest':native,'runtime':runtime,'limits':['فحص CSS معزول، وليس قياس طلاء المتصفح الفعلي.','عناصر WPF أصلية مملوكة للاختبار، وقوائمها لم تُفتح على سطح المكتب ولم تُنفّذ أوامرها.','قائمة شريط النظام تستخدم ألوان Windows النظامية المقترنة؛ ليست لوحات مخصصة جرى اختبارها عبر الألوان الأربعة.','ليس هذا تدقيق وصول شاملًا أو ضمان تباين فوق كل خلفيات سطح المكتب.']}
(DOC/'control-contrast-review-20261025.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
testhead='\n'.join((ROOT/'tests/contrast-checks-20261025.txt').read_text(encoding='utf-8-sig').splitlines()[:3])
limits=''.join('<li>'+escape(v)+'</li>' for v in record['limits'])
html=f'''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>تباين الأزرار والقوائم — مرصد الجهاز</title><style>body{{font:15px/1.9 "Segoe UI",Tahoma,sans-serif;background:#eef3f1;color:#203a3c;margin:0}}main{{max-width:1080px;margin:auto;padding:26px}}header,section{{background:white;border:1px solid #cadbd2;border-radius:12px;padding:22px;margin-bottom:18px}}header{{border-top:5px solid #137b68}}h1{{font-size:27px}}h2{{font-size:21px}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:right;vertical-align:top;padding:10px;border-bottom:1px solid #d5e1da}}th{{background:#eff5f2}}.figures{{display:flex;gap:20px;flex-wrap:wrap}}figure{{margin:0;max-width:100%}}img{{max-width:100%;height:auto;width:360px}}figcaption{{font-size:13px;max-width:360px}}code{{overflow-wrap:anywhere}}pre{{white-space:pre-wrap}}a{{color:#086f64}}@media(max-width:650px){{main{{padding:12px}}header,section{{padding:16px}}}}@media print{{body{{background:white}}main{{padding:0}}section{{break-inside:avoid}}@page{{size:A4;margin:13mm}}}}</style><main><header><p>مرصد الجهاز · {escape(stamp)} · المواصفة1.2.1</p><h1>تصحيح اختفاء نصوص الأزرار والمنيو الفرعي</h1><p>عولجت تعارضات الألوان المثبتة، وفُعّل الويدجت المصحح مع الحفاظ على المظهر واللون والشفافية الحالية.</p></header><section><h2>الأسباب المثبتة والمعالجة</h2><table><tr><th>الحالة</th><th>المشكلة السابقة</th><th>التصحيح</th></tr><tr><td>اختيار الفترة أو الجهاز</td><td>نص زر ممتلئ فوق سطح عادي؛ أبيض على أبيض بتباين1:1 في الفاتح.</td><td>زوج صريح للخلفية والنص عند التحديد، مع احترام أولوية CSS.</td></tr><tr><td>قائمة WPF الفرعية</td><td>خلفية Windows فاتحة مع نص موروث فاتح؛ تباين يقارب1.14:1.</td><td>قالب فعلي للقائمة وخلفية Popup معتمة ونصوص وأيقونات مقترنة لكل حالة ومستوى.</td></tr><tr><td>«تنفيذ فعلي»</td><td>نص داكن ثابت فوق تحذير بني؛2.81:1 في الفاتح.</td><td>لون نص مقابل للتحذير، مع تحديث النص داخل الزر نفسه.</td></tr><tr><td>العناصر المعطلة والمتوقفة</td><td>تلاشي عام يجعل النص قريبًا من الخلفية، ويؤثر أيضًا عبر العنصر الأب.</td><td>عتامة كاملة للنص ولون ثانوي مقروء، مع إبقاء حالة التعطيل الوظيفية.</td></tr></table><p>تظل شفافية رسوم البيانات المقصودة محفوظة؛ أسطح الأوامر والقوائم معتمة لقراءة النص دون اعتماد على خلفية سطح المكتب.</p></section><section><h2>نتائج التحقق</h2><table><tr><th>النطاق</th><th>الحالات</th><th>أقل تباين</th></tr><tr><td>نصوص عناصر الواجهة — حساب CSS الفعلي في الاختبار المعزول</td><td>{web['evaluatedStates']}</td><td><bdi>{web['minimum']['ratio']:.3f}:1</bdi></td></tr><tr><td>قوائم WPF الأصلية وحالاتها</td><td>{menu['menuStateCases']}</td><td><bdi>{menu['minimumContrast']:.2f}:1</bdi></td></tr><tr><td>نصوص أوامر الويدجت الأصلية</td><td>{buttons['controlStateCases']}</td><td><bdi>{buttons['minimumTextContrast']:.2f}:1</bdi></td></tr><tr><td>مؤشر مفاتيح WPF — تباين غير نصي</td><td>تشغيل وإيقاف عبر اللوحات والمظهرين</td><td><bdi>{buttons['minimumSwitchContrast']:.2f}:1</bdi></td></tr></table><pre>{escape(testhead)}</pre><p>الحالات تشمل العادي والتحويم والتحديد والتفعيل والتعطيل والتركيز بحسب نوع العنصر. الفحص السابق لألوان الهوية وحدها لم يكن كافيًا لاكتشاف هذه التعارضات؛ أضيفت فحوص تعيد إثبات العيوب السابقة وتفحص الألوان بعد الوراثة والشفافية.</p></section><section><h2>صور من عناصر WPF الأصلية</h2><div class="figures"><figure><img src="contrast-assets-20261025/widget-menu-light-teal-states.png" alt="حالات المنيو الأصلية في المظهر الفاتح"><figcaption>فاتح: العادي والتحويم والتحديد والتعطيل والتركيز. عناصر اختبار؛ ليست قائمة مستخدم مفتوحة.</figcaption></figure><figure><img src="contrast-assets-20261025/widget-menu-dark-graphite-states.png" alt="حالات المنيو الأصلية في المظهر الداكن"><figcaption>داكن: النصوص ومؤشرات الاختيار ظاهرة على أسطح معتمة.</figcaption></figure></div></section><section><h2>التفعيل وحدود التقييم</h2><p>حالة التفعيل المسجلة: <bdi>{escape(runtime['status'])}</bdi>. أُعيد تشغيل الويدجت، وحُفظت قيمة العتامة والمظهر. الخادم لم يُعد تشغيله، وHTML وبصمة CSP مطابقان للمصدر. يحتاج تبويب التطبيق المفتوح إلى F5 لتحميل التنسيق الجديد.</p><ul>{limits}</ul><p>بصمة الواجهة: <code>{front_hash}</code><br>بصمة الويدجت: <code>{widget_hash}</code></p><p><a href="معايير_تصميم_التطبيق_والويدجت.html">المعايير وقائمة التحقق</a> · <a href="control-contrast-review-20261025.json">سجل الأدلة والحالات والبصمات</a></p></section></main></html>'''
(DOC/'تقرير_تباين_الأزرار_والقوائم_2026-10-25.html').write_text(html,encoding='utf-8')
print('Built contrast review and standards1.2.1 from matching source/native/runtime evidence.')
