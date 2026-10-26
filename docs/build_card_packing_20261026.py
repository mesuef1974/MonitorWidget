"""Local review of card packing, with explicit geometry evidence limits."""
from pathlib import Path
import hashlib,json,re
from html import escape
root=Path(__file__).resolve().parent.parent
checks=(root/'tests/card-packing-checks-20261026.txt').read_text(encoding='utf-8')
m=re.search(r'المراحل الفاشلة: (\d+) من (\d+)',checks)
assert m and m[1]=='0' and 'الفحوص المتخطاة: 0' in checks
runtime=json.loads((root/'tests/card-packing-runtime-20261026.json').read_text(encoding='utf-8'))
sha=hashlib.sha256((root/'index.html').read_bytes()).hexdigest()
wsha=hashlib.sha256((root/'widget.ps1').read_bytes()).hexdigest()
assert runtime['status']=='pass' and runtime['sourceSha256']==sha
assert wsha=='54ded7ad23ec64049f7c4a1be887f4e783c68a41e81c7398d151a7856d286541','Full restored widget changed'
record=dict(date='2026-10-26',specVersion='3.3.0',groups=int(m[2]),failed=0,skipped=0,
 sourceSha256=sha,widgetSha256=wsha,widgetUnchanged=True,browserRendered=False,
 status='implemented_automated_checks_passed_visual_pending')
(root/'tests/card-packing-tracker-20261026.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
old=(root/'docs/استعادة_التقليب_والويدجت_2026-10-26.html').read_text(encoding='utf-8')
style=re.search(r'<style>(.*?)</style>',old,re.S)[0]
extra=''
if (root/'tests/card-packing-cases-20261026.json').is_file():
 extra='<p><a href="../tests/card-packing-cases-20261026.json">حالات قياس توزيع البطاقات</a> — هندسةDOM مضبوطة للاختبار، وليست قياسات من نافذتك الحية.</p>'
 evidence=json.loads((root/'tests/card-packing-cases-20261026.json').read_text(encoding='utf-8'))
 if isinstance(evidence,dict):
  recorded=evidence.get('sourceSha256',evidence.get('sourceSha'))
  if recorded:assert recorded==sha,'Card-packing cases belong to another source version'
  cases=evidence.get('cases',[])
 else:cases=evidence
 rows=[]
 labels={'memory':'الذاكرة','power':'الطاقة','processes':'العمليات','wsl':'WSL','cpu':'المعالج','gpu':'الرسوميات','space':'المساحة','io':'حركة القرص','appearance':'المظهر','home':'النظرة العامة','health':'الصحة','control':'التحكم','alerts':'التنبيهات','tasks':'الجدولة','rules':'القواعد','startup':'بدء التشغيل','database':'قاعدة البيانات','history':'التاريخ'}
 for case in cases if isinstance(cases,list) else []:
  viewport=case.get('viewport',{})
  if not all(key in case for key in ('component','visibleCards','pageCount')) or not all(key in viewport for key in ('width','height')):continue
  rows.append('<tr><td>'+escape(labels.get(case['component'],str(case['component'])))+'</td><td>'+escape(str(viewport['width'])+' × '+str(viewport['height']))+'</td><td>'+escape(str(case['visibleCards']))+'</td><td>'+escape(str(case['pageCount']))+'</td></tr>')
 if rows:extra+='<table style="width:100%;border-collapse:collapse" border="1" cellpadding="9"><caption>عينات من الاختبار المتحكم به: بطاقات متزامنة وصفحات المحتوى الزائد</caption><thead><tr><th>المكوّن</th><th>المساحة المختبرة</th><th>بطاقات ظاهرة</th><th>صفحات</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>'
page='''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>استغلال مساحة الصفحات — مرصد الجهاز</title>__STYLE__<main>
<header><p>مرصد الجهاز · 26 أكتوبر2026 · المواصفة3.3.0</p><h1>بطاقات أكثر قبل التقليب</h1><p>تحسين استغلال مساحة الصفحة دون العودة إلى التمرير أو اختصار الويدجت الكامل.</p></header>
<section><h2>المشكلة</h2><p>كانت مجموعات شقيقة تُخفى قبل قياس مساحة النافذة، وبعض البطاقات تُعرض في عمود واحد رغم إمكان عرض غيرها بجانبها. نتج عن ذلك فراغ وكثرة انتقالات لا تفرضها سعة النافذة.</p></section>
<section><h2>التعديل</h2><p>يُقاس توزيع البطاقات المرتبطة قبل ترقيم المحتوى الزائد. تستفيد الشبكة من اتساع النافذة لإظهار مزيد من البطاقات، مع احتساب العنوان والعداد السفلي وحدود القراءة. يبقى التقليب والعجلة، دون تمرير أو ملء الفراغ بتكبير رسوم لا تحتاج المساحة.</p><p>أضيف معيارRS-08 لاختبار إمكان إضافة البطاقة التالية إلى الصفحة الحالية، مع الحفاظ على المسودات والفلاتر والتركيز وموضع السجلات.</p></section>
<section><h2>التحقق</h2><p>نجحت __COUNT__ مجموعة فحص معزولة دون فشل أو تخطٍّ، وطابق المصدر الذي يقدمه الخادم الملف المفحوص وبصماتCSP. بقي الويدجت الكامل دون تعديل أو إعادة تشغيل في هذه الدفعة.</p><p><a href="../tests/card-packing-checks-20261026.txt">الفحوص</a> · <a href="../tests/card-packing-runtime-20261026.json">مطابقة الخادم</a> · <a href="../tests/card-packing-tracker-20261026.json">سجل النسخة</a> · <a href="معايير_تصميم_التطبيق_والويدجت.html">المعايير وقائمة التحقق</a></p>__EXTRA__</section>
<section><h2>حدود التحقق</h2><p>فحص المتصفح الفعلي ما زال محجوبًا بإذن الأداة المحفوظ. اختباراتDOM وهندستها المضبوطة تثبت سلوك الخوارزمية والتكامل ضمن عيناتها، ولا تثبت خلو كل نافذة حية من القص أو التداخل. لم يُسجّل اعتماد بصري شامل. اضغطF5 داخل نافذة التطبيق الأصلية لتحميل التعديل.</p></section>
<section><h2>المصدر المفحوص</h2><code>HTML: __SHA__</code><code>Widget unchanged: __WSHA__</code><p>تقرير محلي لم يُنشر على الإنترنت.</p></section></main></html>'''
(root/'docs/استغلال_مساحة_الصفحات_2026-10-26.html').write_text(page.replace('__STYLE__',style).replace('__COUNT__',m[2]).replace('__SHA__',sha).replace('__WSHA__',wsha).replace('__EXTRA__',extra),encoding='utf-8')
print('PASS card-packing report and tracker match checked source; full widget unchanged')
