import re
import errlog
"""Health layer on top of db.py: process snapshots (leak detection), disk-fill forecast, GPU samples, hardware/security probe,
and a transparent 0-100 health score. Pure stdlib. Everything here is explainable: every point lost has a reason string."""
import json, os, statistics, subprocess, threading, time
from datetime import datetime

import db

ROOT = os.path.dirname(os.path.abspath(__file__))
PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"
CNW = 0x08000000
NVSMI = r"C:\Windows\System32\nvidia-smi.exe"

SCHEMA = """
CREATE TABLE IF NOT EXISTS procs(ts INTEGER NOT NULL, name TEXT NOT NULL, n INTEGER, private_mb REAL, ws_mb REAL, PRIMARY KEY(ts,name)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_procs_name ON procs(name, ts);
CREATE TABLE IF NOT EXISTS gpu(ts INTEGER PRIMARY KEY, temp REAL, util REAL, vram_mb REAL, vram_total REAL, power REAL, pstate TEXT, throttle TEXT);
CREATE TABLE IF NOT EXISTS gpu_procs(ts INTEGER NOT NULL, name TEXT NOT NULL, vram_mb REAL, PRIMARY KEY(ts,name)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS igpu(ts INTEGER PRIMARY KEY, util3d REAL, video REAL, shared_mb REAL, ded_mb REAL, procs TEXT);
CREATE TABLE IF NOT EXISTS hw(key TEXT PRIMARY KEY, ts INTEGER, json TEXT);
CREATE TABLE IF NOT EXISTS score_daily(day TEXT PRIMARY KEY, ts INTEGER, score REAL, parts TEXT);
CREATE TABLE IF NOT EXISTS recs(id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, kind TEXT NOT NULL, title TEXT, detail TEXT, expect_mb REAL, status TEXT DEFAULT 'open', actual_mb REAL, done_ts INTEGER);
"""
_inited = False


def init():
    global _inited
    if not _inited:
        with db._lock:
            db.conn().executescript(SCHEMA)
            db.conn().commit()
        _inited = True
        start_updates_worker()  # مرة واحدة لكل عملية


def _c():
    init()
    return db.conn()


# ------------------------------------------------------------------ writers
def add_procs(ts, rows):
    with db._lock:
        c = _c()
        c.executemany("INSERT OR REPLACE INTO procs VALUES(?,?,?,?,?)", [(int(ts), r[0], r[1], r[2], r[3]) for r in rows])
        c.commit()


def add_gpu(ts, g, procs):
    with db._lock:
        c = _c()
        c.execute("INSERT OR REPLACE INTO gpu VALUES(?,?,?,?,?,?,?,?)", (int(ts), g.get("temp"), g.get("util"), g.get("vram"), g.get("vramTotal"), g.get("power"), g.get("pstate"), g.get("throttle")))
        c.executemany("INSERT OR REPLACE INTO gpu_procs VALUES(?,?,?)", [(int(ts), n, v) for n, v in procs])
        c.commit()


def set_hw(key, obj):
    with db._lock:
        c = _c(); c.execute("INSERT OR REPLACE INTO hw VALUES(?,?,?)", (key, int(time.time()), json.dumps(obj, ensure_ascii=False))); c.commit()


def get_hw(key):
    with db._lock:
        r = _c().execute("SELECT ts,json FROM hw WHERE key=?", (key,)).fetchone()
    return (r["ts"], json.loads(r["json"])) if r else (0, None)


