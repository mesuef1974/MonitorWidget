# -*- coding: utf-8 -*-
"""Local monitoring backend for the dashboard window and the mini desktop widget.
Binds to 127.0.0.1 only; authenticated APIs use a local token or expiring browser session.
The agent keeps the backend running independently of the dashboard."""

from sqlite_runtime import ensure_sqlite
ensure_sqlite()
import atexit, base64, collections, hashlib, hmac, ctypes, faulthandler, json, os, re, secrets, shutil, sqlite3, subprocess, sys, threading, time, traceback
import errlog
import atomic_json
import appearance
import math, socket
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import secure, db, dbview, health, infra, sched, space, startup_mgr, cpuwatch, gpuwatch, insight, alertcfg
try:
    import diskio_api
except Exception:
    diskio_api = None
try:
    import homeapi
except Exception:
    homeapi = None
try:
    import reliab
except Exception:
    reliab = None

ROOT = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ["USERPROFILE"]
PORT = 47832
# one-time tickets replace the token in URLs: the window opens with ?k=<ticket>, which is consumed once and then
# exchanged for an HttpOnly session cookie. The long-lived token never appears on a command line.
try:
    SESSION_TTL = max(60, min(7 * 86400, int(os.environ.get("MW_SESSION_TTL_SECONDS", "43200"))))
except ValueError:
    SESSION_TTL = 43200
_SESSIONS, _SLOCK = {}, threading.Lock()
MAX_SESSIONS = 128
MAX_POST_BYTES = 65536
REQUEST_TIMEOUT_SECONDS = 15
MAX_HTTP_THREADS = 24


def _session_create():
    now = time.time()
    with _SLOCK:
        for key in [key for key, exp in _SESSIONS.items() if exp <= now]:
            del _SESSIONS[key]
        if len(_SESSIONS) >= MAX_SESSIONS:
            return None
        value = secrets.token_urlsafe(32)
        _SESSIONS[hashlib.sha256(value.encode("utf-8")).digest()] = now + SESSION_TTL
        return value


def _session_valid(value):
    if not value or len(value) > 128:
        return False
    key = hashlib.sha256(value.encode("utf-8")).digest()
    with _SLOCK:
        exp = _SESSIONS.get(key, 0)
        if exp <= time.time():
            _SESSIONS.pop(key, None)
            return False
        return True


def _session_revoke(value):
    with _SLOCK:
        _SESSIONS.pop(hashlib.sha256(value.encode("utf-8")).digest(), None)


def _reject_json_constant(value):
    raise ValueError("non-finite JSON number")


def _validate_json_numbers(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, dict):
        for item in value.values():
            _validate_json_numbers(item)
    elif isinstance(value, list):
        for item in value:
            _validate_json_numbers(item)

_TICKETS, _TLOCK = {}, threading.Lock()
# server-side confirmation for destructive POSTs: the first call returns 428 + a one-time token bound to the route and
# the exact parameters; the action runs only when the same request comes back with that token within CONFIRM_TTL.
_HEALTH = {"t": 0.0, "ok": True}
_HEALTH_LOCK = threading.Lock()


def _healthz_ok():
    """quick_check on a read-only connection, cached for 60 s: an unauthenticated endpoint must not be a lever on the DB."""
    with _HEALTH_LOCK:
        if time.time() - _HEALTH["t"] > 60:
            try:
                with db.ro() as rc:
                    _HEALTH["ok"] = infra.integrity(rc)[0]
            except Exception:
                _HEALTH["ok"] = False
            _HEALTH["t"] = time.time()
        return _HEALTH["ok"]


_CONFIRMS, _CLOCK, CONFIRM_TTL = {}, threading.Lock(), 120
def _confirm_key(route, body):
    return route + "|" + json.dumps({k: v for k, v in body.items() if k != "confirm"}, sort_keys=True, ensure_ascii=False, allow_nan=False)
def _confirm_issue(key):
    t = secrets.token_urlsafe(18)
    with _CLOCK:
        now = time.time()
        for k in [k for k, (_, exp) in _CONFIRMS.items() if exp < now]:
            del _CONFIRMS[k]
        if len(_CONFIRMS) >= 256:
            del _CONFIRMS[next(iter(_CONFIRMS))]
        _CONFIRMS[t] = (key, now + CONFIRM_TTL)
    return t
def _confirm_take(key, tok):
    with _CLOCK:
        got = _CONFIRMS.pop(tok, None)
    return bool(got and got[0] == key and got[1] >= time.time())
def _issue_ticket():
    t = secrets.token_urlsafe(24)
    with _TLOCK:
        now = time.time()
        for k in [k for k, exp in _TICKETS.items() if exp < now]:
            del _TICKETS[k]
        if len(_TICKETS) >= 256:
            del _TICKETS[next(iter(_TICKETS))]
        _TICKETS[t] = now + 60
    return t
def _use_ticket(t):
    with _TLOCK:
        exp = _TICKETS.pop(t, None)
    return bool(exp and exp >= time.time())
PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"
CNW = 0x08000000  # CREATE_NO_WINDOW
ENGINE = HOME + r"\Documents\Scripts\Cleanup\v2\CleanupEngine.ps1"
POLICY = HOME + r"\Documents\Scripts\Cleanup\v2\cleanup-policy.json"
PROT_ENGINE_DIR = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "MonitorWidget", "cleanup")
RUNS = HOME + r"\Documents\Scripts\Cleanup\logs\runs.jsonl"
MCCFG = HOME + r"\Documents\Scripts\MachineCare\machinecare.config.json"
IDLE_EXIT_SECONDS = 0  # 0 = never exit on idle; the agent's server_guard job keeps it running
TIERS = ("daily", "weekly", "monthly", "quarterly")
TIER_TASK = {t: "Cleanup_v2_" + t.capitalize() for t in TIERS}

TOKEN_FILE = os.path.join(ROOT, ".token")
if not os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE, "w") as f:
        f.write(secrets.token_urlsafe(24))
TOKEN = open(TOKEN_FILE).read().strip()
# Browser sessions are independent, time limited, and intentionally invalidated on restart.

with open(os.path.join(ROOT, "tasks.json"), encoding="utf-8") as f:
    WHITELIST = {(t["path"], t["name"]): t for t in json.load(f)}

_lock = threading.Lock()
_cache = {"t": 0, "data": None}
_extra = {"tiers": {}, "paused": []}
_last_request = time.time()
_manual = {"proc": None, "tier": None, "dry": None, "started": None}
_backed_up = set()
_hist = collections.deque(maxlen=360)  # one sample / 5 s = 30 min


# ---------------------------------------------------------------- fast native metrics (no subprocess)
class MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + [
        (n, ctypes.c_ulonglong) for n in ("tp", "ap", "tpf", "apf", "tv", "av", "aev")]


class PE32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD),
                ("heap", ctypes.c_void_p), ("mod", wintypes.DWORD), ("thr", wintypes.DWORD),
                ("ppid", wintypes.DWORD), ("pri", ctypes.c_long), ("flags", wintypes.DWORD),
                ("exe", ctypes.c_wchar * 260)]


def native_metrics():
    ms = MEMSTAT(); ms.dwLength = ctypes.sizeof(ms)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
    gb = 1024 ** 3
    commit_used = (ms.tpf - ms.apf) / gb
    limit = ms.tpf / gb
    total = ms.tp / gb
    avail = ms.ap / gb
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    snap = k.CreateToolhelp32Snapshot(0x2, 0)
    counts = collections.Counter()
    if snap and snap != ctypes.c_void_p(-1).value:
        pe = PE32(); pe.dwSize = ctypes.sizeof(pe)
        ok = k.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            counts[pe.exe.lower()] += 1
            ok = k.Process32NextW(snap, ctypes.byref(pe))
        k.CloseHandle(snap)
    du = shutil.disk_usage("C:\\")
    return {
        "commitPct": round(100 * commit_used / limit, 1), "commitGB": round(commit_used, 1), "limitGB": round(limit, 1),
        "totalGB": round(total, 1), "availGB": round(avail, 2), "ramPct": round(100 * (total - avail) / total, 1),
        "diskFreeGB": round(du.free / gb, 1), "diskTotalGB": round(du.total / gb, 1),
        "diskPct": round(100 * (du.total - du.free) / du.total, 1),
        "claude": counts["claude.exe"], "shells": counts["pwsh.exe"] + counts["powershell.exe"], "conhost": counts["conhost.exe"],
    }


def sampler():
    k = 0
    while True:
        try:
            m = native_metrics()
            _hist.append((int(time.time()), m["commitPct"], m["ramPct"]))
            k += 1
        except Exception:
            errlog.swallowed()
        time.sleep(5)


def ingester():
    n = 0
    while True:
        try:
            db.import_all()
            if _cache["data"]:
                db.note_tasks(_cache["data"].get("tasks"))
            n += 1
            if n % 180 == 1:
                db.prune()
        except Exception:
            errlog.swallowed()
        time.sleep(120)


