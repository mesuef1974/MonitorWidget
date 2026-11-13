"""سجل الإقلاعات واتجاهه: الفحص المرتفع يقرأ آخر 8 إقلاعات فقط من سجل ويندوز، وهنا تُحفظ كلها في جدول boots حتى يُقارَن
وسيط آخر RECENT إقلاعات بوسيط ما قبلها (المعتاد)، ويُنبَّه مرة واحدة لكل إقلاع إن صار أبطأ أو ظهر مسبّب لم يُرَ من قبل.

    service_kinds()            أسماء خدمات ويندوز وتعريفاته من السجل (قراءة فقط): ويندوز يسجّل الخدمة أحيانًا كتطبيق (حدث 101)
    annotate(culprits, kinds)  يضيف kind = service/driver للمسبّب الذي اسمه اسم خدمة أو تعريف
    record(c, probe)           يحفظ إقلاعات الفحص ومسبّباتها (مكرَّر آمن: المفتاح وقت الإقلاع)
    rows(c)                    الإقلاعات المحفوظة من الأقدم
    trend(rows)                نقية: الحالة والوسيطان والمرحلة التي زادت والمسبّبات الجديدة ونقاط الرسم
    alert(t, add_event)        حدث «health» بوقت الإقلاع نفسه؛ جدول events فريد على (ts, source, title) فلا يتكرر

الاتصال c هو db.conn() والمستدعي يمسك db._lock؛ الجدول يُنشأ عند الحاجة كما في pageuse.
"""
import json
import statistics
import time

RECENT = 3          # الإقلاعات الأخيرة التي تُقارَن
BASE_MIN = 4        # أقل عدد قبلها ليُعرف المعتاد
BASE_MAX = 20       # المعتاد من آخر 20 إقلاعًا قبل الأخيرة، لا من تاريخ قديم تغيّر فيه الجهاز
SLOW_ABS = 8.0      # الزيادة التي تُعدّ بطئًا: 8 ث على الأقل
SLOW_REL = 0.25     # وربع المعتاد على الأقل
NEW_MIN_SEC = 5.0   # المسبّب الجديد يُذكر إن أخّر 5 ث فأكثر
LINK_S = 300        # ويندوز يسجّل المسبّب في لحظة إقلاعه؛ 5 دقائق هامش احتياط (كما في الواجهة)
KEEP_DAYS = 400
POINTS = 60
DDL = (
    "CREATE TABLE IF NOT EXISTS boots("
    "ts INTEGER PRIMARY KEY, boot_sec REAL NOT NULL, main_sec REAL, post_sec REAL, explorer_sec REAL, "
    "culprits TEXT NOT NULL DEFAULT '[]') WITHOUT ROWID",
)


def ensure(c):
    for stmt in DDL:
        c.execute(stmt)


def service_kinds():
    """{الاسم بأحرف صغيرة: 'service' أو 'driver'} من HKLM\\SYSTEM\\CurrentControlSet\\Services. أي فشل = قاموس فارغ."""
    out = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services") as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    key = winreg.EnumKey(root, i)
                    with winreg.OpenKey(root, key) as k:
                        typ = winreg.QueryValueEx(k, "Type")[0]
                        kind = "service" if typ & 0x30 else "driver" if typ & 0x03 else None
                        if not kind:
                            continue
                        out[key.lower()] = kind
                        try:
                            disp = winreg.QueryValueEx(k, "DisplayName")[0]
                            image = str(winreg.QueryValueEx(k, "ImagePath")[0])
                        except OSError:
                            disp = image = None
                        if image and "\\windowsapps\\" in image.lower():
                            continue   # خدمة مساعدة داخل حزمة تطبيق تحمل اسمه (Claude): الحدث يخصّ التطبيق لا الخدمة
                        if isinstance(disp, str) and disp and not disp.startswith("@"):   # «@ملف,-رقم» اسم غير مباشر لا يطابق شيئًا
                            out.setdefault(disp.strip().lower(), kind)
                except OSError:
                    continue
    except Exception:
        return {}
    return out


