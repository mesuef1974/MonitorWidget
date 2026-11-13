"""تشخيص السكون والاستيقاظ من سجل النظام (قراءة فقط، بصلاحية المستخدم، wevtutil بلا نافذة كما في bootwatch):
Power-Troubleshooter 1 = استيقاظ: وقت النوم ووقت الاستيقاظ وزمن الخروج من السكون (ms) ومصدره؛ Kernel-Power 42 = دخول السكون وسببه.

    parse(xml)              -> (wakes, enters) نقية
    sessions(wakes, enters) -> جلسات نوم من الأحدث، مع instant (استيقظ قبل INSTANT_S) وslow (خروج بطيء)
    summary(items, days)    -> حمولة صفحة الطاقة
    notes(s)                -> ملاحظات (المستوى، العنوان، النص)
    armed(text)             -> أسماء الأجهزة المسموح لها بإيقاظ الجهاز من مخرج powercfg /devicequery wake_armed
    payload()               -> يقرأ السجل الحي لآخر DAYS يومًا

الانطفاء أثناء دخول السكون (Kernel-Power 41) يرصده bootwatch ويظهر في «الإيقافات المفاجئة»؛ هنا ما نجح دخوله ثم كيف خرج.
"""
import calendar
import re
import subprocess
import time

DAYS = 14
INSTANT_S = 120      # عاد قبل دقيقتين من نومه: السكون لم يثبت
SLOW_MS = 10000      # خروج من السكون أطول من 10 ث
PAIR_S = 15          # حدث 42 يسبق وقت النوم المسجَّل في حدث الاستيقاظ بثوانٍ
MAX_ITEMS = 12
REASONS = {0: "زر الطاقة أو الغطاء", 2: "البطارية", 4: "طلب من تطبيق", 6: "مهلة السبات بعد السكون", 7: "خمول الجهاز"}   # Kernel-Power 42: Reason
_CNW = 0x08000000
_ISO = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)")


def _query(since_iso):
    xp = ("*[System[((Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1) or "
          "(Provider[@Name='Microsoft-Windows-Kernel-Power'] and EventID=42)) and TimeCreated[@SystemTime>='%s']]]" % since_iso)
    r = subprocess.run(["wevtutil", "qe", "System", "/q:" + xp, "/c:600", "/rd:true", "/f:xml"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=_CNW, timeout=30, check=False)
    return r.stdout if r.returncode == 0 else ""


def _armed_query():
    r = subprocess.run(["powercfg", "/devicequery", "wake_armed"], capture_output=True, text=True, encoding="oem", errors="replace",
                       creationflags=_CNW, timeout=15, check=False)
    return r.stdout if r.returncode == 0 else None


def armed(text):
    """None إن تعذّرت القراءة؛ وإلا قائمة الأسماء (قد تكون فارغة: powercfg يكتب NONE)."""
    if text is None:
        return None
    names = [ln.strip()[:80] for ln in text.splitlines() if ln.strip()]
    return [n for n in names if n.upper() != "NONE"][:20]


def _ts(text):
    m = _ISO.search(text or "")
    return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0)) if m else None


def _int(d, k):
    try:
        return int(str(d.get(k, "")).strip(), 0)
    except ValueError:
        return None


def parse(xml):
    """(wakes, enters): الاستيقاظات {sleepTs, wakeTs, wakeMs, driverMs, biosMs, enterMs, srcType, srcText, timerOwner, state}، والدخولات {ts, reason}."""
    wakes, enters = [], []
    for m in re.finditer(r"<Event .*?</Event>", xml or "", re.DOTALL):
        e = m.group(0)
        eid = re.search(r"<EventID[^>]*>(\d+)</EventID>", e)
        ts = _ts((re.search(r"SystemTime='([^']+)'", e) or [None, ""])[1])
        if not eid or ts is None:
            continue
        d = dict(re.findall(r"<Data Name='([^']+)'>([^<]*)</Data>", e))
        if eid.group(1) == "42":
            enters.append({"ts": ts, "reason": _int(d, "Reason")})
        elif eid.group(1) == "1" and "Power-Troubleshooter" in e:
            st, wt = _ts(d.get("SleepTime")), _ts(d.get("WakeTime"))
            if st is None or wt is None or wt < st:
                continue
            wakes.append({"sleepTs": st, "wakeTs": wt, "wakeMs": _int(d, "WakeDuration"), "driverMs": _int(d, "DriverInitDuration"), "biosMs": _int(d, "BiosInitDuration"),
                          "enterMs": _int(d, "SleepDuration"), "srcType": _int(d, "WakeSourceType"), "srcText": (d.get("WakeSourceText") or "").strip()[:80],
                          "timerOwner": (d.get("WakeTimerOwner") or "").strip()[:80], "state": _int(d, "TargetState")})
    return wakes, enters


