# -*- coding: utf-8 -*-
"""Reliability history for the Health page: crashes, forced power-offs, blue screens, disk/hardware errors, driver resets, app hangs.
Source = the Windows event logs, read by relia_scan.ps1 (read-only). Stored in rel_events; also reads io_stalls (freezes the disk collector caught).
Agent job `relia` runs hourly. CLI:  python relia.py --scan | --selftest"""

from sqlite_runtime import ensure_sqlite
ensure_sqlite()
import datetime, json, os, shutil, sqlite3, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
CNW = 0x08000000
KINDS = {  # kind: (Arabic label, severity 1..3)
    "crash": ("إغلاق غير متوقع (انقطاع طاقة أو إطفاء قسري)", 3),
    "unexpected": ("إغلاق غير سليم", 3),
    "bsod": ("شاشة زرقاء", 3),
    "disk": ("خطأ في القرص أو نظام الملفات", 3),
    "whea": ("خطأ في العتاد (معالج أو ذاكرة)", 3),
    "tdr": ("إعادة تشغيل تعريف الشاشة", 2),
    "stall": ("توقف أو تجمّد القرص", 2),
    "hang": ("تجمّد تطبيق", 1),
    "appcrash": ("انهيار تطبيق", 1),
}
DEFAULT_MAINT = [
    {"id": "old-backups", "label": "حذف مجلدات النسخ القديمة بعد التأكد من النسخة الجديدة", "due": "2026-10-25", "done": False},
    {"id": "d-backup", "label": "نسخة احتياطية خارجية على القرص D: تنتظر قرصاً", "due": None, "done": False},
    {"id": "pagefile", "label": "مراجعة حجم ملف الصفحات (33 GB محجوزة مقابل نحو 2.7 GB مستخدمة): قرارك قبل أي تغيير", "due": None, "done": False},
]


def _ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS rel_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, kind TEXT NOT NULL, code INTEGER, "
              "src TEXT, app TEXT, extra TEXT, msg TEXT, UNIQUE(ts,kind,code,src))")
    c.execute("CREATE INDEX IF NOT EXISTS i_rel_ts ON rel_events(ts)")


def _run_ps(days):
    exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(ROOT, "relia_scan.ps1"), "-Days", str(int(days))],
                       capture_output=True, timeout=150, creationflags=CNW if os.name == "nt" else 0)
    txt = r.stdout.decode("utf-8", "replace").strip()
    return json.loads(txt) if txt else []