# ------------------------------------------------------------------ collectors
def read_gpu():
    """nvidia-smi one-shot. Returns (summary dict, [(process name, vram MB)]) or (None, [])."""
    if not os.path.exists(NVSMI):
        return None, []
    try:
        r = subprocess.run([NVSMI, "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,pstate,clocks_throttle_reasons.active",
                            "--format=csv,noheader,nounits"], capture_output=True, timeout=20, creationflags=CNW)
        f = [x.strip() for x in r.stdout.decode("utf-8", "replace").strip().split(",")]
        num = lambda v: float(v) if v.replace(".", "", 1).isdigit() else None
        g = {"temp": num(f[0]), "util": num(f[1]), "vram": num(f[2]), "vramTotal": num(f[3]), "power": num(f[4]), "pstate": f[5], "throttle": f[6]}
    except Exception:
        return None, []
    procs = []
    try:  # per-process dedicated GPU memory from the Windows counters (nvidia-smi shows N/A under WDDM)
        ps = ("(Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage' -ErrorAction Stop).CounterSamples | Where-Object CookedValue -gt 20MB | "
              "ForEach-Object { if($_.InstanceName -match 'pid_(\\d+)'){ $p=Get-Process -Id $Matches[1] -ErrorAction SilentlyContinue; if($p){ '{0}|{1}' -f $p.ProcessName,[int]($_.CookedValue/1MB) } } }")
        r = subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], capture_output=True, timeout=40, creationflags=CNW)
        agg = {}
        for ln in r.stdout.decode("utf-8", "replace").splitlines():
            if "|" in ln:
                n, v = ln.strip().split("|"); agg[n] = agg.get(n, 0) + int(v)
        cap = (g.get("vram") or 0) * 1.05  # counters can over-report shared/reserved memory: drop impossible values and system processes
        procs = sorted(((n, v) for n, v in agg.items() if v <= cap and n.lower() not in ("dwm", "csrss", "system")), key=lambda x: -x[1])[:8]
    except Exception:
        errlog.swallowed()
    return g, procs


IGPU_PS = r"""
$e=(Get-Counter '\GPU Engine(*)\Utilization Percentage' -ErrorAction SilentlyContinue).CounterSamples
$m=(Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage','\GPU Adapter Memory(*)\Shared Usage' -ErrorAction SilentlyContinue).CounterSamples
$o=@{}
foreach($s in $e){ if($s.InstanceName -match 'pid_(\d+)_luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+_eng_\d+_engtype_(\w+)'){ $l=$Matches[2]; $t=$Matches[3]; if(-not $o[$l]){$o[$l]=@{eng=@{};procs=@{};ded=0;sh=0}}; $o[$l].eng[$t]=[double]$o[$l].eng[$t]+$s.CookedValue
  if($s.CookedValue -gt 0.5 -and $t -in '3D','VideoDecode','VideoEncode','VideoProcessing'){ $p=Get-Process -Id $Matches[1] -ErrorAction SilentlyContinue; if($p){ $o[$l].procs[$p.ProcessName]=[double]$o[$l].procs[$p.ProcessName]+$s.CookedValue } } } }
foreach($s in $m){ if($s.Path -match '(0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+\)\\(dedicated|shared) usage'){ $l=$Matches[1]; if(-not $o[$l]){$o[$l]=@{eng=@{};procs=@{};ded=0;sh=0}}; if($Matches[2] -eq 'dedicated'){$o[$l].ded=[int]($s.CookedValue/1MB)}else{$o[$l].sh=[int]($s.CookedValue/1MB)} } }
$o | ConvertTo-Json -Depth 6 -Compress
"""


TOPO_PS = r"""
$all=Get-PnpDevice -Class Display -ErrorAction SilentlyContinue
$o=@{ devices=@($all | ForEach-Object { @{ name=$_.FriendlyName; present=[bool]$_.Present; problem=[string]$_.Problem } }); prefs=@() }
$k='HKCU:\Software\Microsoft\DirectX\UserGpuPreferences'
if(Test-Path $k){ $p=Get-ItemProperty $k; foreach($n in $p.PSObject.Properties){ if($n.Name -notmatch '^PS' -and "$($n.Value)" -match 'GpuPreference=(\d)'){ $o.prefs+=@{ app=$n.Name; pref=[int]$Matches[1] } } } }
$o | ConvertTo-Json -Depth 5 -Compress
"""


