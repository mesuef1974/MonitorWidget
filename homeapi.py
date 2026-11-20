# -*- coding: utf-8 -*-
"""Overview-page payload (read-only SQL over tables the agent already fills). Used by server.py: GET /api/home
Everything here is a SELECT or a process-name snapshot: nothing is written, nothing is killed."""

from sqlite_runtime import ensure_sqlite
ensure_sqlite()
import sqlite3, time

# background duties the user keeps asking about: (key, Arabic label, process names)
BG = [
    ("update", "تحديث ويندوز", ("tiworker.exe", "trustedinstaller.exe", "mousocoreworker.exe", "usoclient.exe", "sihclient.exe", "wuauclt.exe")),
    ("defender", "ديفندر", ("msmpeng.exe", "mpdefendercoreservice.exe", "mpcmdrun.exe")),
    ("index", "الفهرسة", ("searchindexer.exe", "searchprotocolhost.exe", "searchfilterhost.exe")),
    ("onedrive", "ون درايف", ("onedrive.exe", "filecoauth.exe")),
    ("wsl", "WSL ودوكر", ("vmmemwsl", "vmmem", "wslservice.exe", "com.docker.backend.exe", "docker desktop.exe")),
    ("claude", "Claude", ("claude.exe",)),
]
CPU_BUSY = 0.12      # cores
IO_BUSY_MB = 30.0    # MB moved in the last 5 minutes


def running_names():
    """Lower-case exe names of every running process (Toolhelp snapshot); empty set when unavailable."""
    try:
        import procsnap
        return {p.name.lower() for p in procsnap.processes()}
    except Exception:
        return set()


def _rows(c, sql, args=()):
    try:
        return [dict(r) for r in c.execute(sql, args)]
    except sqlite3.Error:
        import errlog
        errlog.swallowed()
        return []


