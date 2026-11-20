# -*- coding: utf-8 -*-
"""CPU watch: cheap per-process CPU sampling (ctypes, no PowerShell, no psutil) + sustained-load alert.
Runs inside agent.py every 30 s. Stores the top consumers in monitor.db (table cpu_top, 3 days) and publishes
the current alert in meta('cpu_alert') for the widget and dashboard. Read-only with respect to the system."""
import ctypes, json, os, time
from ctypes import wintypes

import db
import alertcfg
import procsnap

K = ctypes.WinDLL("kernel32", use_last_error=True)
INTERVAL = 30
WINDOW_S = 600           # sustained-load window
MIN_AVG_CORES = 0.6      # avg cores over the window
MIN_PRESENCE = 0.8       # share of samples in which the process was >= 0.5 core
MIN_SAMPLES = 16         # need ~8 minutes of data before alerting
HINTS = {
    "dllhost.exe": "قد يكون خادم ملفات WSL",
    "msmpeng.exe": "فحص Microsoft Defender",
    "vmmemwsl": "ذاكرة ومعالج WSL / Docker",
    "vmmem": "ذاكرة ومعالج WSL / Docker",
    "searchindexer.exe": "فهرسة بحث ويندوز",
    "msedge.exe": "متصفح Edge",
    "python.exe": "سكربت بايثون", "pythonw.exe": "سكربت بايثون (قد يكون المرصد)",
}


K.OpenProcess.restype = ctypes.c_void_p
K.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
K.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(wintypes.FILETIME)] * 4
K.GetSystemTimes.argtypes = [ctypes.POINTER(wintypes.FILETIME)] * 3
K.CloseHandle.argtypes = [ctypes.c_void_p]


def _ft(f):
    return (f.dwHighDateTime << 32) | f.dwLowDateTime