# ---------------------------------------------------------------- json helpers
def touch():
    global _last_request
    _last_request = time.time()


def read_json(path):
    return atomic_json.read(path)


def _backup_json(path):
    if path not in _backed_up:
        bdir = os.path.join(ROOT, "backups")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy(path, os.path.join(bdir, time.strftime("%Y%m%d_%H%M%S_") + os.path.basename(path)))
        _backed_up.add(path)


def write_json(path, bom, obj):
    with atomic_json.locked(path):
        _backup_json(path)
        atomic_json.write(path, bom, obj)


def edit_json(path):
    return atomic_json.transaction(path, before_write=_backup_json)


# ---------------------------------------------------------------- heavy collector (PowerShell) + extras
def _collect_ttl():
    """How long the heavy PowerShell collection may be reused. It spawns a full pwsh each time (about 1-3 s of CPU),
    and the task list changes slowly, so: 120 s during the first 4 minutes after the server starts (boot window), 45 s afterwards."""
    return 120 if time.time() - infra.START < 240 else 45


def collect(force=False):
    with _lock:
        if not force and _cache["data"] and time.time() - _cache["t"] < _collect_ttl():
            return attach_manual(_cache["data"])
        r = subprocess.run(
            [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(ROOT, "collect.ps1"), "-Root", ROOT],
            capture_output=True, timeout=sched.remaining_timeout(90), creationflags=CNW)
        data = json.loads(r.stdout.decode("utf-8", "replace").strip())
        _cache["data"], _cache["t"] = data, time.time()
    try:
        db.set_json("tasks_snapshot", {"ts": time.time(), "tasks": data.get("tasks", [])})
    except Exception:
        errlog.swallowed()
    try:
        db.note_tasks(data.get("tasks"))
    except Exception:
        errlog.swallowed()
    refresh_extra()
    return attach_manual(data)


def attach_manual(data):
    p = _manual["proc"]
    data["manual"] = {"running": bool(p and p.poll() is None), "tier": _manual["tier"], "dry": _manual["dry"], "started": _manual["started"]}
    data["pausedTiers"] = list(_extra["paused"])
    return data


def refresh_extra():
    try:
        _, pol = read_json(POLICY)
        _extra["paused"] = [t for t in pol.get("pausedTiers", []) if t in TIERS]
    except Exception:
        errlog.swallowed()
    last = {}
    try:
        with open(RUNS, "rb") as f:
            f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 60000))
            lines = f.read().decode("utf-8", "replace").splitlines()
        for ln in lines:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if not r.get("DryRun"):
                last[r["Tier"]] = {"ts": r.get("Started"), "failed": r.get("RulesFailed", 0), "freed": r.get("BytesFreed", 0), "files": r.get("FilesActed", 0)}
    except Exception:
        errlog.swallowed()
    _extra["tiers"] = last


def refresher():
    while True:
        time.sleep(20)
        if time.time() - _last_request < 120:
            try:
                collect(force=True)
            except Exception:
                errlog.swallowed()


SETTINGS = os.path.join(ROOT, "widget.settings.json")
APPEARANCE = appearance.AppearanceStore(ROOT)


def get_theme():
    return APPEARANCE.get()["mode"]


def set_theme(t):
    return APPEARANCE.set_mode(t)


_gn = {"anchor": None, "period": 300, "ts": 0, "busy": False}


def _gn_refresh():
    try:
        ps = ("$t=Get-ScheduledTask SchoolOS-MemoryGuard; $i=$t|Get-ScheduledTaskInfo; "
              "$r=($t.Triggers|?{$_.Repetition.Interval}|select -First 1).Repetition.Interval; "
              "$s=if($r){[int][Xml.XmlConvert]::ToTimeSpan($r).TotalSeconds}else{300}; "
              "'{0}|{1}' -f ([DateTimeOffset]$i.NextRunTime).ToUnixTimeSeconds(),$s")
        r = subprocess.run([PWSH, "-NoProfile", "-Command", ps], capture_output=True, timeout=sched.remaining_timeout(30), creationflags=CNW)
        a, p = r.stdout.decode("utf-8", "replace").strip().split("|")
        _gn["anchor"], _gn["period"] = int(a), max(60, int(p))
    except Exception:
        errlog.swallowed()
    finally:
        _gn["ts"] = time.time(); _gn["busy"] = False


def _guard_task_next():
    """ISO local time of the next MemoryGuard run, projected from the task's NextRunTime anchor."""
    if time.time() - _gn["ts"] > 600 and not _gn["busy"]:
        _gn["busy"] = True
        threading.Thread(target=_gn_refresh, daemon=True).start()
    if not _gn["anchor"]:
        return None
    now, per = time.time(), _gn["period"]
    nxt = _gn["anchor"] if _gn["anchor"] > now else _gn["anchor"] + ((int(now) - _gn["anchor"]) // per + 1) * per
    return {"next": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(nxt)), "period": per}


AUTO_PERIOD = 600
_cycle = {"done": time.time()}


def auto_trim_on():
    """Automatic memory trim runs only when the settings row 'trim.auto' is true. Manual trim is not affected."""
    try:
        with db._lock:
            r = db.conn().execute("SELECT value FROM settings WHERE key='trim.auto'").fetchone()
        return bool(r and json.loads(r[0]) is True)
    except Exception:
        errlog.swallowed()
        return False


def guard_next():
    """Countdown to the next automatic trim: the agent's `trim` job (restarts after every trim, manual or automatic)."""
    if _trim["running"]:  # countdown stops while a trim runs; it restarts when the trim ends (trim_memory sets the next run)
        return {"next": None, "period": AUTO_PERIOD, "running": True, "auto": auto_trim_on()}
    if not auto_trim_on():
        return {"next": None, "period": AUTO_PERIOD, "running": False, "auto": False}
    r = sched.next_run("trim")
    nxt = r["next_run"] if r and r["next_run"] else _cycle["done"] + AUTO_PERIOD
    running = bool(_trim["running"] or (r and r["last_start"] and (not r["last_end"] or r["last_end"] < r["last_start"]) and time.time() - r["last_start"] < 300))
    return {"next": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(nxt)), "period": AUTO_PERIOD, "running": running}


MAINT_RULES = ("docker-dangling", "docker-build-cache", "claude-file-history")


def maintenance_state():
    try:
        _b, pol = read_json(POLICY)
        m = pol.get("maintenance") or {}
        return {"on": bool(m.get("on")), "rules": list(MAINT_RULES), "since": m.get("since")}
    except Exception:
        errlog.swallowed()
        return {"on": False, "rules": list(MAINT_RULES), "since": None}


def maintenance_set(on):
    """Maintenance mode: switch the Docker/Claude rules off (remembering their state) and restore them exactly when it ends."""
    with edit_json(POLICY) as (bom, pol):
        m = pol.get("maintenance") or {}
        if on and not m.get("on"):
            saved = {}
            for r in pol["rules"]:
                if r["id"] in MAINT_RULES:
                    saved[r["id"]] = bool(r.get("enabled")); r["enabled"] = False
            pol["maintenance"] = {"on": True, "since": int(time.time()), "saved": saved}
        elif (not on) and m.get("on"):
            saved = m.get("saved") or {}
            for r in pol["rules"]:
                if r["id"] in saved:
                    r["enabled"] = bool(saved[r["id"]])
            pol["maintenance"] = {"on": False, "since": None, "saved": {}}
        else:
            return maintenance_state()

    with db._lock:
        c = db.conn(); c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)", (int(time.time()), "maintenance", "mode", 1, "on=" + str(bool(on)).lower())); c.commit()
    return maintenance_state()


def set_trim_auto(on):
    with db._lock:
        c = db.conn()
        c.execute("INSERT INTO settings(key,value,ts) VALUES('trim.auto',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts", (json.dumps(bool(on)), int(time.time())))
        c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)", (int(time.time()), "trimauto", "setting", 1, "on=" + str(bool(on)).lower()))
        c.commit()


def _trim_view():
    """Trim state for the UI: the in-process result merged with the newest row in the DB (the agent may have trimmed)."""
    t = dict(_trim)
    try:
        with db._lock:
            r = db.conn().execute("SELECT ts, before_pct, after_pct, freed_mb, ms, mode FROM trims ORDER BY ts DESC LIMIT 1").fetchone()
        mine = time.mktime(time.strptime(t["ts"], "%Y-%m-%dT%H:%M:%S")) if t.get("ts") else 0
        if r and r["ts"] > mine:
            t.update(freedMB=r["freed_mb"], before=r["before_pct"], after=r["after_pct"], ms=r["ms"], mode=r["mode"], procs=0, err=None,
                     ts=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(r["ts"])))
    except Exception:
        errlog.swallowed()
    t["running"] = guard_next()["running"]
    t["auto"] = auto_trim_on()
    return t


