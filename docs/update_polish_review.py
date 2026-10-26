from pathlib import Path
import datetime
import hashlib
import json
import re
import shutil
from html import escape

ROOT=Path(__file__).resolve().parents[1]
DOC=ROOT/'docs'
SHOTS=Path(r'C:/Users/mesue/.codex/visualizations/2026/10/25/01a17266-b23c-7271-993a-11ccd8b89286/qa-shots')
matrix=json.loads((ROOT/'tests/page-polish-matrix-20261025.json').read_text(encoding='utf-8'))
pages=matrix['pages']
assert len(pages)==19 and len({p['id'] for p in pages})==19
widget_hash=hashlib.sha256((ROOT/'widget.ps1').read_bytes()).hexdigest()
front_hash=hashlib.sha256((ROOT/'index.html').read_bytes()).hexdigest()
assert front_hash==matrix['sourceSha256']
native=[]
for file in sorted(SHOTS.glob('widget-polish-*.png.layout.json')):
    data=json.loads(file.read_text(encoding='utf-8-sig'))
    assert data['sourceSha256']==widget_hash,file.name
    native.append({'file':file.name,**data})
assert len(native)==5
runtime_file=ROOT/'tests/polish-runtime-20261025.json'
runtime=json.loads(runtime_file.read_text(encoding='utf-8')) if runtime_file.exists() else {'status':'pending'}
stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).isoformat(timespec='seconds')
builder=DOC/'build_design_standard.py'
s=builder.read_text(encoding='utf-8').replace("'version':'1.1.2'","'version':'1.2.0'").replace('الإصدار 1.1.2 ·','الإصدار 1.2.0 ·')
panel='<section class="panel"><h2>حالة الدفعة الحالية · الشفافية والهيدر وجميع الصفحات</h2><p>مراجعة مصدر19 صفحة وإصلاح قص المحتوى والصفوف والتجاوب والقراءة، مع هيدر من صفين وشفافية خلفية لا تخفف النصوص. صور WPF وفحوص معزولة مرتبطة ببصمات المصدر؛ فحص المتصفح الفعلي بقي محجوبًا وقَبِل المستخدم متابعة الفحص المتاح وتسجيل حدوده.</p><p><a href="مراجعة_الشفافية_وجميع_الصفحات_2026-10-25.html">التقييم الحالي ومصفوفة الصفحات والأدلة</a></p></section>'
s=re.sub(r'<section class="panel"><h2>حالة الدفعة الحالية.*?</section>',lambda _:panel,s,count=1,flags=re.S)
s=s.replace('توجيه المستخدم الحالي: الإغلاق والتمرير أولًا','الدفعة السابقة: إصلاح الإغلاق والتمرير')
s=s.replace('بقية التنسيق والتخطيط مؤجلان إلى ما بعد مراجعة هذين الإصلاحين.','أعقبها طلب المستخدم مراجعة شفافية الويدجت وهيدره ثم جميع الصفحات؛ تُحفظ هذه الدفعة كسجل للإصلاحات الوظيفية السابقة.')
if '<td>1.2.0</td>' not in s:s=s.replace('<tr><td>1.1.2</td>','<tr><td>1.2.0</td><td>2026-10-25</td><td>إضافة معياري الهيدر والشفافية، ومراجعة19 صفحة، وتوثيق حدود التحقق البصري؛ حالات قائمة التحقق مستقلة لكل إصدار.</td></tr><tr><td>1.1.2</td>')
builder.write_text(s,encoding='utf-8')
exec(compile(s,str(builder),'exec'),{'__file__':str(builder),'__name__':'__main__'})
spec=json.loads((DOC/'design-standards-v1.json').read_text(encoding='utf-8'))
assert len(spec['criteria'])==39
files=['index.html','widget.ps1','server.py','tests/run_all.py','tests/frontend_pages_check.js','tests/frontend_layout_check.js','tests/frontend_wheel_check.js','tests/widget_polish_check.ps1','tests/widget_chart_geometry_check.ps1','tests/widget_close_check.ps1','tests/widget_bugfix_check.ps1','tests/widget_visual_fixture.ps1']
manifest={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files}
record={'date':stamp,'specVersion':'1.2.0','pages':pages,'browserRendered':False,'nativeWidget':native,'runtime':runtime,'manifest':manifest,'limits':['صفحات المتصفح لم تُفحص بصريًا؛ الحظر بقي قائمًا رغم الإذن وقَبِل المستخدم الفحص المتاح.','رسم الويدجت من WPF أصلي ببيانات اختبار موسومة، وليس تصويرًا لسطح المكتب الحي.','عتامة النص1 لا تثبت التباين فوق كل خلفية سطح مكتب عند الشفافية الشديدة.','النافذة المدمجة مع الظل340×271؛ البطاقة312×243. هدف260 للارتفاع يحتاج حسم احتساب الظل قبل اعتمادWG-01 كاملًا.']}
(DOC/'polish-review-20261025.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
assets=DOC/'polish-assets-20261025';assets.mkdir(exist_ok=True)
selected=['widget-polish-dark-stale-compact-096.png','widget-polish-light-stale-expanded-050.png']
for name in selected:shutil.copy2(SHOTS/name,assets/name)
rows=''.join(f'<tr><td><bdi>{escape(p["id"])}</bdi><br>{escape(p["scope"])}</td><td>{escape(p["issue"])}</td><td>{escape(p["change"])}</td><td>مصدر وفحوص معزولة؛ بصريًا لم يُفحص</td></tr>' for p in pages)
checks=(ROOT/'tests/polish-checks-20261025.txt').read_text(encoding='utf-8-sig')
testhead='\n'.join(checks.splitlines()[:3])
figures=''.join(f'<figure><img src="polish-assets-20261025/{name}" alt="رسم WPF أصلي للويدجت من المصدر"><figcaption>{"مدمج داكن بعتامة96%" if "compact" in name else "موسع فاتح بعتامة50%"} · بيانات اختبار قديمة موسومة، مع بيان آخر قراءة محفوظة.</figcaption></figure>' for name in selected)
limits=''.join('<li>'+escape(x)+'</li>' for x in record['limits'])
html=f'''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>تهذيب الويدجت ومراجعة الصفحات — مرصد الجهاز</title><style>body{{font:15px/1.9 "Segoe UI",Tahoma,sans-serif;background:#edf3f1;color:#203a3d;margin:0}}main{{max-width:1200px;margin:auto;padding:28px}}header,section{{background:white;border:1px solid #cadbd2;border-radius:12px;padding:22px;margin-bottom:18px}}header{{border-top:5px solid #137b68}}h1{{font-size:28px}}h2{{font-size:21px}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:right;vertical-align:top;padding:10px;border-bottom:1px solid #d5e1da}}th{{background:#eff5f2}}thead{{display:table-header-group}}.shots{{display:flex;gap:24px;align-items:start;flex-wrap:wrap}}figure{{margin:0;max-width:100%}}img{{width:340px;max-width:100%;height:auto}}figcaption{{max-width:340px;font-size:13px}}code{{overflow-wrap:anywhere}}pre{{white-space:pre-wrap}}a{{color:#086f64}}.note{{padding:12px;border-right:4px solid #9a6c20;background:#fff1da}}@media(max-width:700px){{main{{padding:12px}}section,header{{padding:16px}}.tablewrap{{overflow:auto}}table{{min-width:650px}}}}@media print{{body{{background:white}}main{{padding:0}}img{{width:65mm;max-height:160mm}}table{{font-size:11px}}@page{{size:A4;margin:12mm}}}}</style><main><header><p>مرصد الجهاز · {escape(stamp)} · المواصفة1.2.0</p><h1>هيدر أوضح، شفافية متناسقة، ومراجعة مصدر19 صفحة</h1><p>التحقق البصري المتاح للويدجت أصلي من WPF. مراجعة الصفحات من المصدر والفحوص المعزولة؛ لا تعني قبول عرض المتصفح النهائي.</p></header><section><h2>الويدجت</h2><ul><li>صف أول للشعار والاسم والخيارات والإخفاء، وصف ثانٍ لحالة الاتصال وحداثة القياس؛ نقل صلاحية المسؤول إلى التفاصيل.</li><li>عتامة الخلفية منفصلة عن النصوص والأزرار؛ تعالج البطاقات الداخلية بطبقة تلوين خفيفة بدل تعبئة معتمة.100% معتمة؛ الافتراضي96%.</li><li>تصحيح عرض الرسم وشريط التوزيع حسب المساحة الفعلية بعد شريط التمرير، مع مساحة كاملة لنسب المحور.</li><li>تمييز تفاصيل البيانات القديمة بأنها آخر قراءة محفوظة، بدل الإيحاء بأنها قراءة حالية.</li></ul><div class="shots">{figures}</div></section><section><h2>مصفوفة الصفحات</h2><p>قائمة كاملة بمفاتيح الصفحات19، وبالمشكلة أو نطاق المراجعة والمعالجة. لم تُجرَ عمليات تنظيف أو تعديل مهام أو قواعد للتحقق من الشكل.</p><div class="tablewrap"><table><thead><tr><th>الصفحة</th><th>المشكلة أو المراجعة</th><th>المعالجة</th><th>حالة الدليل</th></tr></thead><tbody>{rows}</tbody></table></div></section><section><h2>الفحوص والتفعيل</h2><pre>{escape(testhead)}</pre><p>تتضمن الفحوص العودة إلى أزرار الإغلاق والتحجيم الآمنة، وتمرير الفأرة في الاتجاهين، والمظهر المشترك، وهندسة الهيدر والخلفيات والشارتات. نجحت فحوص ألوان WPF وعتامة النافذة والنصوص والأزرار1؛ الخلفية وحدها تتغير.</p><p>حالة التفعيل المسجلة: <bdi>{escape(runtime.get('status','pending'))}</bdi>. القيمة المستهدفة للخلفية96%، مع حفظ بقية الإعدادات واللون والمظهر. تبويب التطبيق المفتوح يحتاج F5 لتحميل المصدر الجديد.</p><p>بصمة الواجهة: <code>{front_hash}</code><br>بصمة الويدجت: <code>{widget_hash}</code></p></section><section><h2>حدود التقييم وما يبقى</h2><ul>{limits}</ul><p>المعياران الجديدان WG-06 وWG-07 أُضيفا للمواصفة، ليصبح العدد39. الفحوص الجزئية لا تُحوّل المعيار المركب إلى نجاح كامل دون جميع أدلته. نتائج قائمة التحقق محفوظة لكل إصدار مستقلًا.</p><p><a href="معايير_تصميم_التطبيق_والويدجت.html">المعايير وقائمة التحقق</a> · <a href="polish-review-20261025.json">سجل الصفحات والأدلة وبصمات المصدر</a></p></section></main></html>'''
(DOC/'مراجعة_الشفافية_وجميع_الصفحات_2026-10-25.html').write_text(html,encoding='utf-8')
print('Built standards1.2.0,39criteria,19-page review and5matching native-widget evidence records.')