def igpu_topology():
    """Real integrated GPU present? Read from the DirectX adapter registry (instant; the software 'Basic Render Driver' is ignored)."""
    import winreg
    names = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\DirectX") as k:
            for i in range(winreg.QueryInfoKey(k)[0]):
                try:
                    with winreg.OpenKey(k, winreg.EnumKey(k, i)) as s:
                        names.append(str(winreg.QueryValueEx(s, "Description")[0]))
                except OSError:
                    errlog.swallowed()
    except OSError:
        return None
    integ = [n for n in names if re.search(r"Intel|AMD|Radeon", n, re.I)]
    hi = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\DirectX\UserGpuPreferences") as k:
            for i in range(winreg.QueryInfoKey(k)[1]):
                n, v, _ = winreg.EnumValue(k, i)
                if "GpuPreference=2" in str(v):
                    hi.append(os.path.basename(n))
    except OSError:
        errlog.swallowed()
    return {"installed": integ, "present": bool(integ), "forcedDiscrete": hi, "ts": int(time.time())}


_topo_cache = {"t": 0, "v": None}


def igpu_topology_cached():
    if time.time() - _topo_cache["t"] > 1800 or _topo_cache["v"] is None:
        _topo_cache["v"] = igpu_topology(); _topo_cache["t"] = time.time()
    return _topo_cache["v"]


def _ram_mb():
    import ctypes

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS(); m.dwLength = ctypes.sizeof(m)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return int(m.ullTotalPhys / 1048576)


def read_igpu(nv_vram_mb):
    """Fast path (typeperf, ~2 s). Falls back to the PowerShell reader if anything is unexpected."""
    topo = igpu_topology_cached()
    if topo is not None and not topo["present"]:
        return topo
    try:
        import csv
        r = subprocess.run(["typeperf", r"\GPU Engine(*)\Utilization Percentage", r"\GPU Adapter Memory(*)\Dedicated Usage", r"\GPU Adapter Memory(*)\Shared Usage", "-sc", "2"],
                           capture_output=True, timeout=40, creationflags=CNW)
        rows = [x for x in csv.reader(r.stdout.decode("utf-8", "replace").splitlines()) if len(x) > 3]
        hdr, last = rows[0], rows[-1]
        luids = {}
        pidn = {}
        for ln in subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, timeout=20, creationflags=CNW).stdout.decode("utf-8", "replace").splitlines():
            p = ln.strip().strip('"').split('","')
            if len(p) > 1 and p[1].isdigit():
                pidn[int(p[1])] = re.sub(r"\.exe$", "", p[0], flags=re.I)
        for col, val in zip(hdr[1:], last[1:]):
            try:
                v = float(val)
            except ValueError:
                continue
            m = re.search(r"pid_(\d+)_luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+_eng_\d+_engtype_(\w+)\)", col, re.I)
            if m:
                L = luids.setdefault(m.group(2), {"eng": {}, "procs": {}, "ded": 0, "sh": 0})
                t = m.group(3).lower()
                L["eng"][t] = L["eng"].get(t, 0) + v
                if v > 0.5 and t in ("3d", "videodecode", "videoencode", "videoprocessing"):
                    n = pidn.get(int(m.group(1)))
                    if n:
                        L["procs"][n] = L["procs"].get(n, 0) + v
                continue
            m = re.search(r"(0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+\)\\(dedicated|shared) usage", col, re.I)
            if m:
                L = luids.setdefault(m.group(1), {"eng": {}, "procs": {}, "ded": 0, "sh": 0})
                L["ded" if m.group(2).lower() == "dedicated" else "sh"] = int(v / 1048576)
        d = luids
        if not d:
            raise ValueError("no adapters")
    except Exception:
        return _read_igpu_ps(nv_vram_mb)
    nv = min(d, key=lambda k: abs((d[k].get("ded") or 0) - (nv_vram_mb or 0)))
    others = [k for k in d if k != nv]
    if not others:
        return None
    a = d[others[0]]; eng = a["eng"]
    top = sorted(((n, round(v, 1)) for n, v in a["procs"].items()), key=lambda x: -x[1])[:5]
    return {"present": True, "forcedDiscrete": (topo or {}).get("forcedDiscrete", []), "util3d": round(min(100.0, eng.get("3d", 0)), 1),
            "video": round(min(100.0, eng.get("videodecode", 0) + eng.get("videoencode", 0) + eng.get("videoprocessing", 0)), 1),
            "shared_mb": a["sh"], "ded_mb": a["ded"], "procs": top}


