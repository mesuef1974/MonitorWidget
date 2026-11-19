"""Backend for the Memory, Processes, WSL, Power, Alerts and Advice pages.
Read-only toward the system: process table via ctypes, power status via Win32, files/stat for WSL disks. No Docker CLI, no kills."""
import os
import re
import json
import time
import glob
import threading
import subprocess

import db
import secure
import health
import health_score
import errlog
import alertcfg
import gpuwatch
import powerhealth
import sleepwatch

CNW = 0x08000000
HOME = os.environ.get("USERPROFILE") or os.path.expanduser("~")
LOCAL = os.environ.get("LOCALAPPDATA") or os.path.join(HOME, "AppData", "Local")
MCDIR = os.path.join(HOME, "Documents", "Scripts", "MachineCare")
MCCFG = os.path.join(MCDIR, "machinecare.config.json")
MCSTATE = os.path.join(MCDIR, "state.json")
APPDIR = os.path.dirname(os.path.abspath(__file__))
RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400}

_lock = threading.Lock()
_cache = {}


def _cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    v = fn()
    with _lock:
        _cache[key] = (now, v)
    return v


# ------------------------------------------------------------------ process table (ctypes)
_prev_cpu = {}


def _scan():
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    ps = ctypes.windll.psapi

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_void_p), ("mod", wintypes.DWORD),
                    ("thr", wintypes.DWORD), ("ppid", wintypes.DWORD), ("pri", ctypes.c_long), ("flags", wintypes.DWORD), ("exe", ctypes.c_wchar * 260)]

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.GetProcessTimes.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    ps.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
    now = time.time()
    rows = []
    snap = k.CreateToolhelp32Snapshot(0x2, 0)
    pe = PE32()
    pe.dwSize = ctypes.sizeof(pe)
    ok = k.Process32FirstW(snap, ctypes.byref(pe))
    ft = (ctypes.c_ulonglong * 4)()
    cur_cpu = {}
    while ok:
        pid = int(pe.pid)
        r = {"pid": pid, "ppid": int(pe.ppid), "name": pe.exe, "thr": int(pe.thr), "priv": 0.0, "ws": 0.0, "cpuS": 0.0, "created": None, "cores": 0.0}
        h = k.OpenProcess(0x1000, False, pid)
        if h:
            m = PMC()
            m.cb = ctypes.sizeof(m)
            if ps.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb):
                r["priv"] = m.PrivateUsage / 1048576.0
                r["ws"] = m.WorkingSetSize / 1048576.0
            if k.GetProcessTimes(h, ctypes.byref(ft, 0), ctypes.byref(ft, 8), ctypes.byref(ft, 16), ctypes.byref(ft, 24)):
                r["created"] = ft[0] / 1e7 - 11644473600.0
                tot = (ft[2] + ft[3])
                r["cpuS"] = tot / 1e7
                key = (pid, int(ft[0]))
                cur_cpu[key] = (tot, now)
                pv = _prev_cpu.get(key)
                if pv and now > pv[1]:
                    r["cores"] = max(0.0, (tot - pv[0]) / 1e7 / (now - pv[1]))
            k.CloseHandle(h)
        rows.append(r)
        ok = k.Process32NextW(snap, ctypes.byref(pe))
    k.CloseHandle(snap)
    _prev_cpu.clear()
    _prev_cpu.update(cur_cpu)
    return rows


def procs_now():
    """Process rows, cached 4 s (the page polls every few seconds)."""
    def go():
        try:
            return _scan()
        except Exception:
            errlog.swallowed()
            return []
    return _cached("procs", 4.0, go)


def _cat(name):
    n = name.lower()
    if n == "claude.exe":
        return "claude"
    if n in ("vmmemwsl", "vmmem", "wsl.exe", "wslhost.exe", "wslservice.exe", "docker desktop.exe", "com.docker.backend.exe", "docker.exe", "dockerd.exe", "com.docker.build.exe") or n.startswith("com.docker"):
        return "wsl"
    if n in ("powershell.exe", "pwsh.exe", "cmd.exe"):
        return "shell"
    if n == "conhost.exe":
        return "conhost"
    if n in ("msedge.exe", "chrome.exe", "firefox.exe", "brave.exe", "msedgewebview2.exe"):
        return "browser"
    if n in ("node.exe", "python.exe", "pythonw.exe", "code.exe", "bash.exe", "git.exe"):
        return "dev"
    if n in ("system", "registry", "memory compression", "svchost.exe", "dwm.exe", "csrss.exe", "services.exe", "lsass.exe", "wininit.exe", "winlogon.exe", "smss.exe", "msmpeng.exe", "searchindexer.exe", "explorer.exe"):
        return "system"
    return "other"


CAT_AR = {"claude": "Claude", "wsl": "WSL و\u00a0Docker", "shell": "الشلات", "conhost": "نوافذ الطرفية (conhost)", "browser": "المتصفحات", "dev": "أدوات التطوير",
          "system": "ويندوز", "other": "أخرى"}


def _fmt_age(sec):
    if sec is None:
        return None
    return int(max(0, sec))


