"""Local evidence-based follow-up; no visual acceptance inferred from DOM tests."""
from pathlib import Path
import hashlib, html, json, re, shutil

root=Path(__file__).resolve().parent.parent
checks=(root/'tests/usability-recovery-checks-20261026.txt').read_text(encoding='utf-8')
match=re.search(r'المراحل الفاشلة: (\d+) من (\d+)',checks)
assert match and match[1]=='0' and 'الفحوص المتخطاة: 0' in checks
runtime=json.loads((root/'tests/usability-recovery-runtime-20261026.json').read_text(encoding='utf-8'))
sha=hashlib.sha256((root/'index.html').read_bytes()).hexdigest()
wsha=hashlib.sha256((root/'widget.ps1').read_bytes()).hexdigest()
assert runtime['status']=='pass' and runtime['sourceSha256']==sha
activation=json.loads((root/'tests/usability-recovery-widget-20261026.json').read_text(encoding='utf-8'))
assert activation['status']=='pass' and activation['widgetSha256']==wsha
fixtures=Path('C:/Users/mesue/.codex/visualizations/2026/10/25/01a17266-b23c-7271-993a-11ccd8b89286/qa-widget-balanced-20261026')
measurements=json.loads((fixtures/'manifest.json').read_text(encoding='utf-8-sig'))
assert len(measurements)>=10 and all(row['sourceSha256']==wsha for row in measurements)
assets=root/'docs/usability-assets-20261026'
assets.mkdir(exist_ok=True)
for name in ('balanced-dark-600.png','balanced-light-600.png','manifest.json'):
    shutil.copy2(fixtures/name,assets/name)
evidence=dict(date='2026-10-26',specVersion='3.1.0',sourceSha256=sha,widgetSha256=wsha,
              groups=int(match[2]),failures=0,skipped=0,browserRendered=False,
              status='implemented_automated_checks_passed_visual_pending')
(root/'tests/usability-recovery-tracker-20261026.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
document='''<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>تصحيح قابلية الاستخدام — مرصد الجهاز</title>
<style>*{box-sizing:border-box}body{margin:0;background:#edf3f4;color:#173039;font:16px/1.9 "Segoe UI",Tahoma,sans-serif}main{max-width:1000px;margin:auto;padding:26px}header{background:#123b43;color:white;border-radius:16px;padding:26px}section{background:white;border:1px solid #cad9dc;border-radius:12px;padding:22px;margin:18px 0}h1{font-size:27px}h2{font-size:21px}a{color:#006e66}code{direction:ltr;display:block;overflow-wrap:anywhere;font-size:12px}.note{border-right:5px solid #b98222}@media(max-width:600px){main{padding:12px}section,header{padding:16px}}@media print{body{background:white}main{padding:0}header{background:white;color:#173039}section{break-inside:avoid}}</style><main>
<header><p>مرصد الجهاز · 26 أكتوبر 2026 · متابعة المواصفة3.1.0</p><h1>تصحيح المسار وفق تجربة الاستخدام</h1><p>تستعيد هذه الدفعة التحكم المريح في الصفحات، وتعالج بطاقات المساحة الفارغة، وتغني الويدجت بمعلومات عملية.</p></header>
<section><h2>التنقل وعجلة الفأرة</h2><p>عاد تقليب الصفحات بعجلة الفأرة في مواضعه مع عداد سفلي واضح. يحتفظ المحتوى القابل للتمرير بسلوكه الطبيعي، ويتيح شريط الترقيم الصريح التحكم في صفحاته. لم يُعد تقسيم الحقول والنصوص إلى خطوات متتابعة.</p></section>
<section><h2>صفحة المساحة</h2><p>كانت البيانات موجودة في الخادم، بينما ورثت حاويات العرض قيود ارتفاع وقص من التخطيط السابق. أُصلح تدفق البطاقات وقوائم السجلات وتحديثها؛ بقيت أقسام المساحة الخمسة متاحة مع عدادها السفلي. اختبارات المحتوى تتحقق من صفوف فعلية لكل قسم، وليس وجود أزرار التنقل فقط.</p></section>
<section><h2>الويدجت</h2><p>أُضيف سياق القياس: الالتزام بالذاكرة واتجاه مختصر، وآخر صيانة وموعدها التالي وتنبيه واضح. بقيت نافذة واحدة بلا صفحات أو تمرير، بهيدر مقروء وهوية مشتركة. تتكيف مع مساحة العمل، وتبقى إجراءات التنظيف المؤثرة في التطبيق.</p></section>
<section><h2>التحقق</h2><p>نجحت __GROUPS__ مجموعة فحص معزولة دون فشل أو تخطٍّ، وتأكدت مطابقة المصدر الذي يقدمه الخادم وبصماتCSP. أُعيد تشغيل الويدجت المحدد لتفعيل نسخته الجديدة؛ حُفظ المظهر المشترك ولم يُعد تشغيل الخادم.</p><p><a href="../tests/usability-recovery-checks-20261026.txt">الفحوص</a> · <a href="../tests/usability-recovery-runtime-20261026.json">مطابقة الخادم</a> · <a href="../tests/usability-recovery-widget-20261026.json">تفعيل الويدجت</a> · <a href="../tests/usability-recovery-tracker-20261026.json">سجل النسخة</a> · <a href="معايير_تصميم_التطبيق_والويدجت.html">المعايير المحدثة</a></p></section>
<section class="note"><h2>حدود التحقق</h2><p>فحوصDOM وWPF المعزولة لا تعادل مراجعة التطبيق الفعلي بجميع أحجام النوافذ. بقي فحص المتصفح محجوبًا بإذن الأداة المحفوظ؛ لم تُسجّل مطابقة بصرية شاملة. اضغطF5 داخل نافذة التطبيق الأصلية لتحميل تحديث الواجهة.</p></section>
<section><h2>بصمات المصدر</h2><code>HTML: __SHA__</code><code>Widget: __WSHA__</code><p>تقرير محلي لم يُنشر على الإنترنت؛ يحتفظ بالأدلة السابقة كسجل تاريخي.</p></section></main></html>'''
native='''<section><h2>دليل الويدجت المعزول</h2><p>صورتان من مصدر WPF نفسه ببيانات اختبار، وليستا لقطة من نافذتك الحية. ارتفاع الإطار440DIP في مساحة عمل600DIP، و468DIP في768/1080DIP؛ الحالات الطويلة لا تتجاوز85% من مساحة العمل.</p><div style="display:flex;flex-wrap:wrap;gap:18px"><img style="max-width:100%;width:340px;object-fit:contain" src="usability-assets-20261026/balanced-dark-600.png" alt="الويدجت الغني في اختبار داكن معزول"><img style="max-width:100%;width:340px;object-fit:contain" src="usability-assets-20261026/balanced-light-600.png" alt="الويدجت الغني في اختبار فاتح معزول"></div><a href="usability-assets-20261026/manifest.json">القياسات وبصمة مصدر الصور</a></section>'''
document=document.replace('<section><h2>بصمات المصدر',native+'<section><h2>بصمات المصدر')
(root/'docs/تصحيح_قابلية_الاستخدام_2026-10-26.html').write_text(document.replace('__GROUPS__',match[2]).replace('__SHA__',html.escape(sha)).replace('__WSHA__',html.escape(wsha)),encoding='utf-8')
print('PASS usability report and tracker match checked source and widget')