def annotate(culprits, kinds):
    """يصحّح نوع حدث 101 (تطبيق) حين يكون الاسم خدمة أو تعريفًا مسجَّلًا. يعدّل القائمة في مكانها ويعيدها."""
    for c in culprits if isinstance(culprits, list) else []:
        if not isinstance(c, dict) or c.get("id") != 101:
            continue
        name = str(c.get("name") or "").strip().lower()
        kind = kinds.get(name) or kinds.get(name[:-4] if name.endswith((".exe", ".sys")) else name)
        if kind:
            c["kind"] = kind
    return culprits


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def record(c, probe, now=None):
    """يحفظ إقلاعات الفحص. فحص قديم بلا bootCulprits لا يمحو مسبّبات محفوظة. يعيد عدد الإقلاعات المقروءة."""
    ensure(c)
    boots = [b for b in (probe or {}).get("boots") or [] if isinstance(b, dict) and _num(b.get("ts")) and _num(b.get("bootSec")) is not None]
    cs = (probe or {}).get("bootCulprits")
    for b in boots:
        ts = int(b["ts"])
        vals = (ts, _num(b["bootSec"]), _num(b.get("mainSec")), _num(b.get("postSec")), _num(b.get("explorerSec")))
        if isinstance(cs, list):
            mine = [{k: x.get(k) for k in ("id", "name", "degSec", "totalSec", "kind") if x.get(k) is not None} for x in cs
                    if isinstance(x, dict) and x.get("name") and _num(x.get("degSec")) is not None and _num(x.get("ts")) is not None and abs(x["ts"] - ts) <= LINK_S]
            c.execute("INSERT OR REPLACE INTO boots VALUES(?,?,?,?,?,?)", vals + (json.dumps(mine, ensure_ascii=False),))
        else:
            c.execute("INSERT OR IGNORE INTO boots(ts,boot_sec,main_sec,post_sec,explorer_sec) VALUES(?,?,?,?,?)", vals)
    c.execute("DELETE FROM boots WHERE ts<?", (int(now or time.time()) - KEEP_DAYS * 86400,))
    c.commit()
    return len(boots)


def rows(c, limit=POINTS):
    ensure(c)
    out = []
    for r in c.execute("SELECT ts,boot_sec,main_sec,post_sec,culprits FROM boots ORDER BY ts DESC LIMIT ?", (int(limit),)).fetchall():
        try:
            cu = json.loads(r[4] or "[]")
        except ValueError:
            cu = []
        out.append({"ts": r[0], "bootSec": r[1], "mainSec": r[2], "postSec": r[3], "culprits": cu if isinstance(cu, list) else []})
    return out[::-1]


def _med(vals):
    vals = [v for v in vals if v is not None]
    return round(statistics.median(vals), 1) if vals else None


def trend(rws):
    """rws من الأقدم. الحالة: learning (لا معتاد بعد)، steady، slower، faster."""
    rws = sorted(rws, key=lambda r: r["ts"])
    out = {"n": len(rws), "need": RECENT + BASE_MIN, "state": "learning", "recentSec": None, "baseSec": None, "deltaSec": None, "deltaPct": None,
           "phase": None, "newCulprits": [], "lastTs": rws[-1]["ts"] if rws else None,
           "points": [{"ts": r["ts"], "bootSec": r["bootSec"]} for r in rws[-POINTS:]]}
    recent, base = rws[-RECENT:], rws[:-RECENT][-BASE_MAX:]
    if len(rws) < RECENT + BASE_MIN:
        out["recentSec"] = _med(r["bootSec"] for r in recent)
        return out
    rm, bm = _med(r["bootSec"] for r in recent), _med(r["bootSec"] for r in base)
    delta = round(rm - bm, 1)
    out.update(recentSec=rm, baseSec=bm, deltaSec=delta, deltaPct=round(100 * delta / bm) if bm else None)
    gate = max(SLOW_ABS, SLOW_REL * bm)
    out["state"] = "slower" if delta >= gate else "faster" if delta <= -gate else "steady"
    if out["state"] == "slower":   # أي المرحلتين زادت: حتى سطح المكتب (main) أم بعده (post)
        d = {}
        for ph, key in (("main", "mainSec"), ("post", "postSec")):
            a, b = _med(r[key] for r in recent), _med(r[key] for r in base)
            if a is not None and b is not None:
                d[ph] = a - b
        if d and max(d.values()) > 0:
            out["phase"] = max(d, key=d.get)
    seen = {str(x.get("name", "")).lower() for r in rws[:-1] for x in r["culprits"]}
    out["newCulprits"] = sorted(({"name": x["name"], "degSec": x["degSec"], "id": x.get("id"), "kind": x.get("kind")} for x in rws[-1]["culprits"]
                                 if x.get("name") and (_num(x.get("degSec")) or 0) >= NEW_MIN_SEC and str(x["name"]).lower() not in seen),
                                key=lambda x: -x["degSec"])
    return out


def alert(t, add_event):
    """حدثان على الأكثر لآخر إقلاع؛ يعيد عدد ما أُرسل (التكرار يتجاهله جدول events)."""
    if not t or not t.get("lastTs"):
        return 0
    n = 0
    if t["state"] == "slower":
        where = {"main": "، والزيادة قبل ظهور سطح المكتب", "post": "، والزيادة بعد ظهور سطح المكتب"}.get(t.get("phase"), "")
        add_event(t["lastTs"], "health", "warn", "الإقلاع أبطأ من معتاده",
                  "وسيط آخر %d إقلاعات %s ث والمعتاد %s ث (+%s ث)%s." % (RECENT, t["recentSec"], t["baseSec"], t["deltaSec"], where))
        n += 1
    if t.get("newCulprits"):
        add_event(t["lastTs"], "health", "warn", "مسبّب جديد لبطء الإقلاع",
                  "، ".join("%s (%s ث)" % (x["name"], x["degSec"]) for x in t["newCulprits"][:4]))
        n += 1
    return n


def payload(c):
    return trend(rows(c))
