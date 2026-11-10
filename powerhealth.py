"""صحة الطاقة: ما يفعله الجهاز عند البطارية الحرجة، وسبب كل إيقاف مفاجئ.

جزءان، كلاهما بصلاحية المستخدم وبلا نافذة:
  policy()      إجراء البطارية الحرجة ومستواها في خطة الطاقة النشطة (powercfg /q) وهل السبات مفعّل (السجل).
                إن كان الإجراء «سبات» والسبات معطّل، أو «لا شيء»، فالجهاز ينطفئ فجأة حين تفرغ البطارية.
  shutdowns()   حوادث Kernel-Power 41 التي سجّلها bootwatch، مقرونة بآخر قراءة بطارية قبل الانطفاء وأول قراءة بعد
                الإقلاع من power_samples؛ فيُعرف هل السبب فراغ البطارية أم فشل السكون أم غير ذلك.

التحليل بالمعرّفات (GUID) والقيم الست عشرية لا بالتسميات، لأن نص powercfg يتبع لغة النظام.
الدوال parse_policy/risk/cause/join نقية وتُختبر معزولة (tests/powerhealth_check.py).
"""
import re
import subprocess
import time

import bootwatch

CNW = 0x08000000
DAYS = 14
GUID_CRIT_ACTION = "637ea02f-bbcb-4015-8e2c-a1c7b9c0b546"
GUID_CRIT_LEVEL = "9a66d8d7-4ff7-4ef9-b5a2-5a326ca2a469"
GUID_LOW_LEVEL = "8183ba9a-e910-48da-8769-14ae6dc1170a"
ACTIONS = {0: "لا شيء", 1: "سكون", 2: "سبات", 3: "إيقاف تشغيل"}
EMPTY_BEFORE = 10     # آخر قراءة قبل الانطفاء على البطارية عند هذه النسبة أو أقل (مستوى «البطارية المنخفضة») = فرغت
EMPTY_AFTER = 3       # أول قراءة بعد الإقلاع عند هذه النسبة أو أقل = فرغت (الشاحن وُصل للتو)
RECHARGED = 25        # إن عادت القراءة بعد الإقلاع أعلى من السابقة بهذا القدر فالبطارية شُحنت وهو مطفأ: القراءة السابقة لا تكفي دليلًا
BEFORE_WINDOW = 3 * 86400
AFTER_WINDOW = 900
_GUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_HEX_RE = re.compile(r"0x([0-9a-f]{8})", re.IGNORECASE)


def parse_policy(text):
    """{action:{ac,dc}, level:{ac,dc}, lowLevel:{ac,dc}} من نص powercfg /q SCHEME_CURRENT SUB_BATTERY؛ المفقود None."""
    marks = [(m.start(), m.group(0).lower()) for m in _GUID_RE.finditer(text or "")]
    out = {}
    for name, guid in (("action", GUID_CRIT_ACTION), ("level", GUID_CRIT_LEVEL), ("lowLevel", GUID_LOW_LEVEL)):
        out[name] = None
        for i, (pos, g) in enumerate(marks):
            if g != guid:
                continue
            end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
            vals = [int(v, 16) for v in _HEX_RE.findall(text[pos:end])]
            if len(vals) >= 2:      # آخر قيمتين في مقطع الإعداد هما الحاليتان: الشاحن ثم البطارية
                out[name] = {"ac": vals[-2], "dc": vals[-1]}
            break
    return out


def risk(pol, hibernate):
    """سبب عدم نفع إجراء البطارية الحرجة على البطارية، أو None إن كان سليمًا أو غير معروف."""
    act = (pol.get("action") or {}).get("dc")
    if act is None:
        return None
    if act == 0:
        return "none"
    if act == 2 and hibernate is False:
        return "hibernate_off"
    if act == 1:
        return "sleep"
    return None


RISK_TEXT = {
    "none": "لا إجراء عند البطارية الحرجة: الجهاز ينطفئ فجأة حين تفرغ.",
    "hibernate_off": "الإجراء «سبات» والسبات معطّل في النظام، فلا يُنفَّذ: الجهاز ينطفئ فجأة حين تفرغ البطارية.",
    "sleep": "الإجراء «سكون»: السكون يستهلك البطارية المتبقية ثم ينطفئ الجهاز فجأة.",
}