def _thin(pairs, n=90):
    """Keep at most n points by averaging fixed-size groups (pairs = [(t, v|None), ...])."""
    if len(pairs) <= n:
        return pairs
    k = -(-len(pairs) // n)
    out = []
    for i in range(0, len(pairs), k):
        b = pairs[i:i + k]
        vs = [v for _, v in b if v is not None]
        out.append((int(sum(t for t, _ in b) / len(b)), round(sum(vs) / len(vs), 2) if vs else None))
    return out


def _clean(name):
    n = (name or "").strip()
    return n[:-4] if n.lower().endswith(".exe") else n


def payload(db, running=None):
    now = int(time.time())
    t0 = now - 3600
    running = running_names() if running is None else running
    with db.ro() as c:   # own read-only connection: heavy aggregations never hold the global write lock
        # ---- memory series (last hour)
        ram = _rows(c, "SELECT ts,ram_pct,commit_pct FROM samples WHERE ts>=? ORDER BY ts", (t0,))
        # ---- cpu: whole machine + per process, latest snapshot
        last = (_rows(c, "SELECT MAX(ts) t FROM cpu_top") or [{"t": None}])[0]["t"]
        ncpu, cpu_tot, cpu_now = None, None, []
        if last:
            tot = _rows(c, "SELECT cores,n FROM cpu_top WHERE name='__total__' AND ts=?", (last,))
            if tot:
                cpu_tot, ncpu = tot[0]["cores"], tot[0]["n"]
            cpu_now = _rows(c, "SELECT name,cores FROM cpu_top WHERE name!='__total__' AND ts=? ORDER BY cores DESC", (last,))
        cpu_ser = _rows(c, "SELECT ts,cores,n FROM cpu_top WHERE name='__total__' AND ts>=? ORDER BY ts", (t0,))
        # ---- memory per process (latest snapshot)
        mem = _rows(c, "SELECT name,n,private_mb FROM procs WHERE ts=(SELECT MAX(ts) FROM procs) ORDER BY private_mb DESC LIMIT 40")
        # ---- disk I/O
        io_now = _rows(c, "SELECT * FROM v_io_now WHERE dev='disk0'") or _rows(c, "SELECT * FROM v_io_now LIMIT 1")
        io_ser = _rows(c, "SELECT ts,lat_w_avg,lat_w_max,busy_avg,r_mb,w_mb FROM io_min WHERE dev='disk0' AND ts>=? ORDER BY ts", (t0,))
        io_top = _rows(c, "SELECT name,SUM(w_mb) w,SUM(r_mb) r FROM io_procs WHERE ts>=? GROUP BY name", (now - 300,))
        stalls = _rows(c, "SELECT ts_start,ts_end,peak_lat_ms,top_json FROM io_stalls WHERE ts_start>=? ORDER BY ts_start", (t0,))
        stalls24 = (_rows(c, "SELECT COUNT(*) n FROM io_stalls WHERE ts_start>=?", (now - 86400,)) or [{"n": 0}])[0]["n"]
        # ---- gpu (latest)
        g = (_rows(c, "SELECT ts,temp,util,vram_mb,vram_total,power FROM gpu ORDER BY ts DESC LIMIT 1") or [None])[0]
        gser = _rows(c, "SELECT ts,temp,util FROM gpu WHERE ts>=? ORDER BY ts", (t0,))
        trims = _rows(c, "SELECT ts,before_pct,after_pct,freed_mb FROM trims WHERE ts>=? ORDER BY ts", (t0,))

    # ---- who uses what (union of the top of each list, scored by share of the leader)
    cpu_map = {_clean(r["name"]).lower(): r["cores"] for r in cpu_now}
    mem_map = {_clean(r["name"]).lower(): r["private_mb"] for r in mem}
    io_map = {_clean(r["name"]).lower(): (r["w"] or 0) + (r["r"] or 0) for r in io_top}
    cm, mm, im = max(list(cpu_map.values()) + [0.1]), max(list(mem_map.values()) + [100.0]), max(list(io_map.values()) + [10.0])
    names = set(sorted(cpu_map, key=cpu_map.get, reverse=True)[:4]) | set(sorted(mem_map, key=mem_map.get, reverse=True)[:4]) | set(sorted(io_map, key=io_map.get, reverse=True)[:4])
    disp = {}
    for r in cpu_now + mem + io_top:
        disp.setdefault(_clean(r["name"]).lower(), _clean(r["name"]))
    top = []
    for nm in names:
        cpu, mb, io = cpu_map.get(nm, 0.0), mem_map.get(nm, 0.0), io_map.get(nm, 0.0)
        top.append({"name": disp.get(nm, nm), "cpu": round(cpu, 2), "memMB": round(mb), "ioMB": round(io, 1),
                    "score": round(2 * cpu / cm + mb / mm + 1.5 * io / im, 3)})
    top = sorted(top, key=lambda x: -x["score"])[:8]

    # ---- background duties
    bg = []
    for key, label, procs in BG:
        cpu = sum(cpu_map.get(p[:-4] if p.endswith(".exe") else p, 0.0) for p in procs)
        io = sum(io_map.get(p[:-4] if p.endswith(".exe") else p, 0.0) for p in procs)
        present = any(p in running for p in procs) if running else None
        state = "busy" if (cpu >= CPU_BUSY or io >= IO_BUSY_MB) else ("idle" if present or present is None else "off")
        bg.append({"key": key, "label": label, "state": state, "cpu": round(cpu, 2), "ioMB": round(io, 1)})

    cpu_pct = None if cpu_tot is None or not ncpu else round(100.0 * cpu_tot / ncpu, 1)
    marks = [{"t": r["ts"], "kind": "trim", "label": "تنظيف الذاكرة %s%% ← %s%%" % (r["before_pct"], r["after_pct"])} for r in trims]
    marks += [{"t": r["ts_start"], "kind": "stall", "label": "توقف القرص %sms" % int(r["peak_lat_ms"] or 0)} for r in stalls]
    return {
        "ok": True, "now": now,
        "cpu": {"pct": cpu_pct, "cores": cpu_tot, "ncpu": ncpu, "age": (now - last) if last else None,
                "series": _thin([(r["ts"], round(100.0 * r["cores"] / (r["n"] or 1), 1)) for r in cpu_ser])},
        "ram": {"series": {"t": [p[0] for p in _thin([(r["ts"], r["ram_pct"]) for r in ram])],
                           "ram": [p[1] for p in _thin([(r["ts"], r["ram_pct"]) for r in ram])],
                           "commit": [p[1] for p in _thin([(r["ts"], r["commit_pct"]) for r in ram])]}},
        "io": {"now": io_now[0] if io_now else None,
               "series": [{"t": r["ts"], "lat": r["lat_w_avg"], "latMax": r["lat_w_max"], "busy": r["busy_avg"],
                           "mbs": round(((r["r_mb"] or 0) + (r["w_mb"] or 0)) / 60.0, 2)} for r in io_ser],
               "stalls24": stalls24},
        "gpu": ({"temp": g["temp"], "util": g["util"], "vramMB": g["vram_mb"], "vramTotal": g["vram_total"], "power": g["power"], "age": now - g["ts"],
                 "series": _thin([(r["ts"], r["temp"]) for r in gser])} if g else None),
        "top": top, "bg": bg, "marks": marks,
    }


if __name__ == "__main__":
    import os, sys, tempfile
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    if "--selftest" in sys.argv:
        import threading, types
        con = sqlite3.connect(":memory:", check_same_thread=False); con.row_factory = sqlite3.Row
        now = int(time.time())
        con.executescript("""CREATE TABLE samples(ts INT,ram_pct REAL,commit_pct REAL);CREATE TABLE cpu_top(ts INT,name TEXT,cores REAL,n INT);
        CREATE TABLE procs(ts INT,name TEXT,n INT,private_mb REAL);CREATE TABLE io_min(ts INT,dev TEXT,lat_w_avg REAL,lat_w_max REAL,busy_avg REAL,r_mb REAL,w_mb REAL);
        CREATE TABLE io_procs(ts INT,name TEXT,r_mb REAL,w_mb REAL);CREATE TABLE io_stalls(ts_start INT,ts_end INT,peak_lat_ms REAL,top_json TEXT);
        CREATE TABLE gpu(ts INT,temp REAL,util REAL,vram_mb REAL,vram_total REAL,power REAL);CREATE TABLE trims(ts INT,before_pct REAL,after_pct REAL,freed_mb REAL);
        CREATE VIEW v_io_now AS SELECT 'disk0' dev, 1.0 r_bps, 2.0 w_bps, 0.3 lat_w_ms, 5.0 busy_pct;""")
        for i in range(120):
            t = now - 3600 + i * 30
            con.execute("INSERT INTO samples VALUES(?,?,?)", (t, 60 + i % 20, 55 + i % 9))
            con.execute("INSERT INTO cpu_top VALUES(?,?,?,?)", (t, "__total__", 1.5 + (i % 7) * .3, 20))
        t = now - 5
        for nm, cr in (("MsMpEng.exe", 0.6), ("python.exe", 0.2), ("claude.exe", 0.05)):
            con.execute("INSERT INTO cpu_top VALUES(?,?,?,?)", (now, nm, cr, 20))
        con.execute("INSERT INTO cpu_top VALUES(?,?,?,?)", (now, "__total__", 2.0, 20))
        for nm, mb in (("vmmemWSL", 4100), ("claude.exe", 1900), ("MsMpEng.exe", 400)):
            con.execute("INSERT INTO procs VALUES(?,?,?,?)", (now, nm, 1, mb))
        for i in range(60):
            con.execute("INSERT INTO io_min VALUES(?,?,?,?,?,?,?)", (now - 3600 + i * 60, "disk0", .4, 2, 3, 10, 30))
        con.execute("INSERT INTO io_procs VALUES(?,?,?,?)", (now - 60, "MsMpEng.exe", 300, 50))
        con.execute("INSERT INTO io_stalls VALUES(?,?,?,?)", (now - 900, now - 880, 412, "[]"))
        con.execute("INSERT INTO gpu VALUES(?,?,?,?,?,?)", (now - 10, 55, 8, 900, 8188, 14))
        con.execute("INSERT INTO trims VALUES(?,?,?,?)", (now - 1200, 84, 61, 3600))
        db = types.SimpleNamespace(_lock=threading.RLock(), conn=lambda: con)
        p = payload(db, running={"onedrive.exe", "msmpeng.exe", "claude.exe"})
        assert p["ok"] and p["cpu"]["pct"] == 10.0, p["cpu"]
        assert p["top"][0]["name"] in ("MsMpEng", "vmmemWSL", "claude", "python"), p["top"]
        st = {b["key"]: b["state"] for b in p["bg"]}
        assert st["defender"] == "busy" and st["onedrive"] == "idle" and st["wsl"] == "off" and st["claude"] == "idle", st
        assert len(p["marks"]) == 2 and p["io"]["stalls24"] == 1 and p["gpu"]["temp"] == 55
        assert len(p["ram"]["series"]["t"]) <= 90
        print("selftest ok:", [t["name"] for t in p["top"]], st)
