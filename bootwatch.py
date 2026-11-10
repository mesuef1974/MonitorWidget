"""رصد الإقلاع غير النظيف: يسجّل حدثًا في سجل الأحداث لكل حادثة Kernel-Power 41 بنوعها (عطل أو سكون أو زر الطاقة أو انقطاع مفاجئ).

يقرأ سجل النظام بـ wevtutil بلا نافذة وبصلاحية المستخدم (قراءة فقط) كل ساعة وعند إقلاع الوكيل، لآخر LOOKBACK_DAYS يومًا.
وقت الحدث المسجَّل هو وقت حادثة 41 نفسها، والجدول فريد على (ts, source, title) فتكرار القراءة لا يضاعف شيئًا.
الأنواع بالأولوية: BugcheckCode غير صفر = عطل النظام (مع الرمز)؛ SleepInProgress غير صفر = عند السكون؛
PowerButtonTimestamp غير صفر = بزر الطاقة؛ غير ذلك = انقطاع مفاجئ (طاقة أو عتاد). الدوال parse/classify/emit نقية وتُختبر معزولة.
"""
import re
import subprocess
import time

INTERVAL = 3600
LOOKBACK_DAYS = 14
SOURCE = "boot"
TITLE = "إقلاع غير نظيف: %s"     # powerhealth.shutdowns يقرأ الحوادث من جدول events بهذه البادئة
_CNW = 0x08000000


def _query(since_iso):
    xp = "*[System[(EventID=41) and TimeCreated[@SystemTime>='%s']]]" % since_iso
    r = subprocess.run(["wevtutil", "qe", "System", "/q:" + xp, "/c:200", "/rd:true", "/f:xml"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=_CNW, timeout=30, check=False)
    return r.stdout if r.returncode == 0 else ""


def parse(xml):
    """قائمة حوادث 41: {ts (ثوانٍ يونكس)، bugcheck، sleep، button}."""
    import calendar
    out = []
    for m in re.finditer(r"<Event .*?</Event>", xml or "", re.DOTALL):
        e = m.group(0)
        t = re.search(r"SystemTime='(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)", e)
        if not t:
            continue
        d = dict(re.findall(r"<Data Name='([^']+)'>([^<]*)</Data>", e))
        out.append({"ts": calendar.timegm(tuple(int(x) for x in t.groups()) + (0, 0, 0)), "bugcheck": _num(d, "BugcheckCode"),
                    "sleep": _num(d, "SleepInProgress"), "button": _num(d, "PowerButtonTimestamp")})
    return out


def _num(d, k):
    try:
        return int(str(d.get(k, "0")).strip() or 0, 0)
    except ValueError:
        return 0


def classify(ev):
    """(النوع، الخطورة، الوصف)."""
    if ev["bugcheck"]:
        return "عطل", "error", "عطل النظام (شاشة زرقاء) برمز 0x%X" % ev["bugcheck"]
    if ev["sleep"]:
        return "سكون", "warn", "انطفاء أثناء دخول السكون"
    if ev["button"]:
        return "زر", "warn", "انطفاء بزر الطاقة"
    return "انقطاع", "error", "انقطاع مفاجئ بلا زر ولا سكون (الشاحن أو البطارية أو العتاد)"


def emit(events, add_event):
    """حدث لكل حادثة؛ يعيد العدد."""
    for ev in events:
        kind, sev, desc = classify(ev)
        add_event(ev["ts"], SOURCE, sev, TITLE % kind, desc)
    return len(events)


def job():
    import db
    since = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - LOOKBACK_DAYS * 86400))
    evs = parse(_query(since))
    return "%d حادثة" % emit(evs, db.add_event)