def ensure_agent():
    """Start the always-on agent if its mutex is not held (idempotent; the agent also enforces a single instance)."""
    try:
        if ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "Local\\Azkia-MonitorAgent"):
            return False
        exe = sys.executable.replace("python.exe", "pythonw.exe")
        subprocess.Popen([exe, os.path.join(ROOT, "agent.py")], cwd=ROOT, creationflags=CNW)
        infra.log.info("agent launched by server")
        return True
    except Exception:
        infra.log.exception("ensure_agent")
        return False

def _cpu_alert_safe():
    try:
        return cpuwatch.alert_state()
    except Exception:
        return None


def _agent_hb_age():
    try:
        v = db._meta_get("agent_hb", 0)
        return max(0, int(time.time()) - int(v)) if v else None
    except Exception:
        return None


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


IS_ADMIN = _is_admin()


def _resolve_engine():
    """(path, error). النسخة المحمية في ProgramData مع تحقق SHA-256؛ نسخة المستخدم قابلة للكتابة فلا تُشغَّل بصلاحية عالية."""
    prot = os.path.join(PROT_ENGINE_DIR, "CleanupEngine.ps1")
    shaf = os.path.join(PROT_ENGINE_DIR, "engine.sha256")
    if os.path.isfile(prot) and os.path.isfile(shaf):
        try:
            want = open(shaf, encoding="ascii").read().split()[0].lower()
            got = hashlib.sha256(open(prot, "rb").read()).hexdigest()
        except Exception:
            return None, "تعذّر التحقق من نسخة المحرّك المحمية"
        if want != got:
            return None, "بصمة المحرّك المحمي لا تطابق engine.sha256؛ أعد تشغيل Install-ProtectedEngine.ps1 كمسؤول"
        return prot, None
    if IS_ADMIN or os.path.isdir(PROT_ENGINE_DIR):
        return None, "الخادم يعمل بصلاحية مسؤول أو سبق تفعيل النسخة المحمية ولا توجد نسخة محمية من المحرّك؛ شغّل Install-ProtectedEngine.ps1 كمسؤول"
    return ENGINE, None


def mini_payload():
    data = _cache["data"]
    tiers = {}
    tasks = {t["name"]: t for t in (data or {}).get("tasks", [])}
    for t in TIERS:
        task = tasks.get(TIER_TASK[t], {})
        tiers[t] = {"paused": t in _extra["paused"], "last": _extra["tiers"].get(t), "next": task.get("next"),
                    "schedule": task.get("schedule"), "taskState": task.get("state")}
    m = native_metrics()
    h = list(_hist)
    dm = (data or {}).get("metrics", {})
    m["claudeGB"] = dm.get("claudeGB"); m["wslGB"] = dm.get("wslGB")
    p = _manual["proc"]
    appearance_state = APPEARANCE.get()
    return {
        "now": time.strftime("%Y-%m-%dT%H:%M:%S"), "m": m,
        "hist": {"t": [x[0] for x in h], "commit": [x[1] for x in h], "ram": [x[2] for x in h]},
        "tiers": tiers, "guard": (data or {}).get("guard"),
        "manual": {"running": bool(p and p.poll() is None), "tier": _manual["tier"], "dry": _manual["dry"]},
        "ready": data is not None, "guardNext": guard_next(), "trim": _trim_view(), "theme": appearance_state["mode"], "appearance": appearance_state, "cpuAlert": _cpu_alert_safe(),
        "admin": IS_ADMIN, "agentAge": _agent_hb_age(), "cpuNow": cpuwatch.now_total(), "thr": alertcfg.get(),
    }


def _mem_status():
    import ctypes
    class MS(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                    ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong), ("c", ctypes.c_ulonglong)]
    m = MS(); m.l = ctypes.sizeof(MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ap // (1024 * 1024), m.load


def _avail_mb():
    return _mem_status()[0]


_trim = {"running": False, "freedMB": None, "ts": None, "err": None, "before": None, "after": None, "procs": 0, "ms": 0}


def _trim_all_working_sets():
    """PC-Manager style: EmptyWorkingSet on every process we may open (same user; no admin). Pages move to standby = available."""
    import ctypes
    from ctypes import wintypes
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.OpenProcess.restype = wintypes.HANDLE
    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_void_p),
                    ("mod", wintypes.DWORD), ("thr", wintypes.DWORD), ("ppid", wintypes.DWORD), ("pri", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("exe", ctypes.c_wchar * 260)]
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    pe = PE(); pe.dwSize = ctypes.sizeof(PE); n = 0; me = os.getpid()
    ok = k32.Process32FirstW(snap, ctypes.byref(pe))
    while ok:
        if pe.pid > 4 and pe.pid != me:
            h = k32.OpenProcess(0x0100 | 0x0400 | 0x1000, False, pe.pid)  # SET_QUOTA | QUERY_INFORMATION | QUERY_LIMITED
            if h:
                if psapi.EmptyWorkingSet(h):
                    n += 1
                k32.CloseHandle(h)
        ok = k32.Process32NextW(snap, ctypes.byref(pe))
    k32.CloseHandle(snap)
    return n


COLD_NAMES = {"node.exe", "pwsh.exe", "cmd.exe"}   # background shells and dev servers only; never browsers, editors, Claude
COLD_MAX_PROCS = 20


def _visible_pids():
    """PIDs that own a visible top-level window (foreground or background apps with a window are never trimmed)."""
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    pids = set()
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if u32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)
        return True
    u32.EnumWindows(CB(cb), 0)
    return pids


def _cpu_ms(h):
    import ctypes
    class FT(ctypes.Structure):
        _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]
    c, e, k, u = FT(), FT(), FT(), FT()
    if not ctypes.windll.kernel32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
        return None
    tot = lambda f: (f.hi << 32) | f.lo
    return (tot(k) + tot(u)) / 10000.0


def _trim_cold():
    """Empty the working set of background shells that have no window and used almost no CPU over a 2 s sample.
    Their pages move to standby; nothing is purged from the standby list, so no system cache is thrown away."""
    import ctypes
    from ctypes import wintypes
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.OpenProcess.restype = wintypes.HANDLE
    class PE(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_void_p),
                    ("mod", wintypes.DWORD), ("thr", wintypes.DWORD), ("ppid", wintypes.DWORD), ("pri", wintypes.LONG),
                    ("flags", wintypes.DWORD), ("exe", ctypes.c_wchar * 260)]
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    pe = PE(); pe.dwSize = ctypes.sizeof(PE); me = os.getpid(); cands = []
    ok = k32.Process32FirstW(snap, ctypes.byref(pe))
    while ok:
        if pe.pid > 4 and pe.pid != me and pe.exe.lower() in COLD_NAMES:
            cands.append(pe.pid)
        ok = k32.Process32NextW(snap, ctypes.byref(pe))
    k32.CloseHandle(snap)
    vis = _visible_pids()
    cands = [p for p in cands if p not in vis]
    handles = {}
    for p in cands:
        h = k32.OpenProcess(0x0400 | 0x1000, False, p)   # QUERY_INFORMATION | QUERY_LIMITED
        if h:
            handles[p] = h
    before = {p: _cpu_ms(h) for p, h in handles.items()}
    time.sleep(2)
    idle = [p for p, h in handles.items() if before[p] is not None and _cpu_ms(h) is not None and _cpu_ms(h) - before[p] < 200]
    for h in handles.values():
        k32.CloseHandle(h)
    done = 0
    for p in idle[:COLD_MAX_PROCS]:
        h = k32.OpenProcess(0x0100 | 0x0400, False, p)   # SET_QUOTA | QUERY_INFORMATION
        if not h:
            continue
        try:
            if psapi.EmptyWorkingSet(h):
                done += 1
        finally:
            k32.CloseHandle(h)
    return done, len(cands), len(idle)


def _trim_medium(t0):
    b_mb, b_load = _mem_status()
    done, cands, idle = _trim_cold()
    try:
        _purge_task("standby")   # elevated; purge.exe purges standby only when normal-priority standby >= 2 GB
    except Exception:
        errlog.swallowed()
    time.sleep(0.5)
    a_mb, a_load = _mem_status()
    ms = int((time.time() - t0) * 1000)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    _trim.update(freedMB=a_mb - b_mb, before=b_load, after=a_load, procs=done, mode="medium", ms=ms, ts=ts, err=None,
                 candidates=cands, idle=idle)
    try:
        db.add_trim(time.time(), b_load, a_load, a_mb - b_mb, ms, "medium")
    except Exception:
        errlog.swallowed()


PURGE_TASK = "MemoryPurge-OnDemand"
PURGE_LOCK = os.path.join(ROOT, "purge.lock")


def _schtasks(args, timeout=10):
    """schtasks with a hard timeout: a hung Task Scheduler must never hang the trim thread."""
    try:
        return subprocess.run(["schtasks"] + args, capture_output=True, creationflags=CNW, timeout=sched.remaining_timeout(timeout))
    except subprocess.TimeoutExpired:
        errlog.swallowed()
        return None