def _hibernate_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Power") as k:
            return bool(winreg.QueryValueEx(k, "HibernateEnabled")[0])
    except OSError:
        return None


def _query_policy():
    r = subprocess.run(["powercfg", "/q", "SCHEME_CURRENT", "SUB_BATTERY"], capture_output=True, timeout=15, creationflags=CNW, check=False)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def policy():
    """حالة إجراء البطارية الحرجة الآن؛ {ok: False} إن تعذّرت القراءة (لا بطارية أو powercfg فشل)."""
    pol = parse_policy(_query_policy())
    if not pol.get("action"):
        return {"ok": False}
    hib = _hibernate_enabled()
    rk = risk(pol, hib)
    act = pol["action"]["dc"]
    return {"ok": True, "action": act, "actionName": ACTIONS.get(act, str(act)), "actionAc": pol["action"]["ac"],
            "level": (pol.get("level") or {}).get("dc"), "lowLevel": (pol.get("lowLevel") or {}).get("dc"),
            "hibernate": hib, "risk": rk, "riskText": RISK_TEXT.get(rk)}


def cause(kind, before, after):
    """(السبب، الوصف) من نوع bootwatch وقراءتَي البطارية: before=(ts, ac, batt) أو None، after كذلك."""
    if kind == "عطل":
        return "crash", "عطل النظام (شاشة زرقاء)"
    a_empty = bool(after and after[2] is not None and after[2] <= EMPTY_AFTER)
    b_empty = bool(before and before[2] is not None and before[2] <= EMPTY_BEFORE and before[1] == 0
                   and not (after and after[2] is not None and after[2] > before[2] + RECHARGED))
    if b_empty or a_empty:
        return "battery", "فرغت البطارية" + (" أثناء السكون" if kind == "سكون" else "")
    if kind == "سكون":
        return "sleep", "فشل الدخول في السكون أو الخروج منه"
    if kind == "زر":
        return "button", "إطفاء بزر الطاقة"
    return "unknown", "انقطاع بلا سبب مسجَّل (الشاحن أو العتاد)"


def join(events, sample_before, sample_after):
    """events: [(ts, kind)]؛ sample_before(ts)/sample_after(ts) تعيدان (ts, ac, batt) أو None. الأحدث أوّلًا."""
    items = []
    for ts, kind in sorted(events, reverse=True):
        b, a = sample_before(ts), sample_after(ts)
        c, text = cause(kind, b, a)
        items.append({"ts": ts, "kind": kind, "cause": c, "text": text,
                      "lastSeen": b[0] if b else None, "battBefore": b[2] if b else None, "acBefore": None if not b or b[1] is None else bool(b[1]),
                      "battAfter": a[2] if a else None})
    counts = {}
    for it in items:
        counts[it["cause"]] = counts.get(it["cause"], 0) + 1
    return {"days": DAYS, "items": items, "counts": counts}


def shutdowns(now=None):
    """الإيقافات المفاجئة في آخر DAYS يومًا بأسبابها، من جدولَي events وpower_samples."""
    import db
    now = int(now or time.time())
    prefix = bootwatch.TITLE % ""
    with db._lock:
        c = db.conn()
        rows = c.execute("SELECT ts,title FROM events WHERE source=? AND title LIKE ? AND ts>=? ORDER BY ts DESC",
                         (bootwatch.SOURCE, prefix + "%", now - DAYS * 86400)).fetchall()
        evs = [(int(r[0]), str(r[1])[len(prefix):]) for r in rows]

        def before(ts):
            return c.execute("SELECT ts,ac,batt FROM power_samples WHERE ts<? AND ts>=? AND batt IS NOT NULL ORDER BY ts DESC LIMIT 1", (ts, ts - BEFORE_WINDOW)).fetchone()

        def after(ts):
            return c.execute("SELECT ts,ac,batt FROM power_samples WHERE ts>=? AND ts<=? AND batt IS NOT NULL ORDER BY ts LIMIT 1", (ts, ts + AFTER_WINDOW)).fetchone()
        try:
            return join(evs, lambda t: _row(before(t)), lambda t: _row(after(t)))
        except Exception as e:     # جدول power_samples غير موجود بعد (أول تشغيل): الحوادث بلا قراءات
            if "no such table" not in str(e):
                raise
            return join(evs, lambda t: None, lambda t: None)


def _row(r):
    return (int(r[0]), r[1], r[2]) if r else None