# ------------------------------------------------------------------ memory page
def _perfinfo():
    import ctypes
    from ctypes import wintypes

    class PI(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("CommitTotal", ctypes.c_size_t), ("CommitLimit", ctypes.c_size_t), ("CommitPeak", ctypes.c_size_t),
                    ("PhysicalTotal", ctypes.c_size_t), ("PhysicalAvailable", ctypes.c_size_t), ("SystemCache", ctypes.c_size_t), ("KernelTotal", ctypes.c_size_t),
                    ("KernelPaged", ctypes.c_size_t), ("KernelNonpaged", ctypes.c_size_t), ("PageSize", ctypes.c_size_t),
                    ("HandleCount", wintypes.DWORD), ("ProcessCount", wintypes.DWORD), ("ThreadCount", wintypes.DWORD)]
    pi = PI()
    pi.cb = ctypes.sizeof(pi)
    if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb):
        return None
    gb = float(pi.PageSize) / 1073741824.0
    return {"commitPeakGB": round(pi.CommitPeak * gb, 1), "cacheGB": round(pi.SystemCache * gb, 2), "kernelGB": round(pi.KernelTotal * gb, 2),
            "kernelPagedGB": round(pi.KernelPaged * gb, 2), "kernelNonpagedGB": round(pi.KernelNonpaged * gb, 2),
            "handles": int(pi.HandleCount), "procs": int(pi.ProcessCount), "threads": int(pi.ThreadCount)}


def _wslconfig():
    p = os.path.join(HOME, ".wslconfig")
    if not os.path.exists(p):
        return {"exists": False}
    out = {"exists": True, "values": {}}
    try:
        sec = ""
        for ln in open(p, encoding="utf-8-sig", errors="replace"):
            ln = ln.strip()
            if not ln or ln.startswith(("#", ";")):
                continue
            if ln.startswith("["):
                sec = ln.strip("[]").lower()
                continue
            if "=" in ln:
                a, b = ln.split("=", 1)
                key = (sec + "." if sec else "") + a.strip().lower()
                if re.match(r"^[a-z0-9.\-_]{1,40}$", key):
                    out["values"][key] = b.strip()[:40]
    except Exception:
        errlog.swallowed()
    return out