def _read_igpu_ps(nv_vram_mb):
    """Integrated (Intel) GPU: engine load + shared memory per adapter LUID; the NVIDIA adapter is the one whose
    dedicated usage is closest to nvidia-smi's memory.used, every other adapter counts as integrated."""
    topo = igpu_topology()
    if topo is not None and not topo["present"]:
        return topo  # integrated GPU disabled/absent: do not mistake the software adapter for it
    try:
        r = subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", IGPU_PS], capture_output=True, timeout=40, creationflags=CNW)
        d = json.loads(r.stdout.decode("utf-8", "replace").strip() or "{}")
    except Exception:
        return None
    if not d:
        return None
    nv = min(d, key=lambda k: abs((d[k].get("ded") or 0) - (nv_vram_mb or 0)))
    others = [k for k in d if k != nv]
    if not others:
        return None
    a = d[others[0]]
    eng = a.get("eng") or {}
    top = sorted(((n, round(v, 1)) for n, v in (a.get("procs") or {}).items()), key=lambda x: -x[1])[:5]
    return {"present": True, "forcedDiscrete": (topo or {}).get("forcedDiscrete", []), "util3d": round(min(100.0, eng.get("3d", 0) or 0), 1),
            "video": round(min(100.0, (eng.get("videodecode", 0) or 0) + (eng.get("videoencode", 0) or 0) + (eng.get("videoprocessing", 0) or 0)), 1),
            "shared_mb": a.get("sh") or 0, "ded_mb": a.get("ded") or 0, "procs": top}


def _num(v):
    """Numeric column guard: SQLite raises 'datatype mismatch' on lists/odd types."""
    try:
        if isinstance(v, (list, tuple)):
            v = sum(float(x) for x in v)
        return float(v or 0)
    except Exception:
        return 0.0


def add_igpu(ts, g):
    if not g:
        return
    init()
    set_hw("igpu", {k: g[k] for k in ("present", "installed", "forcedDiscrete") if k in g})
    if not g.get("present"):
        return
    with db._lock:
        c = db.conn()
        c.execute("INSERT OR REPLACE INTO igpu(ts,util3d,video,shared_mb,ded_mb,procs) VALUES(?,?,?,?,?,?)",
                  (int(ts), _num(g["util3d"]), _num(g["video"]), _num(g["shared_mb"]), _num(g["ded_mb"]), json.dumps(g["procs"], ensure_ascii=False)))
        c.commit()


def read_procs():
    """Aggregate private bytes / working set per executable name (ctypes, no subprocess)."""
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32; ps = ctypes.windll.psapi

    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_void_p), ("mod", wintypes.DWORD),
                    ("thr", wintypes.DWORD), ("ppid", wintypes.DWORD), ("pri", ctypes.c_long), ("flags", wintypes.DWORD), ("exe", ctypes.c_wchar * 260)]

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p; k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]; k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.OpenProcess.restype = ctypes.c_void_p; k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    ps.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
    agg = {}
    snap = k.CreateToolhelp32Snapshot(0x2, 0)
    pe = PE32(); pe.dwSize = ctypes.sizeof(pe)
    ok = k.Process32FirstW(snap, ctypes.byref(pe))
    while ok:
        h = k.OpenProcess(0x1000, False, pe.pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            m = PMC(); m.cb = ctypes.sizeof(m)
            if ps.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb):
                a = agg.setdefault(pe.exe.lower(), [0, 0.0, 0.0]); a[0] += 1; a[1] += m.PrivateUsage / 1048576; a[2] += m.WorkingSetSize / 1048576
            k.CloseHandle(h)
        ok = k.Process32NextW(snap, ctypes.byref(pe))
    k.CloseHandle(snap)
    rows = [(n, v[0], round(v[1], 1), round(v[2], 1)) for n, v in agg.items() if v[1] >= 40]
    return sorted(rows, key=lambda r: -r[2])[:25]


