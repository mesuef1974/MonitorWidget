"""Regenerate the local Arabic progress page from a frozen, tested source."""
from pathlib import Path
import argparse, hashlib, json
from html import escape
ROOT=Path(__file__).resolve().parents[1];DOC=ROOT/'docs'
parser=argparse.ArgumentParser();parser.add_argument('--runtime',required=True);parser.add_argument('--checks',required=True);parser.add_argument('--date',required=True)
args=parser.parse_args()
import re
assert re.fullmatch(r'\d{4}-\d{2}-\d{2}',args.date)
stamp=args.date.replace('-','')
for name in (args.runtime,args.checks):
    assert Path(name).name==name
runtime=json.loads((ROOT/'tests'/args.runtime).read_text(encoding='utf-8'))
sha=hashlib.sha256((ROOT/'index.html').read_bytes()).hexdigest()
assert runtime['status']=='pass' and runtime['sourceSha256']==sha
checks=(ROOT/'tests'/args.checks).read_text(encoding='utf-8')
assert 'المراحل الفاشلة: 0' in checks and 'الفحوص المتخطاة: 0' in checks
tracker=json.loads((ROOT/'tests/no-scroll-tracker-20261025.json').read_text(encoding='utf-8'))
names=dict(home='نظرة عامة',health='الصحة',alerts='التنبيهات',advice='التوصيات',cpu='المعالج',gpu='الرسوميات',mem='الذاكرة',procs='العمليات',wsl='WSL',power='الطاقة',space='المساحة',io='حركة القرص',startup='بدء التشغيل',tasks='الجدولة',rules='القواعد',history='التاريخ',dbx='قاعدة البيانات',ctrl='التحكم',appearance='المظهر المشترك')
rows='';done=0
for page in tracker['application']['pages']:
    state=page['status'];implemented=state.startswith('implemented');done+=implemented
    status='نُفذ مع قياس وقت التشغيل؛ اعتماد بصري معلق' if implemented else 'معالجة مرحلية غير مدمجة' if state.startswith('stage') else 'قيد التنفيذ' if state=='in_progress' else 'لم تنفذ معالجة منع التمرير بعد'
    rows+=f'<tr><td>{escape(names[page["id"]])}</td><td>{status}</td><td>لم يُفحص المتصفح فعليًا</td></tr>'
record=dict(clientDate=args.date,sourceSha256=sha,runtime=runtime,tracker=tracker,applicationImplementedPages=done,applicationTotalPages=19,visualAcceptance=False)
(DOC/f'no-scroll-progress-{stamp}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
heading='\n'.join(checks.splitlines()[:3])
html=f'''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>متابعة منع التمرير — مرصد الجهاز</title><style>*{{box-sizing:border-box}}body{{margin:0;background:#f1f6f5;color:#173633;font:16px/1.9 "Segoe UI",Tahoma,sans-serif}}main{{max-width:1100px;margin:auto;padding:26px}}header,section{{background:white;border:1px solid #d5e1de;border-radius:14px;padding:22px;margin-bottom:18px}}h1{{font-size:28px}}h2{{font-size:21px}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:right;padding:10px;border-bottom:1px solid #d5e1de;vertical-align:top}}a{{color:#006e64}}pre,code{{direction:ltr;unicode-bidi:isolate;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}.note{{border-right:5px solid #a56a00}}@media(max-width:600px){{main{{padding:10px}}header,section{{padding:14px}}th,td{{padding:6px;font-size:13px}}}}@media print{{body{{background:white}}main{{max-width:none;padding:0}}tr{{break-inside:avoid}}}}</style><main><header><p>مرصد الجهاز · معايير2.0.0 · متابعة مستمرة</p><h1>الويدجت واحد مدمج؛ صفحات التطبيق تُعالج تدريجيًا</h1><p>نُفذت معالجة منع التمرير في {done} من19 صفحة برمجيًا مع قياس مساحة وقت التشغيل. هذا العدد يصف التنفيذ، ولا يعني اعتماد الرسم البصري.</p></header>
<section><h2>الويدجت</h2><p>واجهة واحدة، بلا توسعة أو صفحات تفاصيل أو منطقة تمرير. الإطار المقاس340×248 DIP، بثلاثة أزرار فقط: الخيارات والإخفاء وفتح التطبيق. فُعل مع الحفاظ على اللون والشفافية والموضع.</p><p><a href="ويدجت_واحد_مدمج_2026-10-25.html">الأدلة وقيود القياس الأصلي المعزول</a></p></section>
<section><h2>جرد الصفحات</h2><table><thead><tr><th>الصفحة</th><th>حالة التنفيذ</th><th>حالة التحقق البصري</th></tr></thead><tbody>{rows}</tbody></table></section>
<section><h2>آخر نسخة مجمدة مفحوصة</h2><pre>{escape(heading)}</pre><p>تطابق الملف المخدوم وبصمات CSP ثبت بفحص قراءة فقط. الخادم والويدجت يعملان. التغيير الجديد في HTML يحتاج تحديث النافذة المفتوحة، ولا يتطلب إعادة تشغيل الخادم.</p><p><a href="../tests/{escape(args.checks)}">نتائج الفحوص</a> · <a href="no-scroll-progress-20261025.json">السجل والبصمات</a></p><code>{sha}</code></section>
<section class="note"><h2>حدود القبول</h2><p>فحص المتصفح الفعلي محجوب بإذن محفوظ؛ لم يُستخدم مسار بديل لتجاوزه. لا تُحسب اختبارات DOM ذات الأبعاد المصطنعة أو فحوص المصدر نجاحًا بصريًا عند480×640 أو درجات التكبير المختلفة.</p><p>يُثبت غياب التمرير في الصفحات المعدلة فقط بعد تحقق حدود عناصرها وقت التشغيل، مع ترقيم المحتوى الزائد. إذا لم يسع عنصر منفرد تبقى بياناته ظاهرة مع رسالة واضحة، ولا يُحسب المعيار مستوفى. لا يُعد مجرد إخفاء الأشرطة إصلاحًا.</p><p><a href="معايير_تصميم_التطبيق_والويدجت.html">المعايير وقائمة التحقق</a> · <a href="التحكم_والتنبيهات_دون_تمرير_2026-10-25.html">دفعة التحكم والتنبيهات</a></p></section></main></html>'''
html=html.replace('no-scroll-progress-20261025.json',f'no-scroll-progress-{stamp}.json').replace('مرصد الجهاز · معايير2.0.0 · متابعة مستمرة',f'مرصد الجهاز · معايير{tracker.get("specVersion","2.0.0")} · متابعة {args.date}')
(DOC/f'متابعة_منع_التمرير_{args.date}.html').write_text(html,encoding='utf-8')
print(f'Updated local no-scroll progress: {done}/19 implemented; visual acceptance remains pending.')