def mem_series(rng):
    span = RANGES.get(rng, RANGES["24h"])
    with db._lock:
        rows = db.conn().execute("SELECT ts,ram_pct,commit_pct,avail_gb FROM samples WHERE ts>=? ORDER BY ts", (int(time.time()) - span,)).fetchall()
    n = len(rows)
    k = max(1, -(-n // 240))
    out = {"t": [], "ram": [], "commit": [], "avail": []}
    for i in range(0, n, k):
        b = rows[i:i + k]
        pick = lambda j: [r[j] for r in b if r[j] is not None]
        ram, cm, av = pick(1), pick(2), pick(3)
        out["t"].append(int(sum(r[0] for r in b) / len(b)))
        out["ram"].append(round(max(ram), 1) if ram else None)
        out["commit"].append(round(max(cm), 1) if cm else None)
        out["avail"].append(round(min(av), 2) if av else None)
    return out


def mem_payload(m, extra, rng="24h"):
    rows = procs_now()
    groups = {}
    cats = {}
    for r in rows:
        key = r["name"].lower()
        g = groups.setdefault(key, {"name": r["name"], "n": 0, "priv": 0.0, "ws": 0.0, "cat": _cat(r["name"])})
        g["n"] += 1
        g["priv"] += r["priv"]
        g["ws"] += r["ws"]
    for g in groups.values():
        c = cats.setdefault(g["cat"], {"cat": g["cat"], "label": CAT_AR[g["cat"]], "n": 0, "priv": 0.0})
        c["n"] += g["n"]
        c["priv"] += g["priv"]
    wsl_gb = (extra or {}).get("wslGB")
    if wsl_gb and "wsl" not in cats:
        cats["wsl"] = {"cat": "wsl", "label": CAT_AR["wsl"], "n": 0, "priv": wsl_gb * 1024.0}
    top = sorted(groups.values(), key=lambda g: -g["priv"])[:14]
    top = [{"name": g["name"], "n": g["n"], "privMB": round(g["priv"]), "wsMB": round(g["ws"]), "cat": g["cat"]} for g in top if g["priv"] >= 30]
    catl = sorted(({"cat": c["cat"], "label": c["label"], "n": c["n"], "mb": round(c["priv"])} for c in cats.values()), key=lambda c: -c["mb"])
    try:
        perf = _perfinfo()
    except Exception:
        errlog.swallowed()
        perf = None
    leaks = []
    try:
        leaks = health.leaks()
    except Exception:
        errlog.swallowed()
    cfg = alertcfg.get()["mem"]
    return {"ok": True, "now": int(time.time()), "m": m, "perf": perf, "top": top, "cats": catl, "leaks": leaks, "wslcfg": _wslconfig(),
            "series": mem_series(rng), "range": rng if rng in RANGES else "24h", "span": RANGES.get(rng, RANGES["24h"]), "thr": cfg,
            "claudeGB": (extra or {}).get("claudeGB"), "wslGB": wsl_gb}


# ------------------------------------------------------------------ processes page
def _json_file(p):
    try:
        return json.load(open(p, encoding="utf-8-sig"))
    except Exception:
        return None


def reaper_info():
    cfg = _json_file(MCCFG) or {}
    st = _json_file(MCSTATE) or {}
    rp = cfg.get("reaper") or {}
    mode_cfg = str(rp.get("mode", "auto")).lower()
    mode = "Apply" if mode_cfg == "apply" else "DryRun" if mode_cfg == "dryrun" else None
    days = None
    if mode is None:
        try:
            import datetime
            d0 = datetime.datetime.fromisoformat(str(st.get("installDate"))[:19])
            days = (datetime.datetime.now() - d0).total_seconds() / 86400.0
            mode = "Apply" if days >= float(cfg.get("promoteAfterDays", 7)) else "DryRun"
        except Exception:
            mode = "DryRun"
    since = int(time.time()) - 86400
    with db._lock:
        c = db.conn()
        rows = c.execute("SELECT ts,severity,title,detail FROM events WHERE source='reaper' AND ts>=? ORDER BY ts DESC", (since,)).fetchall()
        last = c.execute("SELECT ts,title,detail FROM events WHERE source='reaper' ORDER BY ts DESC LIMIT 1").fetchone()
        recent = c.execute("SELECT ts,severity,title,detail FROM events WHERE source='reaper' AND title NOT LIKE '%candidates=0 killed=0' ORDER BY ts DESC LIMIT 8").fetchall()
    rx = re.compile(r"candidates=(\d+)\s+killed=(\d+)")
    killed = would = 0
    for r in rows:
        m_ = rx.search(r[2] or "")
        if m_:
            if "mode=Apply" in r[2]:
                killed += int(m_.group(2))
            else:
                would += int(m_.group(1))
    acts = list(recent)
    return {"mode": mode, "modeCfg": mode_cfg, "maxAgeMin": rp.get("maxAgeMinutes"), "maxCpuS": rp.get("maxCpuSeconds"), "bareMin": rp.get("bareClaudeShellMinutes", 240),
            "killBare": bool(rp.get("killBareClaudeShells")), "protect": rp.get("protect") or [], "killed24h": killed, "would24h": would,
            "last": ({"ts": last[0], "text": (last[2] or last[1] or "")[:160]} if last else None),
            "acts": [{"ts": a[0], "sev": a[1], "text": (a[3] or a[2] or "")[:200], "kill": bool(re.search(r"killed=[1-9]", a[2] or ""))} for a in acts]}


def procs_payload():
    rows = procs_now()
    now = time.time()
    by_pid = {r["pid"]: r for r in rows}
    kids = {}
    for r in rows:
        if r["name"].lower() != "conhost.exe":
            kids[r["ppid"]] = kids.get(r["ppid"], 0) + 1
    cats = {}
    for r in rows:
        c = _cat(r["name"])
        a = cats.setdefault(c, {"cat": c, "label": CAT_AR[c], "n": 0, "mb": 0.0, "cores": 0.0})
        a["n"] += 1
        a["mb"] += r["priv"]
        a["cores"] += r["cores"]
    rp = reaper_info()
    bare_s = float(rp.get("bareMin") or 240) * 60.0
    shells, claude = [], []
    for r in rows:
        nm = r["name"].lower()
        age = (now - r["created"]) if r["created"] else None
        par = by_pid.get(r["ppid"])
        if par and r["created"] and par["created"] and par["created"] > r["created"]:
            par = None
        if nm in ("powershell.exe", "pwsh.exe", "cmd.exe"):
            bare = bool(par and par["name"].lower() == "claude.exe" and r["pid"] not in kids and age is not None and age >= bare_s and nm != "cmd.exe")
            shells.append({"pid": r["pid"], "name": r["name"], "parent": par["name"] if par else "—", "age": _fmt_age(age), "cpuS": round(r["cpuS"], 1),
                           "mb": round(r["priv"]), "kids": kids.get(r["pid"], 0), "bare": bare, "orphan": par is None})
        elif nm == "claude.exe":
            claude.append({"pid": r["pid"], "age": _fmt_age(age), "mb": round(r["priv"]), "cores": round(r["cores"], 2), "cpuS": round(r["cpuS"], 1)})
    shells.sort(key=lambda s: -(s["age"] or 0))
    claude.sort(key=lambda s: -s["mb"])
    catl = sorted(({"cat": a["cat"], "label": a["label"], "n": a["n"], "mb": round(a["mb"]), "cores": round(a["cores"], 2)} for a in cats.values()), key=lambda a: -a["n"])
    top_cpu = sorted(({"name": r["name"], "pid": r["pid"], "cores": round(r["cores"], 2), "mb": round(r["priv"])} for r in rows if r["cores"] > 0.01), key=lambda a: -a["cores"])[:8]
    return {"ok": bool(rows), "now": int(now), "total": len(rows), "cats": catl, "shells": shells[:24], "shellTotal": len(shells), "claude": claude[:12], "claudeTotal": len(claude),
            "claudeMB": round(sum(c["mb"] for c in claude)), "conhost": sum(1 for r in rows if r["name"].lower() == "conhost.exe"),
            "bareNow": sum(1 for s in shells if s["bare"]), "orphans": sum(1 for s in shells if s["orphan"]), "topCpu": top_cpu, "reaper": rp}


# ------------------------------------------------------------------ WSL page (no docker CLI)
def _wsl_list():
    def go():
        try:
            r = subprocess.run(["wsl.exe", "-l", "-v"], capture_output=True, timeout=8, creationflags=CNW)
            txt = r.stdout.decode("utf-16-le", "ignore").replace("﻿", "")
            if "\x00" in txt or not txt.strip():
                txt = r.stdout.decode("utf-8", "ignore").replace("\x00", "")
        except Exception:
            return None
        out = []
        for ln in txt.splitlines()[1:]:
            ln = ln.rstrip()
            if not ln.strip():
                continue
            default = ln.lstrip().startswith("*")
            parts = ln.replace("*", " ", 1).split()
            if len(parts) >= 3 and parts[-1].isdigit():
                out.append({"name": " ".join(parts[:-2]), "state": parts[-2], "ver": int(parts[-1]), "default": default})
        return out
    return _cached("wsl_list", 30.0, go)


def _vhdx():
    def go():
        pats = [os.path.join(LOCAL, "Docker", "wsl", "**", "*.vhdx"), os.path.join(LOCAL, "Packages", "*", "LocalState", "*.vhdx"),
                os.path.join(LOCAL, "wsl", "*", "*.vhdx")]
        seen, out = set(), []
        for p in pats:
            try:
                for f in glob.glob(p, recursive=True):
                    if f in seen:
                        continue
                    seen.add(f)
                    st = os.stat(f)
                    rel = f.replace(LOCAL, "%LOCALAPPDATA%")
                    out.append({"path": rel, "gb": round(st.st_size / 1073741824.0, 1), "mtime": int(st.st_mtime)})
            except Exception:
                errlog.swallowed()
        return sorted(out, key=lambda x: -x["gb"])
    return _cached("vhdx", 120.0, go)


def wsl_payload(rng="6h"):
    rows = procs_now()
    names = ("vmmemwsl", "vmmem", "wsl.exe", "wslhost.exe", "wslservice.exe", "docker desktop.exe", "com.docker.backend.exe", "dockerd.exe")
    procs = {}
    for r in rows:
        n = r["name"].lower()
        if n in names or n.startswith("com.docker"):
            g = procs.setdefault(r["name"], {"name": r["name"], "n": 0, "mb": 0.0, "cores": 0.0})
            g["n"] += 1
            g["mb"] += r["priv"]
            g["cores"] += r["cores"]
    pl = sorted(({"name": g["name"], "n": g["n"], "mb": round(g["mb"]), "cores": round(g["cores"], 2)} for g in procs.values()), key=lambda g: -g["cores"])
    span = RANGES.get(rng, 6 * 3600)
    with db._lock:
        c = db.conn()
        q = c.execute("SELECT ts,SUM(cores) FROM cpu_top WHERE lower(name) IN ('vmmemwsl','vmmem') AND ts>=? GROUP BY ts ORDER BY ts", (int(time.time()) - span,)).fetchall()
        tot = c.execute("SELECT ts,cores FROM cpu_top WHERE name='__total__' AND ts>=? ORDER BY ts", (int(time.time()) - span,)).fetchall()
    n = len(q)
    k = max(1, -(-n // 240))
    ser = {"t": [], "v": []}
    for i in range(0, n, k):
        b = q[i:i + k]
        ser["t"].append(int(sum(x[0] for x in b) / len(b)))
        ser["v"].append(round(sum(x[1] for x in b) / len(b), 2))
    vals = [x[1] for x in q]
    avg = round(sum(vals) / len(vals), 2) if vals else None
    mx = round(max(vals), 2) if vals else None
    share = None
    if vals and tot:
        t_sum = sum(x[1] for x in tot)
        share = round(100.0 * sum(vals) / t_sum, 1) if t_sum > 0 else None
    cfg = _wslconfig()
    notes = []
    add = lambda lvl, title, text: notes.append({"lvl": lvl, "title": title, "text": text})
    if avg is not None and avg >= 1.0:
        add("warn", "WSL يستهلك المعالج بشكل متواصل", "متوسط %.1f نواة خلال الفترة. أسباب شائعة: حاوية تشغّل خادم تطوير بإعادة تحميل تلقائي (مثل runserver)، أو فهرسة ملفات عبر مجلد مشترك بين ويندوز وWSL، أو عملية عالقة داخل التوزيعة." % avg)
    elif avg is not None:
        add("ok", "استهلاك WSL معتدل", "متوسط %.2f نواة خلال الفترة." % avg)
    if not cfg.get("exists") or not any(k.endswith("memory") for k in cfg.get("values", {})):
        add("info", "لا سقف لذاكرة WSL", "ملف .wslconfig لا يحدّد سقفاً للذاكرة، فيأخذ WSL حتى نصف ذاكرة الجهاز افتراضياً. وضع سقف (memory) و(processors) يمنع التهامه للجهاز. لن أغيّر شيئاً قبل إذنك.")
    big = [v for v in _vhdx() if v["gb"] >= 20]
    if big:
        add("info", "قرص افتراضي كبير", "أكبر قرص WSL افتراضي %.0f GB؛ وهو لا يتقلص تلقائياً بعد حذف الملفات داخله، ويمكن ضغطه لاحقاً (Optimize-VHD) بعد أخذ نسخة." % big[0]["gb"])
    add("info", "تفاصيل الحاويات مؤجلة", "لا يقرأ التطبيق حالة الحاويات (docker) الآن بحسب طلبك لأن Claude Code يعمل عليها. الصفحة تعرض ما يُقرأ من ويندوز فقط.")
    return {"ok": True, "now": int(time.time()), "procs": pl, "series": ser, "avg": avg, "max": mx, "share": share, "range": rng if rng in RANGES else "6h", "span": span,
            "distros": _wsl_list(), "wslcfg": cfg, "vhdx": _vhdx(), "notes": notes}


# ------------------------------------------------------------------ power / thermal page
def _power_status():
    import ctypes
    from ctypes import wintypes

    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]
    s = SPS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return None
    return {"ac": {0: False, 1: True}.get(s.ACLineStatus), "pct": None if s.BatteryLifePercent == 255 else int(s.BatteryLifePercent),
            "charging": bool(s.BatteryFlag & 8) if s.BatteryFlag != 255 else None, "noBattery": bool(s.BatteryFlag & 128),
            "secLeft": None if s.BatteryLifeTime == 0xFFFFFFFF else int(s.BatteryLifeTime), "saver": bool(s.SystemStatusFlag & 1)}


def _cpu_mhz():
    """Per-core current/max/limit MHz from CallNtPowerInformation(ProcessorInformation); the OS's own view of frequency scaling."""
    import ctypes
    from ctypes import wintypes

    class PPI(ctypes.Structure):
        _fields_ = [("Number", wintypes.ULONG), ("MaxMhz", wintypes.ULONG), ("CurrentMhz", wintypes.ULONG), ("MhzLimit", wintypes.ULONG),
                    ("MaxIdleState", wintypes.ULONG), ("CurrentIdleState", wintypes.ULONG)]
    n = os.cpu_count() or 1
    arr = (PPI * n)()
    r = ctypes.windll.powrprof.CallNtPowerInformation(11, None, 0, ctypes.byref(arr), ctypes.sizeof(arr))
    if r != 0:
        return None
    cur = [a.CurrentMhz for a in arr]
    mx = [a.MaxMhz for a in arr]
    lim = [a.MhzLimit for a in arr]
    return {"cur": round(sum(cur) / n), "curMax": max(cur), "max": round(sum(mx) / n), "limit": round(sum(lim) / n), "cores": n}


def power_plans():
    """Installed power plans from powercfg /list (cached 10 s): [{guid,name,active}]."""
    def go():
        try:
            r = subprocess.run(["powercfg", "/list"], capture_output=True, timeout=8, creationflags=CNW)
            txt = r.stdout.decode("utf-8", "replace")
        except Exception:
            errlog.swallowed()
            return []
        out = []
        for m in re.finditer(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s+\((.*?)\)(\s*\*)?", txt):
            out.append({"guid": m.group(1).lower(), "name": m.group(2), "active": bool(m.group(3))})
        return out
    return _cached("plans", 10.0, go)


def power_set(guid):
    guid = str(guid or "").lower()
    if guid not in {p["guid"] for p in power_plans()}:
        return None, "unknown plan"
    r = subprocess.run(["powercfg", "/setactive", guid], capture_output=True, timeout=10, creationflags=CNW)
    with _lock:
        _cache.pop("plans", None)
    if r.returncode != 0:
        return None, "powercfg failed"
    try:
        with db._lock:
            c = db.conn()
            c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)", (int(time.time()), "powerplan", "page", 1, guid))
            c.commit()
    except Exception:
        errlog.swallowed()
    return power_plans(), None


def _uptime_s():
    import ctypes
    f = ctypes.windll.kernel32.GetTickCount64
    f.restype = ctypes.c_ulonglong
    return int(f() / 1000)


def _power_table():
    with db._lock:
        c = db.conn()
        c.execute("CREATE TABLE IF NOT EXISTS power_samples(ts INTEGER PRIMARY KEY, ac INTEGER, batt INTEGER, mhz INTEGER, mhz_max INTEGER, mhz_limit INTEGER)")
        c.commit()


def power_job():
    """Agent job (every minute): one row of power state and CPU frequency."""
    _power_table()
    ps = None
    mh = None
    try:
        ps = _power_status()
    except Exception:
        errlog.swallowed()
    try:
        mh = _cpu_mhz()
    except Exception:
        errlog.swallowed()
    if not ps and not mh:
        return "no reading"
    now = int(time.time())
    with db._lock:
        c = db.conn()
        c.execute("INSERT OR REPLACE INTO power_samples VALUES(?,?,?,?,?,?)",
                  (now, None if not ps or ps["ac"] is None else int(ps["ac"]), None if not ps else ps["pct"], None if not mh else mh["cur"],
                   None if not mh else mh["max"], None if not mh else mh["limit"]))
        c.commit()
    return "ac=%s mhz=%s" % (ps and ps["ac"], mh and mh["cur"])


def power_payload(rng="24h"):
    _power_table()
    span = RANGES.get(rng, RANGES["24h"])
    with db._lock:
        rows = db.conn().execute("SELECT ts,ac,batt,mhz,mhz_max,mhz_limit FROM power_samples WHERE ts>=? ORDER BY ts", (int(time.time()) - span,)).fetchall()
    n = len(rows)
    k = max(1, -(-n // 240))
    ser = {"t": [], "mhz": [], "batt": [], "ac": []}
    for i in range(0, n, k):
        b = rows[i:i + k]
        mz = [r[3] for r in b if r[3] is not None]
        bt = [r[2] for r in b if r[2] is not None]
        ser["t"].append(int(sum(r[0] for r in b) / len(b)))
        ser["mhz"].append(round(max(mz)) if mz else None)
        ser["batt"].append(round(min(bt)) if bt else None)
        ser["ac"].append(b[-1][1])
    try:
        ps = _power_status()
    except Exception:
        errlog.swallowed()
        ps = None
    try:
        mh = _cpu_mhz()
    except Exception:
        errlog.swallowed()
        mh = None
    try:
        up = _uptime_s()
    except Exception:
        up = None
    ts, hw = health.get_hw("probe")
    hw = hw or {}
    gl = None
    try:
        gl = gpuwatch.live_info()
    except Exception:
        errlog.swallowed()
    limited = None
    if mh and mh["max"]:
        limited = mh["limit"] < 0.95 * mh["max"]
    notes = []
    add = lambda lvl, title, text: notes.append({"lvl": lvl, "title": title, "text": text})
    if limited:
        add("warn", "النظام يحدّ من تردد المعالج", "الحدّ المسموح %d MHz والأقصى %d MHz؛ قد يكون بسبب خطة الطاقة أو الحرارة أو البطارية." % (mh["limit"], mh["max"]))
    if ps and ps.get("ac") is False and not ps.get("noBattery"):
        add("info", "الجهاز على البطارية", "الأداء قد يُخفَّض لتوفير الطاقة. وصّل الشاحن للأعمال الثقيلة.")
    bt = hw.get("battery") if isinstance(hw, dict) else None
    if bt and bt.get("healthPct") is not None and bt["healthPct"] < 70:
        add("warn", "صحة البطارية منخفضة", "السعة الحالية %s%% من الأصلية؛ فكّر في استبدالها إن كان الجهاز يُفصل عن الشاحن كثيراً." % bt["healthPct"])
    if gl and gl.get("temp") is not None and gl["temp"] >= alertcfg.get()["gpu"]["tempWarn"]:
        add("warn", "حرارة كرت الشاشة مرتفعة", "الحرارة الآن %d°." % gl["temp"])
    if up and up > 14 * 86400:
        add("info", "الجهاز يعمل منذ أيام طويلة", "إعادة التشغيل تصفّر تسرّب الذاكرة وتُكمل التحديثات.")
    crit = {"ok": False}
    shut = {"days": powerhealth.DAYS, "items": [], "counts": {}}
    if not (ps and ps.get("noBattery")):
        try:
            crit = _cached("battpolicy", 60, powerhealth.policy)
        except Exception:
            errlog.swallowed()
        try:
            shut = powerhealth.shutdowns()
        except Exception:
            errlog.swallowed()
    if crit.get("risk"):
        add("bad", "إجراء البطارية الحرجة لا ينفع", crit["riskText"] + " غيّره إلى «إيقاف تشغيل» من خيارات الطاقة.")
    if shut["counts"].get("battery"):
        add("warn", "إيقافات مفاجئة بسبب فراغ البطارية", "%d في آخر %d يومًا: الجهاز انطفأ والبطارية فارغة. وصّل الشاحن قبل %s%%." % (
            shut["counts"]["battery"], shut["days"], crit.get("lowLevel") or 10))
    slp = None
    try:
        slp = _cached("sleepwatch", 300, sleepwatch.payload)   # قراءة سجل النظام مرة كل 5 دقائق
        for lvl, title, text in sleepwatch.notes(slp):
            add(lvl, title, text)
    except Exception:
        errlog.swallowed()
    add("info", "حرارة المعالج غير متاحة", "ويندوز لا يعطي حرارة المعالج دون صلاحية إدارية أو أداة مخصصة؛ نعرض تردده الفعلي كمؤشر على التقييد، وحرارة كرت الشاشة من برنامج التعريف.")
    return {"ok": True, "now": int(time.time()), "status": ps, "mhz": mh, "limited": limited, "uptimeS": up, "plan": hw.get("powerPlan"), "pendingReboot": hw.get("pendingReboot"),
            "battery": bt, "gpu": gl, "plans": power_plans(), "series": ser, "range": rng if rng in RANGES else "24h", "span": span, "notes": notes,
            "critical": crit, "shutdowns": shut, "sleep": slp}


# ------------------------------------------------------------------ alerts page
# مصادر التنبيهات في db.add_event؛ تسمياتها العربية في SRC بالواجهة (web/index.html)
ALERT_SOURCES = ("cpu", "health", "guard", "fix", "commit", "disk", "boot", "localai", "net")


def alerts_payload():
    with db._lock:
        c = db.conn()
        rows = c.execute("SELECT ts,source,severity,title,detail FROM events WHERE source IN (%s) AND severity IN ('warn','error','crit','bad') "
                         "ORDER BY ts DESC LIMIT 40" % ",".join("?" * len(ALERT_SOURCES)), ALERT_SOURCES).fetchall()
        if not rows:
            rows = c.execute("SELECT ts,source,severity,title,detail FROM events WHERE severity!='info' ORDER BY ts DESC LIMIT 40").fetchall()
    return {"cfg": alertcfg.get(), "defaults": alertcfg.DEFAULTS, "limits": alertcfg.LIMITS,
            "log": [{"ts": r[0], "source": r[1], "sev": r[2], "title": r[3], "detail": (r[4] or "")[:240]} for r in rows]}


def alerts_set(body):
    cfg, err = alertcfg.validate(body)
    if err:
        return None, err
    return alertcfg.save(cfg), None


# ------------------------------------------------------------------ advice page
_obk = {"t": 0.0, "k": None, "v": None, "busy": False}


def _ar_folders(n):
    """مطابقة العدد: «مجلد واحد» و«مجلدان» و«3 مجلدات» و«11 مجلدًا»."""
    if n == 1:
        return "مجلد واحد"
    if n == 2:
        return "مجلدان"
    return "%d %s" % (n, "مجلدات" if 3 <= n % 100 <= 10 else "مجلدًا")


OLDBAK_MIN_GB = 0.1  # نسخ تنظيف قديمة أصغر من نحو 100 MB لا تُعرض توصية: حذفها لا يغيّر شيئًا


class _Skip(Exception):
    """يتخطّى بطاقة توصية من داخل كتلتها دون تسجيله خطأً."""


def _gb_text(gb):
    """حجم مقروء: أقل من جيجابايت يُعرض بالميجابايت بدل «0.0 GB»."""
    return "%.1f GB" % gb if gb >= 1 else "%.0f MB" % (gb * 1024)


def _oldbak_gb(paths, ttl=900):
    """حجم نسخ التنظيف القديمة بالجيجابايت، يُحسب في خيط خلفي ويُخزَّن 15 دقيقة (None حتى أول حساب): المشي على آلاف الملفات عند كل فتح لصفحة التوصيات كان يكلّف ثانيتين."""
    k = tuple(paths)
    if (_obk["k"] != k or time.time() - _obk["t"] > ttl) and not _obk["busy"]:
        _obk["busy"] = True

        def work():
            try:
                _obk.update(v=sum(_folder_size_gb(p) for p in k), k=k, t=time.time())
            finally:
                _obk["busy"] = False
        threading.Thread(target=work, daemon=True).start()
    return _obk["v"] if _obk["k"] == k else None


def _folder_size_gb(p):
    tot = 0
    for dp, dn, fn in os.walk(p):
        for f in fn:
            try:
                tot += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return tot / 1073741824.0


def advice_payload(m, extra, maint, trim):
    items = []
    cfg = alertcfg.get()
    add = lambda iid, lvl, title, text, action=None, go=None: items.append({"id": iid, "lvl": lvl, "title": title, "text": text, "action": action, "go": go})
    ram, commit = (m or {}).get("ramPct"), (m or {}).get("commitPct")
    mc = cfg["mem"]
    if ram is not None and ram >= mc["ramBad"]:
        add("ram", "bad", "الذاكرة ممتلئة تقريباً (%d%%)" % ram, "المتاح %.1f GB فقط. اضغط «تنظيف الذاكرة» لنقل الصفحات غير المستخدمة، ثم راجع أكبر المستهلكين." % (m.get("availGB") or 0),
            {"label": "تنظيف الذاكرة الآن", "api": "/api/trim-memory"}, "mem")
    elif ram is not None and ram >= mc["ramWarn"]:
        add("ram", "warn", "الذاكرة مرتفعة (%d%%)" % ram, "ما زال هناك هامش، لكن راقب أكبر المستهلكين.", None, "mem")
    if commit is not None and commit >= mc["commitBad"]:
        add("commit", "bad", "الالتزام قريب من الحدّ (%d%%)" % commit, "عند بلوغ الحد تفشل التطبيقات في طلب ذاكرة جديدة. أغلق ما لا تحتاجه أو زد حجم ملف الصفحات.", None, "mem")
    elif commit is not None and commit >= mc["commitWarn"]:
        add("commit", "warn", "الالتزام مرتفع (%d%%)" % commit, "هامش الالتزام يتقلص؛ راجع التسرّب وعمليات Claude وWSL.", None, "mem")
    if trim is not None and not trim.get("auto") and ram is not None and ram >= mc["ramWarn"]:
        add("autotrim", "info", "التنظيف التلقائي للذاكرة متوقف", "تفعيله ينقل الصفحات غير المستخدمة دورياً ويُبقي الذاكرة أخف.",
            {"label": "تفعيل التنظيف التلقائي", "api": "/api/trim-auto", "body": {"on": True}}, "mem")
    wsl = (extra or {}).get("wslGB")
    if wsl and wsl >= 3:
        add("wsl", "warn", "WSL يحجز %.1f GB من الذاكرة" % wsl, "يحجز WSL الذاكرة ولا يُرجعها بسرعة. سقف الذاكرة في .wslconfig يحل ذلك بعد أن يفرغ Claude Code من عمله على Docker.", None, "wsl")
    cl = (extra or {}).get("claudeGB")
    ncl = (m or {}).get("claude", 0)
    if cl and cl >= 3:  # بطاقة واحدة: العدد يُضمّ إلى بطاقة الحجم بدل بطاقة ثانية بالنصيحة نفسها
        add("claude", "warn", "عمليات Claude تستهلك %.1f GB" % cl + (" (%d عملية)" % ncl if ncl >= 20 else ""),
            "كثرة نوافذ وجلسات Claude تتراكم ذاكرتها، وكل نافذة/جلسة تضيف عمليات فرعية. أغلق الجلسات الخاملة.", None, "procs")
    elif ncl >= 20:
        add("claude_n", "info", "%d عملية Claude" % m["claude"], "عدد كبير؛ كل نافذة/جلسة تضيف عمليات فرعية.", None, "procs")
    free = (m or {}).get("diskFreeGB")
    dc = cfg["disk"]
    if free is not None and free < dc["freeBadGB"]:
        add("disk", "bad", "مساحة C قليلة جداً (%d GB)" % free, "قد يتوقف ويندوز عن التحديث وتتباطأ الذاكرة الافتراضية. شغّل التنظيف أو انقل الملفات الكبيرة.", None, "space")
    elif free is not None and free < dc["freeWarnGB"]:
        add("disk", "warn", "مساحة C تنخفض (%d GB)" % free, "ما زالت مقبولة، لكن راجع صفحة المساحة.", None, "space")
    rp = None
    try:
        rp = reaper_info()
    except Exception:
        errlog.swallowed()
    if rp and rp["mode"] == "DryRun":
        add("reaper", "info", "كانس الشلات في وضع التجربة", "يسجّل ما كان سينهيه (%d في آخر 24 ساعة) دون إنهاء." % rp["would24h"], None, "procs")
    if rp and rp["killed24h"]:
        add("reaper_k", "ok", "الكانس أنهى %d عملية خاملة خلال 24 ساعة" % rp["killed24h"], "تفاصيل ما أُنهي في صفحة العمليات.", None, "procs")
    if maint and maint.get("on"):
        since = maint.get("since")
        days = (time.time() - since) / 86400.0 if since else 0
        add("maint", "warn" if days >= 3 else "info", "وضع الصيانة مفعّل منذ %.0f يوماً" % days, "قواعد Docker/Claude متوقفة. أطفئه عندما ينتهي Claude Code.",
            {"label": "إيقاف وضع الصيانة", "api": "/api/maintenance", "body": {"on": False}, "confirm": "إيقاف وضع الصيانة؟ ستعود قواعد تنظيف Docker وClaude للعمل في مواعيدها."}, "ctrl")
    try:
        # القياس القديم لا يُبنى عليه: إعادة تشغيل أو تحديث من مسبار قبل أيام قد يكون انتهى؛ الحدود نفسها التي تحكم درجة الصحة
        now = time.time()
        hw_ts, hw = health.get_hw("probe")
        up_ts, up = health.get_hw("updates")
        hw = (hw or {}) if health_score.number(hw_ts) and -60 <= now - hw_ts <= health_score.PROBE_MAX_AGE else {}
        up = up if health_score.number(up_ts) and -60 <= now - up_ts <= health_score.UPDATES_MAX_AGE else None
    except Exception:
        hw, up = {}, None
    if hw.get("pendingReboot"):
        add("reboot", "warn", "إعادة تشغيل معلّقة", "ويندوز ينتظر إعادة التشغيل لإكمال تحديث.", None, None)
    if up and up.get("n"):
        add("updates", "info", "%d تحديث معلّق" % up["n"], "ثبّتها في نافذة تحديثات ويندوز.", None, None)
    try:
        g = gpuwatch.api_payload("24h")
        for n in g.get("notes", []):
            if n["lvl"] in ("bad", "warn"):
                add("gpu_" + n["title"][:10], n["lvl"], "كرت الشاشة: " + n["title"], n["text"], None, "gpu")
    except Exception:
        errlog.swallowed()
    try:
        logs = sorted(glob.glob(os.path.join(APPDIR, "cleanup_apps", "backup_d_*.log")))
        if logs:
            tail = open(logs[-1], encoding="utf-8-sig", errors="replace").read()[-600:]
            if "ABORT" in tail:
                add("backup", "info", "لا نسخة احتياطية خارجية للقرص D","آخر محاولة توقفت لعدم وجود قرص خارجي. وصّل قرصاً ثم شغّل backup_d_external.ps1.", None, None)
    except Exception:
        errlog.swallowed()
    try:
        import datetime
        if datetime.date.today() >= datetime.date(2026, 10, 25):
            olds = [p for p in glob.glob(os.path.join(APPDIR, "cleanup_apps", "*_backup_*")) if os.path.isdir(p)]
            if olds:
                sz = _oldbak_gb(olds[:8])
                if sz is not None and len(olds) <= 8 and sz < OLDBAK_MIN_GB:
                    raise _Skip  # حجم مُقاس كامل لا يستحق بطاقة (حالة 18/11: 4 MB)
                add("oldbak", "info", "نسخ تنظيف احتياطية قديمة (%s)" % _ar_folders(len(olds)), "مرّ موعد مراجعتها (25 أكتوبر)%s. احذفها إن لم تعد تحتاجها." % (" وحجمها نحو " + _gb_text(sz) if sz is not None else ""), None, None)
    except _Skip:
        pass
    except Exception:
        errlog.swallowed()
    order = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
    items.sort(key=lambda x: order.get(x["lvl"], 9))
    if not any(i["lvl"] in ("bad", "warn") for i in items):
        items.insert(0, {"id": "fine", "lvl": "ok", "title": "لا شيء عاجل", "text": "كل المؤشرات ضمن الحدود التي حددتها في صفحة التنبيهات.", "action": None, "go": None})
    return {"ok": True, "now": int(time.time()), "items": items, "counts": {k: sum(1 for i in items if i["lvl"] == k) for k in ("bad", "warn", "info", "ok")}}


_run_lock = threading.Lock()


def reaper_run(dry, mcdir):
    """Runs MachineCare's own Invoke-OrphanReaper.ps1 once (-DryRun previews, -Apply acts) and returns its Reaper log lines."""
    if not _run_lock.acquire(blocking=False):
        return {"ok": False, "error": "already running"}
    try:
        import ctypes
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        # A hash check followed by execution of a user-writable script is still
        # racy. Elevated execution requires a reviewed protected bundle; do not
        # copy a script alone if its config/module dependencies were not audited.
        script = os.path.join(secure.BIN if elevated else mcdir, "Invoke-OrphanReaper.ps1")
        if elevated and not os.path.isfile(script):
            return {"ok": False, "error": "تشغيل الكانس بصلاحية مسؤول يتطلب حزمة محمية ومراجعة؛ لن تُشغّل نسخة مجلد المستخدم بهذه الصلاحية"}
        if not os.path.exists(script):
            return {"ok": False, "error": "script missing"}
        pin, pin_detail = secure.reaper_pin_state(script)
        if pin == "pinned-mismatch":
            return {"ok": False, "error": "script changed since it was pinned"}
        logp = os.path.join(mcdir, "logs", "machinecare.log")
        pos = os.path.getsize(logp) if os.path.exists(logp) else 0
        r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "RemoteSigned", "-File", script, "-DryRun" if dry else "-Apply"],
                           capture_output=True, timeout=90, creationflags=CNW)
        lines = []
        try:
            with open(logp, "rb") as f:
                f.seek(pos)
                lines = [x for x in f.read().decode("utf-8", "replace").splitlines() if "Reaper" in x or "reaper" in x]
        except OSError:
            pass
        cand = killed = 0
        for x in lines:
            m_ = re.search(r"candidates=(\d+)\s+killed=(\d+)", x)
            if m_:
                cand, killed = int(m_.group(1)), int(m_.group(2))
        with db._lock:
            c = db.conn()
            c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)", (int(time.time()), "reaper-run", "page", 1 if r.returncode == 0 else 0, "dry=%s cand=%d killed=%d pin=%s" % (dry, cand, killed, pin)))
            c.commit()
        return {"ok": r.returncode == 0, "dry": dry, "candidates": cand, "killed": killed, "lines": [x[-220:] for x in lines[-8:]]}
    except Exception:
        errlog.swallowed()
        return {"ok": False, "error": "run failed"}
    finally:
        _run_lock.release()