import secure
# after the protected install the elevated probe writes into the admin-only out folder (users cannot forge it)
ADMIN_PROBE = secure.ADMIN_PROBE if secure.installed() else os.path.join(ROOT, "admin_probe.json")


def _admin_probe_fresh(max_age=45 * 60):
    """Result of the elevated scheduled task (disk wear, battery), if recent and parseable."""
    try:
        if time.time() - os.path.getmtime(ADMIN_PROBE) > max_age:
            return None
        with open(ADMIN_PROBE, encoding="utf-8-sig") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


UPD_MAX_AGE = 30 * 60  # عدد التحديثات المعلّقة يُحدَّث كل 30 دقيقة، بصرف النظر عن المسبار المرفوع


def _updates_probe():
    """يسأل ويندوز عن التحديثات المعلّقة فقط. يعيد dict {n, titles} أو None عند الفشل."""
    try:
        r = subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(ROOT, "healthprobe.ps1"), "-UpdatesOnly"],
                           capture_output=True, timeout=120, creationflags=CNW)
        u = json.loads(r.stdout.decode("utf-8", "replace").strip()).get("updates")
        return {"n": u.get("n"), "titles": u.get("titles")} if isinstance(u, dict) else None
    except Exception:
        return None


def _updates_loop():
    """خيط واحد يفحص Windows Update كل UPD_MAX_AGE، ولا يُستدعى من أي مسار طلب أو مجمّع فلا يحجب شيئاً."""
    time.sleep(20)  # تأخير قصير بعد الإقلاع كي لا يزاحم تحميل الخادم
    while True:
        try:
            fresh = _updates_probe()
            if fresh is not None:
                set_hw("updates", fresh)  # الطابع الزمني من set_hw = وقت الفحص الفعلي
        except Exception as e:
            errlog.swallowed()
        time.sleep(UPD_MAX_AGE)


def start_updates_worker():
    threading.Thread(target=_updates_loop, daemon=True, name="updates-worker").start()


def run_probe(skip_updates=False):
    obj = _admin_probe_fresh()
    if obj is None:
        args = [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(ROOT, "healthprobe.ps1"), "-SkipUpdates"]
        r = subprocess.run(args, capture_output=True, timeout=150, creationflags=CNW)
        obj = json.loads(r.stdout.decode("utf-8", "replace").strip())
        obj["elevated"] = False
    else:
        obj["elevated"] = True
    # التحديثات لها تخزين مستقل بطابع زمني، فلا تُعاد قراءة قيمة قديمة إلى ما لا نهاية
    obj["updates"] = get_hw("updates")[1]  # القيمة يحدّثها خيط التحديثات وحده
    obj.pop("PSComputerName", None); obj.pop("RunspaceId", None); obj.pop("PSShowComputerName", None)
    try:
        import boothist   # استيراد متأخر: الوحدة لا تلزم إلا هنا
        boothist.annotate(obj.get("bootCulprits"), boothist.service_kinds())   # ويندوز يسجّل الخدمة أحيانًا كتطبيق
    except Exception:
        errlog.swallowed()
    set_hw("probe", obj)
    try:   # الفحص يقرأ آخر 8 إقلاعات فقط؛ السجل يحفظها كلها للاتجاه، وينبّه مرة لكل إقلاع أبطأ أو فيه مسبّب جديد
        with db._lock:
            boothist.record(db.conn(), obj)
            trend = boothist.payload(db.conn())
        boothist.alert(trend, db.add_event)
    except Exception:
        errlog.swallowed()
    for b in obj.get("boots") or []:
        db.add_event(b["ts"], "boot", "info", "إقلاع الجهاز", "زمن الإقلاع %s ث" % b["bootSec"])
    return obj