def _purge_lock_take():
    """File lock shared by server and agent (two processes). A lock older than 120 s is stale."""
    if os.path.exists(PURGE_LOCK) and time.time() - os.path.getmtime(PURGE_LOCK) < 120:
        raise RuntimeError("purge already running")
    with open(PURGE_LOCK, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))


def _purge_lock_drop():
    try:
        os.remove(PURGE_LOCK)
    except OSError:
        pass


def _purge_task(mode="light"):
    """Elevated on-demand purge. Returns result dict, or None if no task is installed.
    Protected install: one task per mode with the mode as a fixed argument (no file is trusted).
    Legacy fallback (before the owner runs the secure installer): the old task plus purge_mode.txt.
    mode 'light' = low-priority standby pages only (default); 'deep' = working sets + standby (manual only)."""
    expected = secure.protected_expected()
    secure_ok = secure.installed() and mode in secure.PURGE_TASKS
    if expected and not secure_ok:
        raise RuntimeError("protected purge installation incomplete; repair the secure installation")
    task = secure.PURGE_TASKS[mode] if secure_ok else PURGE_TASK
    res = secure.PURGE_RESULT if secure_ok else os.path.join(ROOT, "purge_result.json")
    q = _schtasks(["/query", "/tn", task])
    if q is None:
        raise RuntimeError("schtasks query timed out")
    if q.returncode != 0:
        if not secure_ok:
            return None
        raise RuntimeError("protected purge task missing; repair the secure installation")
    _purge_lock_take()
    try:
        if not secure_ok:
            with open(os.path.join(ROOT, "purge_mode.txt"), "w", encoding="utf-8") as fh:
                fh.write(mode)
        old = os.path.getmtime(res) if os.path.exists(res) else 0
        r = _schtasks(["/run", "/tn", task])
        if r is None or r.returncode != 0:
            return None
        for _ in range(90):   # bounded result wait, also respects a scheduler job deadline
            sched.check_cancelled()
            time.sleep(0.5)
            if os.path.exists(res) and os.path.getmtime(res) > old:
                for _ in range(20):
                    sched.check_cancelled()
                    time.sleep(0.2)
                    try:
                        with open(res, encoding="utf-8-sig") as fh:
                            return json.load(fh)
                    except ValueError:
                        continue
                raise RuntimeError("purge result unreadable")
        raise RuntimeError("purge task timed out")
    finally:
        _purge_lock_drop()


