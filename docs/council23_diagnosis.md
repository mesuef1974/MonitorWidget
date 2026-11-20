# البند 23 من المجلس 19/11 — تشخيص مهام التحديث المعطّلة (20/11/2026)

البند: مهام تحديث Edge وLenovo TVSU وصيانة Vantage معطّلة؛ تحديد من عطّلها، وفحص قرائي يحذّر من تعطيل مهام التحديث.

## ملاحظة منهجية
Get-ScheduledTask بصلاحية عادية لا يرى هذه المهام أصلًا (محجوبة بواصفات أمانها)، فبدت «محذوفة». القراءة الصحيحة بصلاحية مسؤول.

## الحال الحي (قراءة مرتفعة، 20/11 07:00)
| المهمة | الحالة | آخر تشغيل | ملاحظة |
|---|---|---|---|
| \MicrosoftEdgeUpdateTaskMachineCore | مفعّلة | 20/11 05:00 (0) | أعاد محدِّث Edge كتابتها 19/11 20:34 (حدث 140 من حساب الجهاز) |
| \MicrosoftEdgeUpdateTaskMachineUA | مفعّلة | 20/11 05:39 (0) | كل ساعة |
| \TVT\TVSUUpdateTask | مفعّلة | 17/11 17:46 (0) | أسبوعية: System Update يعمل |
| \TVT\TVSUUpdateTask_UserLogOn | معطّلة | 17/10 09:16 | عند الدخول فقط |
| \Lenovo\Vantage\Schedule\LenovoSystemUpdateAddin_WeeklyTask | مفعّلة | 17/11 19:01 (0) | |
| \Lenovo\Vantage\Lenovo.Vantage.ServiceMaintainance | معطّلة | 20/7/2026 (1056) | |

## من عطّلها
- Edge Core: صفحة «بدء التشغيل» في المرصد (startup_mgr.toggle)، ثلاث مرات: 20/10 19:03 و26/10 21:54 و7/11 20:25 (جدول actions، kind=startup). المهمة لها مشغّل دخول فظهرت في الصفحة كعنصر بدء. محدِّث Edge يعيد تفعيلها عند تحديثه، ولهذا رآها المجلس معطّلة صباح 19/11 وهي مفعّلة الآن.
- TVSUUpdateTask_UserLogOn: صفحة «بدء التشغيل» في المرصد، 17/10 13:53:11 (actions). المهمة الأسبوعية الأساسية لم تُمسّ.
- Vantage ServiceMaintainance: ليس المرصد (لا سجل لها في actions الذي يبدأ 16/10، وآخر تشغيل لها 20/7 أي قبل ذلك). الفاعل غير معروف: سجل TaskScheduler/Operational يبدأ 19/11 17:12 (سقفه 10 MB)، وتدقيق «Other Object Access Events» معطّل فلا أحداث 4698-4702. تاريخ كتابة ملفها 23/10 10:22 مشترك مع مهام كثيرة (إعادة كتابة جماعية للمخزن)، فلا يدل على وقت التعطيل.
- مهام Vantage الأخرى المعطّلة (Telemetry وMessaging وHeartbeat وSmartPerformance) ليست مهام تحديث؛ توقّف تشغيلها حول 18/10، ولا سجل لها في actions.

## الإصلاح (PR البند 23)
1. updwatch.py: فحص قرائي بلا PowerShell، بعد الإقلاع بدقيقتين ثم كل 6 ساعات: لكل محدِّث برنامجه مثبَّت (Edge وChrome وLenovo System Update وVantage وOffice) حالة warn إن غابت مهامه أو عُطّلت كلها، وpartial إن عُطّل بعضها. تنبيه warn بالمصدر updates عند تغيّر المجموعة وحده. بلا صلاحية مسؤول: skipped.
2. startup_mgr.toggle يرفض تعطيل مهمة تحديث (الخط الأحمر 6) ويسمح بتفعيلها؛ الصفحة تُظهر وسم «تحديث» ولوحة «مهام التحديث».
3. لا إصلاح آلي: إعادة تفعيل TVSUUpdateTask_UserLogOn أو ServiceMaintainance قرار المالك من الصفحة.