# ------------------------------------------------------------------ analytics
def leaks(hours=4):
    """Executables whose private memory keeps growing across the last snapshots (>=18 points ~ 90 min)."""
    now = int(time.time()); out = []
    with db._lock:
        rows = _c().execute("SELECT name,ts,private_mb,n FROM procs WHERE ts>=? ORDER BY name,ts", (now - hours * 3600,)).fetchall()
    by = {}
    for r in rows:
        # per-instance memory: the snapshot sums all instances of one name, so opening more windows looked like a leak
        per = (r["private_mb"] or 0) / max(1, r["n"] or 1)
        by.setdefault(r["name"], []).append((r["ts"], per))
    for name, pts in by.items():
        if len(pts) < 18 or pts[-1][0] < now - 1800:
            continue
        xs = [(t - pts[0][0]) / 3600 for t, _ in pts]; ys = [v for _, v in pts]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        den = sum((x - mx) ** 2 for x in xs) or 1
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den  # MB per hour
        start = statistics.median(ys[:3]); end = statistics.median(ys[-3:]); growth = end - start
        pos = sum(1 for a, b in zip(ys, ys[1:]) if b >= a - 1) / max(1, len(ys) - 1)
        if growth >= 250 and slope >= 80 and pos >= 0.6:
            out.append({"name": name, "fromMB": round(start), "toMB": round(end), "growthMB": round(growth), "slopeMBh": round(slope), "hours": round(xs[-1], 1)})
    return sorted(out, key=lambda x: -x["growthMB"])[:6]


def disk_forecast():
    """Theil-Sen slope over daily minima of free GB (samples; falls back to cleanup-run history)."""
    with db._lock:
        c = _c()
        days = c.execute("SELECT date(ts,'unixepoch','localtime') d, MIN(disk_free_gb) v FROM samples WHERE disk_free_gb IS NOT NULL AND ts>=? GROUP BY d ORDER BY d",
                         (int(time.time()) - 60 * 86400,)).fetchall()
        basis = "قياسات"
        if len(days) < 4:
            days = c.execute("SELECT date(ts,'unixepoch','localtime') d, MIN(free_after) v FROM cleanup_runs WHERE free_after IS NOT NULL AND ts>=? GROUP BY d ORDER BY d",
                             (int(time.time()) - 60 * 86400,)).fetchall(); basis = "تشغيلات التنظيف"
        cur = c.execute("SELECT disk_free_gb FROM samples WHERE disk_free_gb IS NOT NULL ORDER BY ts DESC LIMIT 1").fetchone()
    pts = [(datetime.strptime(r["d"], "%Y-%m-%d").timestamp() / 86400, r["v"]) for r in days if r["v"] is not None]
    free = cur[0] if cur else (pts[-1][1] if pts else None)
    if len(pts) < 4 or free is None:
        return {"freeGB": free, "slopeGBday": None, "daysLeft": None, "basis": basis, "points": len(pts), "state": "جمع"}
    slopes = [(b[1] - a[1]) / (b[0] - a[0]) for i, a in enumerate(pts) for b in pts[i + 1:] if b[0] - a[0] >= 0.5]
    s = statistics.median(slopes) if slopes else 0.0
    left = None if s >= -0.05 else free / -s
    return {"freeGB": round(free, 1), "slopeGBday": round(s, 2), "daysLeft": round(left) if left else None, "basis": basis, "points": len(pts),
            "state": "مستقر" if left is None else ("حرج" if left < 14 else "تنبيه" if left < 45 else "جيد")}


def _lin(v, bad, good, mx):
    """Linear points: <=bad -> 0, >=good -> mx (bad may be > good for 'lower is better')."""
    if bad == good:
        return mx
    t = (v - bad) / (good - bad)
    return round(max(0.0, min(1.0, t)) * mx, 1)


def score(m, tasks):
    """Return measured-only points and explicit coverage/freshness; never award unknowns."""
    from health_score import build_score
    ts_probe, hw = get_hw("probe")
    updates_ts, updates = get_hw("updates")
    with db._lock:
        row = _c().execute("SELECT ts,temp FROM gpu ORDER BY ts DESC LIMIT 1").fetchone()
    return build_score(m, tasks, ts_probe, hw, updates_ts, updates,
                       dict(row) if row else None, time.time())