def trim_memory(mode="light"):
    """One pass only (the old extra passes re-faulted pages and froze the machine)."""
    mode = mode if mode in ("light", "medium", "deep") else "light"
    _trim.update(running=True, err=None, mode=mode)
    t0 = time.time()
    try:
        if mode == "medium":
            _trim_medium(t0)
            return
        res = _purge_task(mode)
        if res:
            _trim.update(freedMB=res.get("freedMB"), before=res.get("beforePct"), after=res.get("afterPct"), procs=0, mode=mode,
                         ms=int((time.time() - t0) * 1000), ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
        elif mode == "deep":
            b_mb, b_load = _mem_status()
            n = _trim_all_working_sets(); time.sleep(0.6)
            a_mb, a_load = _mem_status()
            _trim.update(freedMB=a_mb - b_mb, before=b_load, after=a_load, procs=n, mode="user",
                         ms=int((time.time() - t0) * 1000), ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
        else:
            raise RuntimeError("purge task not installed")
    except Exception as e:
        _trim.update(err=str(e)[:200])
    finally:
        _trim["running"] = False
        _cycle["done"] = time.time()
        try:
            sched.set_next("trim", time.time() + AUTO_PERIOD)
        except Exception:
            errlog.swallowed()
    try:
        if _trim.get("ts") and not _trim.get("err"):
            db.add_trim(time.time(), _trim.get("before"), _trim.get("after"), _trim.get("freedMB"), _trim.get("ms"), _trim.get("mode"))
    except Exception:
        errlog.swallowed()


def task_op(op, path, name):
    r = subprocess.run(
        [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(ROOT, "ops.ps1"), "-Op", op, "-Path", path, "-Name", name],
        capture_output=True, timeout=sched.remaining_timeout(40), creationflags=CNW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.decode("utf-8", "replace") or "failed").strip()[:300])


_ASSETS = {
    "/assets/favicon.svg": "image/svg+xml", "/assets/icon.svg": "image/svg+xml",
    "/assets/logo_light.svg": "image/svg+xml", "/assets/logo_dark.svg": "image/svg+xml",
    "/assets/favicon.ico": "image/x-icon", "/assets/icon.ico": "image/x-icon",
    "/assets/icon_192.png": "image/png", "/assets/icon_512.png": "image/png",
    "/assets/apple-touch-icon.png": "image/png",
    "/assets/logo_light.png": "image/png", "/assets/logo_dark.png": "image/png",
}


_CSP_BASE = ("default-src 'none'; connect-src 'self'; img-src 'self' data:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'")
_csp_cache = {}


def _page_csp(html):
    """CSP for the page: inline scripts are allowed only by the hash of the exact <script> bodies in the served file."""
    key = hash(html)
    if key not in _csp_cache:
        _csp_cache.clear()
        bodies = re.findall(rb"<script>(.*?)</script>", html, re.S)
        # HTML parsing normalizes CRLF and CR to LF before the browser hashes
        # inline script text. Windows-written source files must use that exact
        # normalized text too, otherwise CSP blocks the entire application.
        hashes = " ".join("'sha256-%s'" % base64.b64encode(hashlib.sha256(b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).digest()).decode() for b in bodies)
        _csp_cache[key] = "script-src %s; %s" % (hashes or "'none'", _CSP_BASE)
    return _csp_cache[key]


class LocalHTTPServer(ThreadingHTTPServer):
    """Bound concurrent connections before creating worker threads."""
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(MAX_HTTP_THREADS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache="no-store", csp=None, cookie=None, etag=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", cache)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        if etag:
            self.send_header("ETag", etag)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        # JSON replies get a locked-down policy; the HTML page gets the hash-based one
        self.send_header("Content-Security-Policy", csp or "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(b)

    def _guard(self):
        if self.headers.get("Host", "") not in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT):
            self._send(403, {"error": "bad host"})
            return False
        touch()
        return True

    def _cookie(self, name):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def _authed(self):
        tok = self.headers.get("X-Token", "")
        ck = self._cookie("mw_s")
        # constant-time comparison; both sides encoded so a non-ASCII header cannot raise
        if (tok and hmac.compare_digest(tok.encode("utf-8"), TOKEN.encode("utf-8"))) or            _session_valid(ck):
            return True
        self._send(401, {"error": "unauthorized"})
        return False

    def do_GET(self):
        if not self._guard():
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/v2", "/v2/"):  # /v2 اسم قديم للصفحة نفسها
            k = parse_qs(urlparse(self.path).query).get("k", [""])[0]
            if k and _use_ticket(k):  # exchange the one-time ticket for a cookie, then drop it from the URL
                session = _session_create()
                if session is None:
                    return self._send(503, {"error": "session capacity reached"})
                self.send_response(302)
                self.send_header("Set-Cookie", "mw_s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (session, SESSION_TTL))
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            page = open(os.path.join(ROOT, "web", "index.html"), "rb").read()
            self._send(200, page, "text/html; charset=utf-8", csp=_page_csp(page))
        elif re.fullmatch(r"/css/[a-z0-9_-]+\.css", path):
            # أنماط الواجهة المركزية: ملفات web/css الثابتة بلا أسرار؛ ETag بدل التخزين الطويل حتى يظهر أي تعديل فورًا
            try:
                body = open(os.path.join(ROOT, "web", "css", path.rsplit("/", 1)[1]), "rb").read()
            except OSError:
                return self._send(404, {"error": "not found"})
            tag = '"%s"' % hashlib.sha1(body).hexdigest()[:16]
            if self.headers.get("If-None-Match") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            self._send(200, body, "text/css; charset=utf-8", cache="no-cache", etag=tag)
        elif re.fullmatch(r"/fonts/[a-z0-9-]+\.woff2", path):
            # خطوط الهوية (OFL) من web/fonts: ملفات ثابتة بلا أسرار؛ ETag + تخزين يومي لأن أسماءها تحمل الوزن ولا تتغيّر إلا مع الملف
            try:
                body = open(os.path.join(ROOT, "web", "fonts", path.rsplit("/", 1)[1]), "rb").read()
            except OSError:
                return self._send(404, {"error": "not found"})
            tag = '"%s"' % hashlib.sha1(body).hexdigest()[:16]
            if self.headers.get("If-None-Match") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                return
            self._send(200, body, "font/woff2", cache="public, max-age=86400", etag=tag)
        elif path in _ASSETS or path == "/favicon.ico":
            fn = "favicon.ico" if path == "/favicon.ico" else path
            fp = os.path.join(ROOT, "assets", fn.lstrip("/").replace("assets/", "", 1))
            try:
                # static icons/logos carry ?v=N in the page, so they can be cached; only data replies stay no-store
                self._send(200, open(fp, "rb").read(), _ASSETS.get(fn, "image/x-icon"), cache="public, max-age=86400")
            except OSError:
                self._send(404, {"error": "not found"})
        elif path == "/api/healthz":
            ok = _healthz_ok()
            self._send(200 if ok else 503, {"ok": ok, "uptime_s": int(time.time() - infra.START), "pid": os.getpid()})
        elif path == "/api/db":
            if not self._authed():
                return
            self._send(200, dbview.overview())
        elif path == "/api/db/rows":
            if not self._authed():
                return
            q = parse_qs(urlparse(self.path).query)
            try:
                self._send(200, dbview.rows(q.get("table", [""])[0], q.get("limit", ["25"])[0], q.get("offset", ["0"])[0]))
            except (ValueError, sqlite3.Error):
                self._send(400, {"error": "bad params"})
        elif path == "/api/jobs":
            if not self._authed():
                return
            self._send(200, {"jobs": sched.listing(), "agent": space._agent_status(), "quarantine": space.quarantine_list()})
        elif path == "/api/startup":
            if not self._authed():
                return
            self._send(200, startup_mgr.list_items())
        elif path == "/api/actions":
            if not self._authed():
                return
            with db._lock:
                rows = [dict(r) for r in db.conn().execute("SELECT * FROM actions ORDER BY ts DESC LIMIT 100")]
            self._send(200, {"rows": rows})
        elif path == "/api/space":
            if not self._authed():
                return
            infra.log.info("space: request received")
            _t0 = time.time()
            d = space.overview()
            infra.log.info("space: overview built in %.2fs" % (time.time() - _t0))
            d['quarantine'] = space.quarantine_list()
            self._send(200, d)
            infra.log.info("space: response sent in %.2fs" % (time.time() - _t0))
        elif path == "/api/widget":
            if not self._authed():
                return
            self._send(200, {"running": widget_running() and widget_visible()})
        elif path == "/api/infra":
            if not self._authed():
                return
            try:
                with db.ro() as _rc:
                    self._send(200, infra.overview(_rc))
            except Exception as e:
                self._send(500, {"error": str(e)[:200]})
        elif path == "/api/state":
            if not self._authed():
                return
            try:
                if _cache["data"] and time.time() - _cache["t"] < 30:
                    self._send(200, attach_manual(_cache["data"]))
                else:
                    self._send(200, collect())
            except Exception as e:
                self._send(500, {"error": "collect failed: %s" % e})
        elif path.startswith("/api/") and path in ("/api/history", "/api/events", "/api/runs", "/api/db-info", "/api/theme", "/api/appearance", "/api/health"):
            if not self._authed():
                return
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            try:
                ival = lambda k, d, lo, hi: max(lo, min(hi, int(q.get(k, d))))
                if path == "/api/history":
                    out = db.history(q.get("range", "24h"))
                elif path == "/api/events":
                    out = db.events(ival("limit", 10, 1, 100), ival("offset", 0, 0, 10**7), q.get("sev"), (q.get("q") or "")[:60] or None, q.get("range"))
                elif path == "/api/runs":
                    out = db.runs(ival("limit", 10, 1, 100), ival("offset", 0, 0, 10**7))
                elif path == "/api/theme":
                    out = {"theme": get_theme()}
                elif path == "/api/appearance":
                    out = APPEARANCE.get()
                elif path == "/api/health":
                    tasks = (_cache["data"] or {}).get("tasks")
                    if not tasks:
                        tasks = (db.get_json("tasks_snapshot") or {}).get("tasks") or []
                    out = health.overview(native_metrics(), tasks)
                    out["shell"] = shell_status()
                else:
                    out = db.info()
                self._send(200, out)
            except Exception as e:
                self._send(500, {"error": str(e)[:200]})
        elif path == "/api/maintenance":
            if not self._authed():
                return
            self._send(200, maintenance_state())
        elif path == "/api/cpu":
            if not self._authed():
                return
            try:
                self._send(200, cpuwatch.api_payload())
            except Exception:
                errlog.swallowed()
                self._send(500, {"error": "cpu data unavailable"})
        elif path == "/api/gpu":
            if not self._authed():
                return
            try:
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                self._send(200, gpuwatch.api_payload(q.get("range", "24h")))
            except Exception:
                errlog.swallowed()
                self._send(500, {"error": "gpu data unavailable"})
        elif path in ("/api/mem", "/api/procs", "/api/wsl", "/api/power", "/api/advice", "/api/alerts"):
            if not self._authed():
                return
            try:
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                if path == "/api/mem":
                    dm = (_cache["data"] or {}).get("metrics", {})
                    out = insight.mem_payload(native_metrics(), {"claudeGB": dm.get("claudeGB"), "wslGB": dm.get("wslGB")}, q.get("range", "24h"))
                elif path == "/api/procs":
                    out = insight.procs_payload()
                elif path == "/api/wsl":
                    out = insight.wsl_payload(q.get("range", "6h"))
                elif path == "/api/power":
                    out = insight.power_payload(q.get("range", "24h"))
                elif path == "/api/alerts":
                    out = insight.alerts_payload()
                else:
                    dm = (_cache["data"] or {}).get("metrics", {})
                    out = insight.advice_payload(native_metrics(), {"claudeGB": dm.get("claudeGB"), "wslGB": dm.get("wslGB")}, maintenance_state(), _trim_view())
                self._send(200, out)
            except Exception:
                errlog.swallowed()
                self._send(500, {"error": "data unavailable"})
        elif path == "/api/io":
            if not self._authed():
                return
            try:
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                self._send(200, diskio_api.payload(db, q.get("range", "24h"), q.get("dev", "disk0")))
            except Exception:
                errlog.swallowed()
                self._send(500, {"error": "io data unavailable"})
        elif path == "/api/home":
            if not self._authed():
                return
            if homeapi is None:   # module failed to import: answer clearly instead of hanging the request
                self._send(503, {"error": "home module unavailable"})
                return
            try:
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                self._send(200, homeapi.payload(db, q.get("range", "1h")))
            except Exception:
                errlog.swallowed()
                self._send(500, {"error": "home data unavailable"})
        elif path == "/api/reliab":
            if not self._authed():
                return
            try:
                self._send(200, reliab.snapshot(db))
            except Exception:
                errlog.swallowed()
                self._send(500, {"error": "reliability data unavailable"})
        elif path == "/api/mini":
            if not self._authed():
                return
            if _cache["data"] is None:
                threading.Thread(target=lambda: collect(True), daemon=True).start()
            self._send(200, mini_payload())
        else:
            self._send(404, {"error": "not found"})

    def _needs_confirm(self, route, body):
        """True (and a 428 already sent) when this destructive request has no valid one-time confirmation token."""
        key = _confirm_key(route, body)
        tok = body.get("confirm")
        if isinstance(tok, str) and _confirm_take(key, tok):
            return False
        self._send(428, {"error": "confirm_required", "confirm": _confirm_issue(key), "ttl": CONFIRM_TTL})
        return True

    def _space_confirmed(self, route, body, act, kind):
        """Space cleanups: the token is also bound to the preview the user saw (seen_mb, part of the confirm key).
        The dry-run is repeated after the token is accepted; a candidate set that grew past the tolerance is refused."""
        seen = body.get("seen_mb")
        if isinstance(seen, bool) or not isinstance(seen, (int, float)) or not 0 <= seen <= 1e12 or not math.isfinite(seen):
            self._send(400, {"error": "seen_mb required"})
            return False
        if self._needs_confirm(route, body):
            return False
        cur = sum(space.clean(k, dry=True).get("mb", 0) for k in (space.BULK if act == "cleanall" else [kind]))
        if cur > seen * 1.25 + 50:
            self._send(409, {"error": "candidates_changed", "seen_mb": seen, "now_mb": round(cur, 1)})
            return False
        return True

    def do_POST(self):
        if not self._guard() or not self._authed():
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._send(415, {"error": "json only"})
            return
        org = self.headers.get("Origin")   # browsers always send it on cross-site POSTs; the widget and scripts send none
        if org is not None and org not in ("http://127.0.0.1:%d" % PORT, "http://localhost:%d" % PORT):
            self._send(403, {"error": "bad origin"})
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                return self._send(400, {"error": "transfer encoding unsupported"})
            raw_length = self.headers.get("Content-Length", "0")
            if not raw_length.isascii() or not raw_length.isdecimal():
                self.close_connection = True
                return self._send(400, {"error": "invalid content length"})
            n = int(raw_length)
            if n > MAX_POST_BYTES:
                self.close_connection = True
                return self._send(413, {"error": "request too large"})
            raw = self.rfile.read(n)
            if len(raw) != n:
                self.close_connection = True
                return self._send(400, {"error": "incomplete request"})
            body = json.loads(raw or b"{}", parse_constant=_reject_json_constant)
            _validate_json_numbers(body)
            if not isinstance(body, dict):
                return self._send(400, {"error": "object expected"})
            route = self.path.split("?")[0]
            if route == "/api/appearance":
                try:
                    result = APPEARANCE.update(body)
                except appearance.AppearanceConflict as exc:
                    return self._send(409, {"error": "appearance changed", "appearance": exc.current})
                except appearance.AppearanceUnavailable:
                    return self._send(409, {"error": "appearance recovery required"})
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})
                return self._send(200, {"ok": True, **result})
            if route == "/api/session/logout":
                _session_revoke(self._cookie("mw_s"))
                return self._send(200, {"ok": True}, cookie="mw_s=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            if route == "/api/jobs/run":
                name = str(body.get("name", ""))
                if sched.next_run(name):
                    sched.set_next(name, time.time())
                    self._send(200, {"ok": True})
                else:
                    self._send(404, {"error": "unknown job"})
                return
            if route == "/api/space":
                act, kind = body.get("action"), body.get("kind")
                if act == "scan":
                    threading.Thread(target=space.scan, daemon=True).start()
                    if not space.state.get("big"):
                        space.state["big"] = True
                        threading.Thread(target=lambda: (space.scan_big(), space.state.update(big=False)), daemon=True).start()
                    self._send(200, {"ok": True})
                elif act == "qrestore":
                    self._send(200, space.quarantine_restore(int(body.get("id", 0))))
                elif act == "auto":
                    self._send(200, {"ok": space.set_auto(kind, bool(body.get("on")))})
                elif act == "report":
                    import extras
                    self._send(200, {"ok": extras.report_start()})
                elif act == "dismmonthly":
                    space._setting_set("space.dism_monthly", bool(body.get("on")))
                    self._send(200, {"ok": True})
                elif act == "elevated":
                    self._send(200, {"ok": space.set_elevated(kind, bool(body.get("on")))})
                elif act in ("cleanall", "clean") and not self._space_confirmed(route, body, act, kind):
                    return
                elif act == "cleanall":
                    out = [dict(kind=k, **space.clean(k, dry=False, src="manual")) for k in space.BULK]
                    self._send(200, {"ok": True, "mb": round(sum(o.get("mb", 0) for o in out), 1), "parts": out})
                elif act in ("preview", "clean"):
                    self._send(200, space.clean(kind, dry=(act == "preview"), early=(act == "clean" and kind == "winold" and body.get("early") is True)))
                else:
                    self._send(400, {"error": "bad action"})
                return
            if route == "/api/widget":
                want = bool(body.get("show"))
                if want:
                    if widget_running():
                        widget_stop(); time.sleep(0.8)
                    widget_fit_on_screen()
                    widget_start()
                    for _ in range(30):
                        time.sleep(0.4)
                        if widget_running() and widget_visible():
                            break
                else:
                    widget_stop(); time.sleep(0.5)
                infra.log.info("widget %s -> running=%s", "show" if want else "hide", widget_running())
                return self._send(200, {"running": widget_running() and widget_visible()})
            if route == "/api/power-plan":
                plans, err = insight.power_set(body.get("guid"))
                if err:
                    return self._send(400, {"error": err})
                return self._send(200, {"plans": plans})
            if route == "/api/reaper-set":
                key, val = body.get("key"), body.get("value")
                if key != "bareClaudeShellMinutes" or isinstance(val, bool) or not isinstance(val, int) or not 60 <= val <= 2880:
                    return self._send(400, {"error": "not allowed"})
                with edit_json(MCCFG) as (bom, cfg):
                    cfg.setdefault("reaper", {})[key] = val

                return self._send(200, {"bareMin": val})
            if route == "/api/reaper-run":
                dry = body.get("dry")
                if not isinstance(dry, bool):
                    return self._send(400, {"error": "dry must be boolean"})
                if not dry and self._needs_confirm(route, body):
                    return
                return self._send(200, insight.reaper_run(dry, os.path.dirname(MCCFG)))
            if route == "/api/reaper-protect":
                import re as _re
                nm, op = body.get("name"), body.get("op")
                if op not in ("add", "remove") or not isinstance(nm, str) or not _re.match(r"^[A-Za-z0-9_.\- ]{1,64}$", nm.strip()):
                    return self._send(400, {"error": "bad name"})
                nm = nm.strip().replace(".", "\\.")  # entries are regex fragments joined with | by the reaper
                with edit_json(MCCFG) as (bom, cfg):
                    lst = cfg.setdefault("reaper", {}).setdefault("protect", [])
                    if op == "add" and nm.lower() not in [x.lower() for x in lst]:
                        lst.append(nm)
                    elif op == "remove":
                        lst[:] = [x for x in lst if x.lower() != nm.lower()]

                return self._send(200, {"protect": lst})
            if route == "/api/alerts":
                cfg, err = insight.alerts_set(body)
                if err:
                    return self._send(400, {"error": err})
                return self._send(200, {"cfg": cfg})
            if route == "/api/maintenance":
                on = body.get("on")
                if not isinstance(on, bool):
                    return self._send(400, {"error": "on must be boolean"})
                return self._send(200, maintenance_set(on))
            if route == "/api/explain-errors":
                import explain
                r = explain.errors(native_metrics(), ROOT)
                return self._send(200, r)
            if route == "/api/llm-unload":
                import explain
                r = explain.unload()
                return self._send(200, r) if r.get("ok") else self._send(503, {"error": r.get("err", "failed")})
            if route == "/api/explain":
                import explain
                tasks = (_cache["data"] or {}).get("tasks") or (db.get_json("tasks_snapshot") or {}).get("tasks") or []
                m = native_metrics()
                extra = ["الذاكرة: التزام %s%% ومتاح %s GB" % (m.get("commitPct"), m.get("availGB"))]
                try:
                    extra += ["أكبر العمليات: %s ×%s بذاكرة خاصة %s MB" % (x["name"], x["n"], round(x["private_mb"] or 0)) for x in health.top_procs(5)]
                    extra += ["تسرّب محتمل: %s من %s إلى %s MB" % (x["name"], x["fromMB"], x["toMB"]) for x in health.leaks()[:3]]
                    extra += ["مهمة فاشلة: %s (النتيجة %s)" % (x.get("label"), x.get("result")) for x in tasks if x.get("enabled") and not x.get("missing") and x.get("result") not in (0, 267009, 267011, 267014, 267008, 267010, 2147946720, None)][:4]
                except Exception:
                    errlog.swallowed()
                r = explain.health(health.score(m, tasks)["parts"], m, extra)
                return self._send(200, r) if r.get("ok") else self._send(503, {"error": r.get("err", "failed")})
            if route == "/api/trim-auto":
                on = body.get("on")
                if not isinstance(on, bool):
                    return self._send(400, {"error": "on must be boolean"})
                set_trim_auto(on)
                return self._send(200, {"ok": True, "auto": on})
            if route == "/api/ticket":
                return self._send(200, {"ticket": _issue_ticket()})
            if route == "/api/shell":   # زر الويدجت يبلّغ محرك النافذة حين يسقط إلى Edge مباشرة؛ غير هدّام
                code, payload = record_shell_report(body)
                return self._send(code, payload)
            if route == "/api/shutdown":
                infra.log.info("shutdown requested")
                try:
                    db.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    errlog.swallowed()
                self._send(200, {"ok": True})
                threading.Timer(0.4, lambda: os._exit(0)).start()
                return
            if route == "/api/task":
                op, path, name = body.get("op"), body.get("path"), body.get("name")
                entry = WHITELIST.get((path, name))
                if not entry or op not in ("enable", "disable", "run"):
                    return self._send(400, {"error": "not allowed"})
                if entry.get("needsAdmin"):
                    return self._send(400, {"error": "هذه المهمة تحتاج صلاحية مدير لتغييرها"})
                if op == "run" and not entry.get("canRun"):
                    return self._send(400, {"error": "run not allowed for this task"})
                task_op(op, path, name)
            elif route == "/api/startup":
                return self._send(*startup_mgr.toggle(body))
            elif route == "/api/rule":
                rid, en = body.get("id"), body.get("enabled")
                if not isinstance(en, bool):
                    return self._send(400, {"error": "enabled must be boolean"})
                with edit_json(POLICY) as (bom, pol):
                    hit = [r for r in pol["rules"] if r["id"] == rid]
                    if not hit:
                        return self._send(400, {"error": "unknown rule"})
                    hit[0]["enabled"] = en

                with db._lock:  # every rule toggle goes to the action log
                    _c = db.conn(); _c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)", (int(__import__("time").time()), "rule", str(rid), 1, "enabled=" + str(en).lower())); _c.commit()
            elif route == "/api/tier-pause":
                tier, paused = body.get("tier"), body.get("paused")
                if tier not in TIERS or not isinstance(paused, bool):
                    return self._send(400, {"error": "bad request"})
                with edit_json(POLICY) as (bom, pol):
                    cur = [t for t in pol.get("pausedTiers", []) if t in TIERS and t != tier]
                    if paused:
                        cur.append(tier)
                    pol["pausedTiers"] = cur

                _extra["paused"] = cur
            elif route == "/api/theme":
                t = body.get("theme")
                if t not in ("auto", "light", "dark"):
                    return self._send(400, {"error": "bad theme"})
                try:
                    result = set_theme(t)
                except appearance.AppearanceUnavailable:
                    return self._send(409, {"error": "appearance recovery required"})
                return self._send(200, {"ok": True, "theme": result["mode"], "appearance": result})
            elif route == "/api/trim-memory":
                if _trim["running"]:
                    return self._send(409, {"error": "trim already running"})
                mode = body.get("mode") if isinstance(body, dict) else None
                threading.Thread(target=trim_memory, args=(mode or "light",), daemon=True).start()
            elif route == "/api/reaper":
                key, val = body.get("key"), body.get("value")
                if key != "killBareClaudeShells" or not isinstance(val, bool):
                    return self._send(400, {"error": "not allowed"})
                with edit_json(MCCFG) as (bom, cfg):
                    cfg["reaper"][key] = val

            elif route == "/api/run-cleanup":
                tier, dry = body.get("tier"), body.get("dry", True)
                if not isinstance(dry, bool):
                    return self._send(400, {"error": "dry must be boolean"})
                if tier not in TIERS:
                    return self._send(400, {"error": "bad tier"})
                if not dry and self._needs_confirm(route, body):
                    return
                p = _manual["proc"]
                if p and p.poll() is None:
                    return self._send(409, {"error": "a manual cleanup is already running"})
                engine, err = _resolve_engine()
                if err:
                    return self._send(503, {"error": err})
                args = [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", engine, "-Tier", tier, "-Force", "-PolicyPath", POLICY]
                if dry:
                    args.append("-DryRun")
                _manual.update(proc=subprocess.Popen(args, creationflags=CNW), tier=tier, dry=dry, started=time.strftime("%Y-%m-%dT%H:%M:%S"))
            else:
                return self._send(404, {"error": "not found"})
            _cache["t"] = 0
            self._send(200, {"ok": True})
        except (socket.timeout, TimeoutError):
            self.close_connection = True
            self._send(408, {"error": "request timed out"})
        except (ValueError, TypeError, UnicodeError, RecursionError):
            self._send(400, {"error": "invalid request data"})
        except Exception:
            request_id = secrets.token_hex(6)
            infra.log.exception("POST failed request_id=%s route=%s", request_id, self.path.split("?")[0])
            self._send(500, {"error": "internal error", "request_id": request_id})


def watchdog():
    while True:
        time.sleep(20)
        if IDLE_EXIT_SECONDS and time.time() - _last_request > IDLE_EXIT_SECONDS:
            os._exit(0)


def _work_area():
    """منطقة العمل الفعلية للشاشة الأساسية بالبكسل الفعلي (بدون شريط المهام)."""
    import ctypes, ctypes.wintypes as wt
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI: الأرقام بالبكسل الفعلي
    except Exception:
        pass
    r = wt.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right - r.left, r.bottom - r.top


TITLEBAR_RESERVE = 40  # شريط عنوان النافذة يُضاف فوق المقاس فيلزم طرحه حتى لا تغطي شريط المهام
WIDGET_RESERVE = 490  # عرض الويدجت (372 وحدة منطقية) مع هامش، بالبكسل الفعلي


def _place_window(title, x, y, w, h):
    """Place only the exact dashboard title owned by Edge, never a WPF widget."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.EnumWindows.argtypes = (CB, wintypes.LPARAM)
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.MoveWindow.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL)
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    def owned_by_edge(hwnd):
        pid = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            return False
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            image = ctypes.create_unicode_buffer(32768)
            count = wintypes.DWORD(len(image))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(count)):
                return False
            return os.path.basename(image.value).casefold() == "msedge.exe"
        finally:
            kernel32.CloseHandle(handle)

    for _ in range(40):
        time.sleep(0.15)
        found = []
        def cb(hwnd, _l):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if buf.value == title and owned_by_edge(hwnd):
                    found.append(hwnd)
                    return False
            return True
        user32.EnumWindows(CB(cb), 0)
        if found:
            # Explicit Open must restore a previously minimized application.
            # MoveWindow alone leaves that window minimized and appears to do nothing.
            user32.ShowWindow(found[0], 9)  # SW_RESTORE
            user32.MoveWindow(found[0], x, y, w, h, True)
            user32.SetForegroundWindow(found[0])
            return


def _ticket_for_window():
    """Ask the running server to issue its ticket, even from a --open helper.

    A helper process has a different in-memory ticket store. Issuing a ticket
    locally there opens an unauthenticated dashboard beside a healthy backend.
    """
    import urllib.request
    request = urllib.request.Request(
        "http://127.0.0.1:%d/api/ticket" % PORT, data=b"{}", method="POST",
        headers={"X-Token": TOKEN, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        value = json.load(response)
    ticket = value.get("ticket") if isinstance(value, dict) else None
    if not isinstance(ticket, str) or not ticket:
        raise RuntimeError("The running server did not issue a dashboard ticket")
    return ticket


SHELL_STATE_PATH = os.path.join(os.environ.get("LOCALAPPDATA", ROOT), "MonitorWidget", "shell_state.json")
SHELL_ENGINES = ("webview2", "edge", "browser")                     # من يكتب الحالة: open_window هنا، app_shell.ps1، وزر الويدجت
SHELL_REASONS = ("", "no_shell", "no_webview2", "init_failed")       # مفاتيح ثابتة تفسّرها صفحة الصحة


def _shell_readiness():
    """(ready, reason): هل غلاف app_shell.ps1 ومكتبات WebView2 متوفران؟ وإلا نرجع إلى Edge --app. السبب مفتاح ثابت للواجهة."""
    if not os.path.exists(os.path.join(ROOT, "app_shell.ps1")):
        return False, "no_shell"
    base = os.environ.get("LOCALAPPDATA", "")
    if os.path.exists(os.path.join(base, "MonitorWidget", "webview2", "Microsoft.Web.WebView2.Wpf.dll")):
        return True, ""
    teams = os.path.join(base, "Microsoft", "TeamsMeetingAdd-in")
    try:
        if any(os.path.exists(os.path.join(teams, v, "x64", "Microsoft.Web.WebView2.Wpf.dll")) for v in os.listdir(teams)):
            return True, ""
    except OSError:
        pass
    return False, "no_webview2"


def _shell_ready():
    return _shell_readiness()[0]


def _record_shell(engine, reason=""):
    """يسجّل محرك آخر فتح للنافذة (webview2 / edge / browser) في ملف صغير؛ عملية --open المساعدة تنتهي فورًا فلا تكفي الذاكرة."""
    try:
        os.makedirs(os.path.dirname(SHELL_STATE_PATH), exist_ok=True)
        atomic_json.write(SHELL_STATE_PATH, False, {"engine": engine, "reason": reason, "ts": int(time.time())})
    except Exception:
        infra.log.exception("shell state not recorded")


def shell_status():
    """حالة الغلاف لصفحة الصحة: الجاهزية الآن وسببها، ومحرك آخر فتح. fallback=True حين لا يعمل WebView2."""
    ready, reason = _shell_readiness()
    last = {}
    try:
        last = atomic_json.read(SHELL_STATE_PATH)[1] or {}
    except Exception:
        last = {}
    engine = last.get("engine") if isinstance(last, dict) else None
    last_reason = (last.get("reason") if isinstance(last, dict) else "") or ""
    detail = ""
    if last_reason == "init_failed":   # الغلاف كتب الاستثناء في shell_error.log بجانب ملف الحالة؛ سطره الأول يكفي الصفحة
        try:
            with open(os.path.join(os.path.dirname(SHELL_STATE_PATH), "shell_error.log"), encoding="utf-8-sig", errors="replace") as fh:
                detail = fh.read(400).strip()
        except OSError:
            detail = ""
    return {"ready": ready, "reason": reason, "engine": engine, "lastReason": last_reason, "detail": detail,
            "ts": (last.get("ts") if isinstance(last, dict) else None), "fallback": (not ready) or (engine in ("edge", "browser"))}


def record_shell_report(body):
    """POST /api/shell من الويدجت: (رمز الحالة، الردّ). محرك وسبب من القائمة البيضاء فقط، ثم حالة الغلاف المحدّثة."""
    engine, reason = body.get("engine"), body.get("reason", "")
    if engine not in SHELL_ENGINES or reason not in SHELL_REASONS:
        return 400, {"error": "unknown engine or reason"}
    _record_shell(engine, reason)
    return 200, {"ok": True, "shell": shell_status()}


def open_window():
    time.sleep(0.8)
    url = "http://127.0.0.1:%d/?k=%s" % (PORT, _ticket_for_window())
    try:
        x, y, w, h = _work_area()
        size, pos = "%d,%d" % (max(900, w - WIDGET_RESERVE), h - TITLEBAR_RESERVE), "--window-position=%d,%d" % (x, y)
        _place_args = (x, y, max(900, w - WIDGET_RESERVE), h - TITLEBAR_RESERVE)
    except Exception:
        size, pos = "1280,1000", "--window-position=0,0"
        _place_args = (0, 0, 1280, 1000)
    ready, reason = _shell_readiness()
    if ready:
        _record_shell("webview2")
        # غلاف بلا إطار بحواف مستديرة مثل الويدجت؛ يضع نفسه بالمقاس والموضع المطلوبين فلا حاجة لـ _place_window
        sx, sy, sw, sh = _place_args
        subprocess.Popen(["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", os.path.join(ROOT, "app_shell.ps1"),
                          "-Url", url, "-X", str(sx), "-Y", str(sy), "-W", str(sw), "-H", str(sh + TITLEBAR_RESERVE)], creationflags=0x08000000)
        return
    for edge in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(edge):
            prof = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "MonitorWidget", "appwin")  # ملف تعريف منفصل: نافذة جديدة دائماً بالمقاس الصحيح
            os.makedirs(prof, exist_ok=True)
            _record_shell("edge", reason)
            subprocess.Popen([edge, "--app=" + url, "--user-data-dir=" + prof, "--window-size=" + size, pos])
            # A --open helper exits immediately afterward. Keep placement alive
            # long enough to restore the window instead of killing a daemon thread.
            threading.Thread(target=_place_window, args=("مرصد الجهاز",) + _place_args, daemon=False).start()
            return
    _record_shell("browser", reason)
    os.startfile(url)


def widget_visible():
    """False while the widget window is hidden (the process stays alive; the flag is written by widget.ps1)."""
    try:
        return json.load(open(SETTINGS, encoding="utf-8-sig")).get("visible", True) is not False
    except Exception:
        return True


def widget_running():
    import ctypes
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "Local\\Azkia-MiniWidget")
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def _widget_process_matches(process):
    """Match this checkout's direct -File invocation, never a substring in -Command."""
    import ntpath
    if not isinstance(process, dict) or str(process.get("Name", "")).casefold() not in ("pwsh.exe", "powershell.exe"):
        return False
    command = process.get("CommandLine")
    if not isinstance(command, str) or not command:
        return False
    argc = ctypes.c_int()
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = parse(command, ctypes.byref(argc))
    if not argv:
        return False
    try:
        args = [argv[i] for i in range(argc.value)]
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = (ctypes.c_void_p,)
        free.restype = ctypes.c_void_p
        free(ctypes.cast(argv, ctypes.c_void_p))
    i = 1
    while i < len(args):
        option = args[i].casefold()
        if option in ("-file", "/file"):
            if i + 1 >= len(args) or not ntpath.isabs(args[i + 1]):
                return False
            expected = ntpath.normcase(ntpath.normpath(ntpath.join(ROOT, "widget.ps1")))
            return ntpath.normcase(ntpath.normpath(args[i + 1])) == expected
        if option in ("-noprofile", "-noninteractive", "-sta", "-mta", "-nologo", "-noexit"):
            i += 1
        elif option in ("-executionpolicy", "-windowstyle", "-workingdirectory", "-inputformat", "-outputformat") and i + 1 < len(args):
            i += 2
        else:
            # Reject command/encoded-command modes and ambiguous abbreviations.
            return False
    return False


def widget_stop():
    # Query only PowerShell hosts; process command lines stay in memory and are
    # never logged. No filesystem path or untrusted string is interpolated into PS.
    query = "$ErrorActionPreference='Stop'; $rows=@(Get-CimInstance Win32_Process -Filter \"Name = 'pwsh.exe' OR Name = 'powershell.exe'\" | Select-Object Name,ProcessId,CommandLine,@{n='CreationTicks';e={$_.CreationDate.ToUniversalTime().Ticks.ToString()}}); ConvertTo-Json -InputObject $rows -Compress"
    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", query], capture_output=True, timeout=sched.remaining_timeout(20), creationflags=CNW)
    if result.returncode != 0:
        raise RuntimeError("widget process query failed")
    try:
        processes = json.loads(result.stdout.decode("utf-8-sig"))
        if isinstance(processes, dict):
            processes = [processes]
        if not isinstance(processes, list):
            raise ValueError("invalid process list")
    except (ValueError, UnicodeError, AttributeError):
        raise RuntimeError("widget process query invalid") from None
    for process in processes:
        if not _widget_process_matches(process):
            continue
        pid, ticks = process.get("ProcessId"), str(process.get("CreationTicks", ""))
        if type(pid) is not int or pid <= 0 or pid == os.getpid() or not ticks.isascii() or not ticks.isdecimal():
            raise RuntimeError("widget process identity invalid")
        # Recheck creation time so PID reuse cannot target a replacement process.
        stop = "$ErrorActionPreference='Stop'; $p=Get-CimInstance Win32_Process -Filter 'ProcessId = %d'; if($p){if($p.CreationDate.ToUniversalTime().Ticks.ToString() -ne '%s'){throw 'identity changed'}; Stop-Process -Id %d -Force -ErrorAction Stop}" % (pid, ticks, pid)
        stopped = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", stop], capture_output=True, timeout=sched.remaining_timeout(20), creationflags=CNW)
        if stopped.returncode != 0:
            raise RuntimeError("widget stop failed")


def widget_start():
    subprocess.Popen(["wscript.exe", os.path.join(ROOT, "launch_mini.vbs")], creationflags=CNW)


def widget_fit_on_screen():
    """If the saved position is off-screen, move the widget back to the top-right of the primary screen."""
    import ctypes
    w, h = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    try:
        st = json.load(open(SETTINGS, encoding="utf-8"))
    except Exception:
        st = {}
    l, t = st.get("left"), st.get("top")
    if l is None or t is None or l < 0 or t < 0 or l > w - 120 or t > h - 120:
        st["left"], st["top"] = max(0, w - 372), 0  # top-right, matches the widget default
        json.dump(st, open(SETTINGS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _timed(fn):
    def w(self, *a, **k):
        t0 = time.time()
        self._st = 200
        try:
            return fn(self, *a, **k)
        finally:
            infra.note_request(self.path.split("?")[0], getattr(self, "_st", 200), (time.time() - t0) * 1000)
    return w


_orig_send = Handler._send
def _send_rec(self, code, *a, **k):
    self._st = code
    return _orig_send(self, code, *a, **k)
Handler._send = _send_rec
Handler.do_GET = _timed(Handler.do_GET)
Handler.do_POST = _timed(Handler.do_POST)


def _maint():
    time.sleep(30)
    while True:
        try:
            pass
        except Exception:
            infra.log.exception("maintenance")
        time.sleep(3600)


_server_mutex = None


def _mark(event):
    """علامة مباشرة إلى ملف مستقل، لا تعتمد على نظام السجلات (الذي لم يصل من عملية الخادم)."""
    try:
        with open(os.path.join(infra.LOG_DIR, "server_marks.txt"), "a", encoding="utf-8") as f:
            f.write("%s %s pid=%d\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), event, os.getpid()))
    except Exception:
        pass


def _claim_single_server():
    global _server_mutex
    _server_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\Azkia-MonitorServer")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


if __name__ == "__main__":
    try:
        if not _claim_single_server():
            raise OSError("server already running")
        LocalHTTPServer.allow_reuse_address = False
        srv = LocalHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        if "--open" in sys.argv:
            open_window()  # already running: just open another window
        sys.exit(0)
    # تشخيص الانهيار: الخادم يعمل بـ pythonw بلا نافذة، فنكتب كل توقف إلى السجل وإلى ملف faulthandler
    _fault_fh = open(os.path.join(infra.LOG_DIR, "server_fault.log"), "a", encoding="utf-8")
    faulthandler.enable(file=_fault_fh, all_threads=True)
    atexit.register(lambda: (_mark("exit"), infra.log.info("server exit pid=%d", os.getpid())))
    threading.excepthook = lambda a: infra.log.error("thread %s crashed:\n%s", a.thread.name if a.thread else "?",
                                                      "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    _mark("boot")
    infra.log.info("server boot pid=%d", os.getpid())
    refresh_extra()
    c0 = db.bootstrap(); health.init()
    infra.log.info('server start pid=%d schema=%s', os.getpid(), c0.execute('PRAGMA user_version').fetchone()[0])
    ensure_agent()
    for fn in (watchdog, sampler, refresher, ingester, _maint):
        threading.Thread(target=fn, daemon=True).start()
    if "--open" in sys.argv:
        threading.Thread(target=open_window, daemon=True).start()
    try:
        srv.serve_forever()
    except BaseException:
        infra.log.exception("serve_forever stopped")
        raise