def store(db, rows):
    n = 0
    with db._lock:
        c = db.conn(); _ensure(c)
        for e in rows:
            try:
                kind = str(e.get("kind"))
                if kind not in KINDS:
                    continue
                cur = c.execute("INSERT OR IGNORE INTO rel_events(ts,kind,code,src,app,extra,msg) VALUES(?,?,?,?,?,?,?)",
                                (int(e["ts"]), kind, int(e.get("id") or 0), str(e.get("src") or "")[:80], str(e.get("app") or "")[:80],
                                 str(e.get("code") or "")[:20], str(e.get("msg") or "")[:200]))
                n += cur.rowcount
            except Exception:
                continue
        c.execute("INSERT INTO meta(key,value) VALUES('rel_scan_ts',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(int(time.time())),))
        c.commit()
    return n


def scan(db, days=30, rows=None):
    """Read the event logs and store new events. Returns the number of new rows."""
    rows = _run_ps(days) if rows is None else rows
    return store(db, rows)


def job():
    import db
    n = scan(db, 30)
    return "%d new reliability events" % n


def _label(kind, app, code):
    lab = KINDS[kind][0]
    if kind in ("hang", "appcrash") and app:
        lab = "%s: %s" % (lab, app)
    if kind == "crash":
        lab = lab + (" · رمز %s" % code if code and code not in ("0x0",) else "")
    return lab


def summary(db, days=30):
    now = int(time.time()); t0 = now - days * 86400
    with db._lock:
        c = db.conn(); _ensure(c)
        rows = [dict(r) for r in c.execute("SELECT ts,kind,code,src,app,extra,msg FROM rel_events WHERE ts>=? ORDER BY ts DESC", (t0,))]
        try:
            stalls = [dict(r) for r in c.execute("SELECT ts_start ts,peak_lat_ms,top_json FROM io_stalls WHERE ts_start>=? ORDER BY ts_start DESC", (t0,))]
        except sqlite3.Error:
            stalls = []
        scanned = c.execute("SELECT value FROM meta WHERE key='rel_scan_ts'").fetchone()
    crashes = [r["ts"] for r in rows if r["kind"] == "crash"]
    ev = []
    for r in rows:
        if r["kind"] == "unexpected" and any(abs(r["ts"] - t) <= 900 for t in crashes):
            continue    # event 6008 repeats event 41 after the same forced power-off
        ev.append({"ts": r["ts"], "kind": r["kind"], "sev": KINDS[r["kind"]][1], "label": _label(r["kind"], r["app"], r["extra"]), "detail": r["msg"] or ""})
    for s in stalls:
        top = ""
        try:
            top = "، ".join(x.get("name", "") for x in json.loads(s["top_json"] or "[]")[:3])
        except Exception:
            pass
        ev.append({"ts": s["ts"], "kind": "stall", "sev": 2, "label": "%s · ذروة %d ms" % (KINDS["stall"][0], int(s["peak_lat_ms"] or 0)), "detail": top})
    ev.sort(key=lambda x: -x["ts"])

    def count(since):
        out = {}
        for e in ev:
            if e["ts"] >= since:
                out[e["kind"]] = out.get(e["kind"], 0) + 1
        return out
    daily = {}
    for e in ev:
        d = datetime.datetime.fromtimestamp(e["ts"]).strftime("%Y-%m-%d")
        x = daily.setdefault(d, {"sev": 0, "n": 0, "kinds": {}})
        x["sev"] = max(x["sev"], e["sev"]); x["n"] += 1; x["kinds"][e["kind"]] = x["kinds"].get(e["kind"], 0) + 1
    days_list = []
    for i in range(days - 1, -1, -1):
        d = (datetime.date.today() - datetime.timedelta(days=i)).isoformat()
        x = daily.get(d, {"sev": 0, "n": 0, "kinds": {}})
        days_list.append({"day": d, "sev": x["sev"], "n": x["n"], "kinds": x["kinds"]})
    crit = [e["ts"] for e in ev if e["sev"] >= 3]
    last_crit = max(crit) if crit else None
    c7 = count(now - 7 * 86400)
    sev7 = max([e["sev"] for e in ev if e["ts"] >= now - 7 * 86400] or [0])
    state = "bad" if sev7 >= 3 else ("warn" if sev7 == 2 or len(crit) else "ok")
    return {"ok": True, "now": now, "days": days, "scanned": int(scanned[0]) if scanned else None, "counts": count(t0), "counts7": c7,
            "daily": days_list, "recent": ev[:80], "total": len(ev), "lastCritical": last_crit,
            "daysSinceCritical": None if last_crit is None else int((now - last_crit) // 86400), "state": state,
            "labels": {k: v[0] for k, v in KINDS.items()}}


def ssd(db):
    """Volume written to the physical disk since the I/O collector started (a floor, not lifetime TBW)."""
    with db._lock:
        c = db.conn()
        try:
            r = c.execute("SELECT COALESCE(SUM(w_gb),0) w, COALESCE(SUM(r_gb),0) r, COUNT(*) n, MIN(day) d0 FROM io_day WHERE dev='disk0'").fetchone()
            r7 = c.execute("SELECT COALESCE(SUM(w_gb),0) w FROM io_day WHERE dev='disk0' AND day>=date('now','-7 day','localtime')").fetchone()
        except sqlite3.Error:
            return None
    n = r["n"] or 0
    return {"writtenGB": round(r["w"], 1), "readGB": round(r["r"], 1), "days": n, "since": r["d0"], "avgDayGB": round(r["w"] / n, 1) if n else None,
            "last7GB": round(r7["w"], 1)}


def maint(db):
    items = db.get_json("maint_items")
    if not items:
        items = DEFAULT_MAINT
        try:
            db.set_json("maint_items", items)
        except Exception:
            pass
    today = datetime.date.today()
    out = []
    for it in items:
        left = None
        if it.get("due"):
            try:
                left = (datetime.date.fromisoformat(it["due"]) - today).days
            except ValueError:
                left = None
        out.append(dict(it, daysLeft=left))
    return out


def extras(db):
    out = {}
    for k, fn in (("rel", lambda: summary(db)), ("ssd", lambda: ssd(db)), ("maint", lambda: maint(db))):
        try:
            out[k] = fn()
        except Exception:
            out[k] = None
    return out


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    if "--selftest" in sys.argv:
        import threading, types
        con = sqlite3.connect(":memory:", check_same_thread=False); con.row_factory = sqlite3.Row
        con.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);CREATE TABLE io_stalls(ts_start INT,ts_end INT,peak_lat_ms REAL,top_json TEXT);"
                          "CREATE TABLE io_day(day TEXT,dev TEXT,r_gb REAL,w_gb REAL);")
        kv = {}
        db = types.SimpleNamespace(_lock=threading.RLock(), conn=lambda: con, get_json=lambda k, d=None: kv.get(k, d), set_json=lambda k, v: kv.__setitem__(k, v))
        now = int(time.time())
        rows = [{"ts": now - 3 * 86400, "kind": "crash", "id": 41, "src": "Kernel-Power", "code": "0x0", "msg": "x"},
                {"ts": now - 3 * 86400 + 60, "kind": "unexpected", "id": 6008, "src": "EventLog", "msg": "y"},
                {"ts": now - 20 * 86400, "kind": "hang", "id": 1002, "src": "Application Hang", "app": "chrome.exe", "msg": "z"},
                {"ts": now - 40 * 86400, "kind": "disk", "id": 51, "src": "disk", "msg": "old"}]
        assert scan(db, rows=rows) == 4
        assert scan(db, rows=rows) == 0, "duplicates must be ignored"
        con.execute("INSERT INTO io_stalls VALUES(?,?,?,?)", (now - 7200, now - 7190, 412, '[{"name":"TiWorker.exe"}]'))
        for i in range(5):
            con.execute("INSERT INTO io_day VALUES(date('now',?,'localtime'),'disk0',3,10)", ("-%d day" % i,))
        s = summary(db)
        assert s["total"] == 3 and s["counts"]["crash"] == 1 and "unexpected" not in s["counts"] and s["state"] == "bad", s
        assert s["daysSinceCritical"] == 3 and len(s["daily"]) == 30 and s["recent"][0]["kind"] == "stall"
        assert ssd(db)["writtenGB"] == 50.0
        m = maint(db); assert len(m) == 3 and kv["maint_items"]
        assert set(extras(db)) == {"rel", "ssd", "maint"}
        print("selftest ok:", s["counts"], s["state"], ssd(db))
    elif "--scan" in sys.argv:
        import db
        print(scan(db, 30), "new"); print(json.dumps(summary(db)["counts"], ensure_ascii=False))