def save_daily(sc):
    # Partial/unknown scores have a different denominator and must not mix with complete history.
    if sc.get("status") != "complete" or sc.get("score") is None:
        return
    day = time.strftime("%Y-%m-%d")
    with db._lock:
        c = _c()
        r = c.execute("SELECT score FROM score_daily WHERE day=?", (day,)).fetchone()
        if r and r[0] is not None and r[0] <= sc["score"]:
            return   # the daily score is the WORST of the day, not the last sample
        c.execute("INSERT OR REPLACE INTO score_daily VALUES(?,?,?,?)", (day, int(time.time()), sc["score"], json.dumps(sc["parts"], ensure_ascii=False))); c.commit()


def score_history(n=30):
    with db._lock:
        rows = _c().execute("SELECT day,score FROM score_daily ORDER BY day DESC LIMIT ?", (n,)).fetchall()
    return [{"day": r["day"], "score": r["score"]} for r in reversed(rows)]


def gpu_info():
    with db._lock:
        c = _c()
        cur = c.execute("SELECT * FROM gpu ORDER BY ts DESC LIMIT 1").fetchone()
        hist = c.execute("SELECT ts,temp,util,vram_mb,power FROM gpu WHERE ts>=? ORDER BY ts", (int(time.time()) - 12 * 3600,)).fetchall()
        procs = c.execute("SELECT name,vram_mb FROM gpu_procs WHERE ts=(SELECT MAX(ts) FROM gpu_procs) ORDER BY vram_mb DESC LIMIT 6").fetchall()
        mx = c.execute("SELECT MAX(temp) t FROM gpu WHERE ts>=?", (int(time.time()) - 86400,)).fetchone()
        ig = c.execute("SELECT * FROM igpu ORDER BY ts DESC LIMIT 1").fetchone()
        ih = c.execute("SELECT ts,util3d,video,shared_mb FROM igpu WHERE ts>=? ORDER BY ts", (int(time.time()) - 12 * 3600,)).fetchall()
        igmx = c.execute("SELECT MAX(util3d) a, AVG(util3d) b FROM igpu WHERE ts>=?", (int(time.time()) - 86400,)).fetchone()
    igd = None
    tp = get_hw("igpu")[1] or {}
    if ig and tp.get("present"):
        igd = {"ts": ig["ts"], "util3d": ig["util3d"], "video": ig["video"], "shared_mb": ig["shared_mb"], "ded_mb": ig["ded_mb"],
               "shared_total": _ram_mb() // 2, "procs": json.loads(ig["procs"] or "[]"), "max24": igmx["a"], "avg24": round(igmx["b"] or 0, 1),
               "hist": [{"t": r["ts"], "util3d": r["util3d"], "video": r["video"], "shared": r["shared_mb"]} for r in ih[-160:]]}
    if igd is not None:
        igd["present"] = True
    elif tp:
        igd = {"present": False, "installed": tp.get("installed", []), "forcedDiscrete": tp.get("forcedDiscrete", [])}
    return {"igpu": igd, "now": dict(cur) if cur else None, "procs": [dict(p) for p in procs], "max24": mx["t"] if mx else None,
            "hist": [{"t": r["ts"], "temp": r["temp"], "util": r["util"], "vram": r["vram_mb"], "power": r["power"]} for r in hist[-160:]]}


def top_procs(n=8):
    with db._lock:
        r = _c().execute("SELECT name,n,private_mb,ws_mb FROM procs WHERE ts=(SELECT MAX(ts) FROM procs) ORDER BY private_mb DESC LIMIT ?", (n,)).fetchall()
    return [dict(x) for x in r]


def overview(m, tasks):
    sc = score(m, tasks); save_daily(sc)
    ts, hw = get_hw("probe")
    hw = dict(hw or {})
    uts, uval = get_hw("updates")  # القيمة الحية من خيط التحديثات، لا من آخر مسبار
    if uval is not None:
        hw["updates"] = uval; hw["updatesTs"] = uts
    return {"score": sc, "history": score_history(), "forecast": disk_forecast(), "leaks": leaks(), "gpu": gpu_info(), "top": top_procs(), "hw": hw, "hwTs": ts}