def _snap():
    out = {}
    for pe in procsnap.processes():
        pid = pe.pid
        if pid:
            ph = K.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if ph:
                c, e, k, u = (wintypes.FILETIME() for _ in range(4))
                if K.GetProcessTimes(ph, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
                    out[pid] = (pe.name, _ft(c), _ft(k) + _ft(u))
                K.CloseHandle(ph)
            else:
                out.setdefault(-pid, (pe.name, 0, 0))  # seen but not readable
    return out


def _systimes():
    i, k, u = (wintypes.FILETIME() for _ in range(3))
    K.GetSystemTimes(ctypes.byref(i), ctypes.byref(k), ctypes.byref(u))
    return _ft(i), _ft(k) + _ft(u)  # kernel time already includes idle


_prev = None


def _ensure():
    with db._lock:
        c = db.conn()
        c.execute("CREATE TABLE IF NOT EXISTS cpu_top(ts INTEGER NOT NULL, name TEXT NOT NULL, cores REAL, n INTEGER, "
                  "PRIMARY KEY(ts,name)) WITHOUT ROWID")
        c.commit()


def sample():
    """Take one sample. First call only primes the baseline."""
    global _prev
    now = time.time()
    snap = _snap(); idle, tot = _systimes()
    prev, _prev = _prev, (now, snap, idle, tot)
    if prev is None:
        return "primed"
    dt = now - prev[0]
    if dt < 5:
        return "too soon"
    ncpu = os.cpu_count() or 1
    agg, cnt = {}, {}
    for pid, (name, ct, t) in snap.items():
        cnt[name] = cnt.get(name, 0) + 1
        p = prev[1].get(pid)
        if pid > 0 and p and p[1] == ct and t >= p[2]:
            agg[name] = agg.get(name, 0.0) + (t - p[2]) / 1e7 / dt
    td = tot - prev[3]
    busy = ncpu * (1.0 - (idle - prev[2]) / td) if td > 0 else 0.0
    top = sorted(((n, v) for n, v in agg.items() if v >= 0.03), key=lambda x: -x[1])[:8]
    rows = [(int(now), n, round(v, 3), cnt.get(n, 1)) for n, v in top]
    rows.append((int(now), "__total__", round(max(0.0, busy), 3), ncpu))
    _ensure()
    with db._lock:
        c = db.conn()
        c.executemany("INSERT OR REPLACE INTO cpu_top VALUES(?,?,?,?)", rows)
        c.commit()
    return "%s %.2f | total %.2f/%d cores" % (top[0][0] if top else "-", top[0][1] if top else 0, busy, ncpu)


def _meta_set(k, v):
    with db._lock:
        c = db.conn()
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        c.commit()


def _min_cores():
    try:
        return alertcfg.cpu_min_cores()
    except Exception:
        return MIN_AVG_CORES


def _ignored():
    """Names listed (one per line, # comments) in cpu_ignore.txt never raise an alert (they stay visible in the dashboard)."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cpu_ignore.txt"), encoding="utf-8") as f:
            base = {l.strip().lower() for l in f if l.strip() and not l.lstrip().startswith("#")}
    except OSError:
        base = set()
    try:
        return base | alertcfg.cpu_ignore()
    except Exception:
        return base


def evaluate(now=None):
    now = now or time.time()
    ign = _ignored()
    with db._lock:
        c = db.conn()
        ns = c.execute("SELECT COUNT(*) FROM cpu_top WHERE name='__total__' AND ts>=?", (int(now - WINDOW_S),)).fetchone()[0]
        res = None
        if ns >= MIN_SAMPLES:
            ncpu = c.execute("SELECT n FROM cpu_top WHERE name='__total__' ORDER BY ts DESC LIMIT 1").fetchone()[0] or 1
            q = c.execute("SELECT name, SUM(cores), SUM(CASE WHEN cores>=0.5 THEN 1 ELSE 0 END) FROM cpu_top "
                          "WHERE name!='__total__' AND ts>=? GROUP BY name", (int(now - WINDOW_S),)).fetchall()
            best = None
            for name, s, pres in q:
                avg = s / ns
                if name.lower() in ign:
                    continue
                if avg >= _min_cores() and pres >= MIN_PRESENCE * ns and (best is None or avg > best[1]):
                    best = (name, avg, pres)
            if best:
                res = {"ts": int(now), "name": best[0], "avg": round(best[1], 2), "minutes": round(best[2] * INTERVAL / 60.0),
                       "pct": round(100 * best[1] / ncpu, 1), "ncpu": ncpu, "hint": HINTS.get(best[0].lower(), "")}
    _meta_set("cpu_alert", json.dumps(res or {"ts": int(now), "name": ""}, ensure_ascii=False))
    if res:  # one event per process per 30 minutes
        try:
            last = json.loads(db._meta_get("cpu_alert_last", "{}") or "{}")
        except Exception:
            last = {}
        if now - last.get(res["name"], 0) > 1800:
            last[res["name"]] = int(now)
            _meta_set("cpu_alert_last", json.dumps(last))
            db.add_event(now, "cpu", "warn", "المعالج: %s يستهلك %.1f نواة" % (res["name"], res["avg"]),
                         "متوسط %.1f من %d نواة (%.0f%%) منذ نحو %s. %s" % (res["avg"], res["ncpu"], res["pct"], ar_minutes(res["minutes"]), res["hint"]))
    return res


def job():
    d = sample()
    if d in ("primed", "too soon"):
        return d
    r = evaluate()
    return d + (" | ALERT " + r["name"] if r else "")


def alert_state():
    """Fresh alert dict or None (stale when the agent is not sampling)."""
    try:
        v = json.loads(db._meta_get("cpu_alert", "") or "{}")
    except Exception:
        return None
    if not v or not v.get("name") or time.time() - v.get("ts", 0) > 180:
        return None
    return v


def api_payload():
    _ensure()
    now = time.time()
    with db._lock:
        c = db.conn()
        last = c.execute("SELECT MAX(ts) FROM cpu_top").fetchone()[0]
        if not last:
            return {"ok": False, "ncpu": os.cpu_count() or 1, "alert": None, "now": [], "avg10": [], "avg60": [], "series": {"t": [], "v": []}}
        ncpu = c.execute("SELECT n FROM cpu_top WHERE name='__total__' AND ts=?", (last,)).fetchone()
        ncpu = (ncpu[0] if ncpu else None) or os.cpu_count() or 1
        cur = [{"name": r[0], "cores": r[1], "n": r[2], "hint": HINTS.get(r[0].lower(), "")} for r in
               c.execute("SELECT name,cores,n FROM cpu_top WHERE ts=? AND name!='__total__' ORDER BY cores DESC", (last,))]
        tot = c.execute("SELECT cores FROM cpu_top WHERE ts=? AND name='__total__'", (last,)).fetchone()
        tot = tot[0] if tot else 0

        def avg(sec):
            ns = c.execute("SELECT COUNT(*) FROM cpu_top WHERE name='__total__' AND ts>=?", (int(now - sec),)).fetchone()[0] or 1
            return [{"name": r[0], "avg": round(r[1] / ns, 3), "hint": HINTS.get(r[0].lower(), "")} for r in
                    c.execute("SELECT name,SUM(cores) s FROM cpu_top WHERE name!='__total__' AND ts>=? GROUP BY name ORDER BY s DESC LIMIT 8", (int(now - sec),))]
        a10, a60 = avg(600), avg(3600)
        ser = c.execute("SELECT ts,cores FROM cpu_top WHERE name='__total__' AND ts>=? ORDER BY ts", (int(now - 3600),)).fetchall()
    return {"ok": True, "ts": last, "ncpu": ncpu, "total": tot, "now": cur, "avg10": a10, "avg60": a60,
            "series": {"t": [r[0] for r in ser], "v": [r[1] for r in ser]}, "alert": alert_state(), "stale": now - last > 180}


def ar_minutes(n):
    """«دقيقة» و«دقيقتين» و«3 دقائق» و«15 دقيقة»: مطابقة العدد في نصّ التنبيه."""
    n = int(n or 0)
    if n == 1:
        return "دقيقة"
    if n == 2:
        return "دقيقتين"
    return "%d %s" % (n, "دقائق" if 3 <= n % 100 <= 10 else "دقيقة")


def now_total():
    """Latest whole-machine CPU reading for the dashboard footer."""
    try:
        with db._lock:
            r = db.conn().execute("SELECT ts,cores,n FROM cpu_top WHERE name='__total__' ORDER BY ts DESC LIMIT 1").fetchone()
        if not r:
            return None
        return {"cores": round(r[1], 2), "n": r[2], "pct": round(100.0 * r[1] / (r[2] or 1), 1), "age": int(time.time()) - int(r[0])}
    except Exception:
        return None