def source(w):
    """مصدر الاستيقاظ بالنص الذي كتبه ويندوز حين يوجد، وإلا بالنوع المعروف."""
    if w.get("timerOwner"):
        return "مؤقّت: " + w["timerOwner"]
    if w.get("srcText"):
        return "جهاز: " + w["srcText"]
    return {0: "غير معروف", 1: "زر الطاقة"}.get(w.get("srcType"), "نوع %s" % w.get("srcType"))


def _sec(ms):
    return round(ms / 1000, 1) if ms is not None else None


def sessions(wakes, enters):
    out = []
    for w in sorted(wakes, key=lambda x: -x["wakeTs"]):
        near = [e for e in enters if abs(e["ts"] - w["sleepTs"]) <= PAIR_S]
        rs = min(near, key=lambda e: abs(e["ts"] - w["sleepTs"]))["reason"] if near else None
        asleep = w["wakeTs"] - w["sleepTs"]
        out.append({"ts": w["wakeTs"], "sleepTs": w["sleepTs"], "asleepS": asleep, "wakeS": _sec(w["wakeMs"]), "driverS": _sec(w["driverMs"]), "biosS": _sec(w["biosMs"]),
                    "enterS": _sec(w["enterMs"]), "reason": None if rs is None else REASONS.get(rs, "سبب %d" % rs), "source": source(w),
                    "hibernate": w.get("state") == 5, "instant": asleep < INSTANT_S, "slow": (w["wakeMs"] or 0) >= SLOW_MS})
    return out


def summary(items, days=DAYS):
    wk = [i["wakeS"] for i in items if i["wakeS"] is not None]
    inst = [i for i in items if i["instant"]]
    src = {}
    for i in inst:
        src[i["source"]] = src.get(i["source"], 0) + 1
    slowest = max((i for i in items if i["wakeS"] is not None), key=lambda i: i["wakeS"], default=None)
    return {"days": days, "n": len(items), "instant": len(inst), "slow": sum(1 for i in items if i["slow"]),
            "avgWakeS": round(sum(wk) / len(wk), 1) if wk else None, "maxWakeS": max(wk) if wk else None,
            "slowest": {k: slowest[k] for k in ("ts", "wakeS", "driverS", "biosS")} if slowest else None,
            "instantSources": sorted(({"source": k, "n": v} for k, v in src.items()), key=lambda x: -x["n"]), "items": items[:MAX_ITEMS]}


def notes(s):
    out = []
    if not s or not s.get("n"):
        return out
    if s["instant"] >= 2:
        top = s["instantSources"][0]
        lvl = "info" if top["source"] == "زر الطاقة" and top["n"] == s["instant"] else "warn"   # كلها بزر الطاقة = المالك أيقظه، لا خلل
        dev = any(x["source"].startswith("جهاز:") for x in s["instantSources"]) and s.get("armed")
        more = " %d أجهزة مسموح لها بالإيقاظ؛ أسماؤها في لوحة «السكون والاستيقاظ»." % len(s["armed"]) if dev else ""
        out.append((lvl, "الجهاز يستيقظ بُعيد نومه", "%d من %d مرة خلال %d يومًا عاد الجهاز قبل دقيقتين من نومه؛ المصدر الأغلب: %s (%d).%s" % (
            s["instant"], s["n"], s["days"], top["source"], top["n"], more)))
    sl = s.get("slowest")
    if sl and sl["wakeS"] is not None and sl["wakeS"] * 1000 >= SLOW_MS:
        part = "، منها %s ث في تهيئة التعريفات" % sl["driverS"] if sl.get("driverS") is not None and sl["driverS"] >= sl["wakeS"] / 2 else ""
        out.append(("info", "الخروج من السكون بطيء أحيانًا", "تجاوز الاستيقاظ 10 ث في %d من %d مرة خلال %d يومًا؛ أبطؤها %s ث%s. سجل ويندوز هنا لا يسمّي التعريف." % (s["slow"], s["n"], s["days"], sl["wakeS"], part)))
    return out


def payload(now=None, query=_query, armed_query=_armed_query):
    now = now or time.time()
    since = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - DAYS * 86400))
    wakes, enters = parse(query(since))
    out = summary(sessions(wakes, enters))
    try:
        out["armed"] = armed(armed_query())
    except Exception:
        out["armed"] = None
    return out
