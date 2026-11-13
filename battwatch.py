"""إنذار فراغ البطارية قبل الانطفاء: الجهاز انطفأ مرارًا والبطارية فارغة، والمالك لا يعلم إلا بعد الإقلاع.

    evaluate(ps, crit)  نقية: None أو {level, pct, secLeft, critLevel, text} حين الجهاز على البطارية وقريب من حدّه الحرج
    current()           الحالة الآن (GetSystemPowerStatus رخيصة؛ سياسة البطارية الحرجة من ذاكرة insight المؤقتة 60 ث)
    guard()             مهمة الوكيل كل دقيقة: حدث «health» مرة لكل مستوى في كل نوبة تفريغ (تظهر في صفحة التنبيهات)

المستويان: تنبيه عند حدّ «البطارية المنخفضة» + WARN_PAD أو أقل من WARN_SECS متبقية؛ خطر عند الحدّ الحرج + BAD_PAD أو أقل من BAD_SECS.
الويدجت يعرض النص من /api/mini (battAlert) ويُشعِر مرة عند بلوغ الخطر. لا يغيّر أي إعداد طاقة: قراءة وتنبيه فقط.
"""
import json
import time

WARN_PAD = 5        # حدّ «البطارية المنخفضة» في ويندوز 10% افتراضيًا: ننبّه عند 15%
BAD_PAD = 5         # الحدّ الحرج 5% افتراضيًا: الخطر عند 10%
WARN_SECS = 20 * 60
BAD_SECS = 10 * 60
REARM_PAD = 5       # تنتهي النوبة بوصل الشاحن أو بارتفاع الشحن فوق حدّ التنبيه بـ5 نقاط
KEY = "battwatch.episode"


def _mins(sec):
    m = max(1, round(sec / 60))
    return "دقيقة" if m == 1 else "دقيقتين" if m == 2 else "%d دقائق" % m if m <= 10 else "%d دقيقة" % m


def thresholds(crit):
    crit = crit if isinstance(crit, dict) else {}
    low, lvl = crit.get("lowLevel"), crit.get("level")
    low = low if isinstance(low, int) and 1 <= low <= 90 else 10
    lvl = lvl if isinstance(lvl, int) and 1 <= lvl <= 90 else 5
    return max(low, lvl) + WARN_PAD, lvl + BAD_PAD, lvl


def evaluate(ps, crit=None):
    if not isinstance(ps, dict) or ps.get("noBattery") or ps.get("ac") is not False or ps.get("pct") is None:
        return None
    warn_at, bad_at, lvl = thresholds(crit)
    pct, sec = ps["pct"], ps.get("secLeft")
    sec = sec if isinstance(sec, int) and sec > 0 else None
    if pct <= bad_at or (sec is not None and sec <= BAD_SECS):
        level = "bad"
    elif pct <= warn_at or (sec is not None and sec <= WARN_SECS):
        level = "warn"
    else:
        return None
    act = (crit or {}).get("actionName") if isinstance(crit, dict) and crit.get("ok") else None
    text = "البطارية %d%%" % pct + (" ويبقى نحو %s" % _mins(sec) if sec is not None else "") + "؛ وصّل الشاحن"
    text += "، فعند %d%% ينفّذ الجهاز «%s»." % (lvl, act) if act else "."
    return {"level": level, "pct": pct, "secLeft": sec, "critLevel": lvl, "text": text}


def current():
    import insight
    import powerhealth
    ps = insight._power_status()
    if not ps or ps.get("noBattery") or ps.get("ac") is not False:
        return None   # على الشاحن أو بلا بطارية: لا حاجة لقراءة السياسة
    try:
        crit = insight._cached("battpolicy", 60, powerhealth.policy)
    except Exception:
        crit = None
    return evaluate(ps, crit)


def mini():
    """لحمولة /api/mini: لا يرمي أبدًا."""
    try:
        return current()
    except Exception:
        return None


def _load():
    import db
    try:
        v = json.loads(db._meta_get(KEY) or "null")
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def _save(v):
    import db
    with db._lock:
        db._meta_set(KEY, json.dumps(v))
        db.conn().commit()


def guard(now=None, current=current, load=_load, save=_save, add_event=None):
    """حدث لكل مستوى جديد في النوبة: تنبيه ثم خطر. النوبة تُغلق حين يزول الإنذار، فالتأرجح حول الحدّ لا يكرّر الحدث خلال ساعة."""
    if add_event is None:
        import db
        add_event = db.add_event
    now = int(now or time.time())
    a, ep = current(), load()
    if a is None:
        if ep and not ep.get("closed"):
            save({**ep, "closed": now})
        return "ok"
    fresh = not ep or (ep.get("closed") and now - ep["closed"] >= 3600)
    if fresh:
        ep = {"ts": now, "level": None}
    order = {None: 0, "warn": 1, "bad": 2}
    if order[a["level"]] > order.get(ep.get("level"), 0):
        add_event(now, "health", "error" if a["level"] == "bad" else "warn",
                  "البطارية توشك على الفراغ" if a["level"] == "bad" else "البطارية منخفضة", a["text"])
        save({"ts": ep["ts"], "level": a["level"]})
        return "alerted: " + a["level"]
    if ep.get("closed"):
        save({"ts": ep["ts"], "level": ep.get("level")})   # عاد الإنذار قبل ساعة: النوبة نفسها
    return "alert: " + a["level"]
