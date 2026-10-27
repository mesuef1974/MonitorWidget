# -*- coding: utf-8 -*-
"""تنظيف الذاكرة (البند 6/ب من الخارطة): حالة آخر تنظيف وعرضها، مهمة purge المرتفعة الثابتة، التنظيف البارد والمتوسط والعميق،
مفتاح التنظيف التلقائي trim.auto، والعدّ التنازلي للتنظيف التالي، مع POST /api/trim-memory و/api/trim-auto.
يستعمله الخادم (mini_payload وadvice) والوكيل (مهمة trim). العقود والسلوك كما كانت في server.py حرفيًا.

    ROUTES / post(route, body, invalidate)   المساران POST؛ post يعيد (رمز الحالة، الردّ) أو None إن لم يكن المسار من هنا
    TRIM                                     حالة آخر تنظيف داخل العملية (كانت _trim في server.py)
    AUTO_PERIOD                              مدة الدورة التلقائية بالثواني
    auto_trim_on() / set_trim_auto(on)       مفتاح الإعداد trim.auto في جدول settings
    guard_next()                             العدّ التنازلي للتنظيف التلقائي التالي (مهمة trim في الوكيل)
    view()                                   حالة التنظيف للواجهة مدمجة مع أحدث صف في جدول trims (كانت _trim_view)
    trim_memory(mode)                        مرور واحد: light (purge مرتفعة) / medium (بارد + standby) / deep (مجموعات العمل)
"""
import ctypes
import json
import os
import subprocess
import threading
import time
from ctypes import wintypes

import db
import errlog
import sched
import secure
from paths import CNW, ROOT

ROUTES = ("/api/trim-memory", "/api/trim-auto")
AUTO_PERIOD = 600
PURGE_TASK = "MemoryPurge-OnDemand"
PURGE_LOCK = os.path.join(ROOT, "purge.lock")
COLD_NAMES = {"node.exe", "pwsh.exe", "cmd.exe"}   # background shells and dev servers only; never browsers, editors, Claude
COLD_MAX_PROCS = 20

TRIM = {"running": False, "freedMB": None, "ts": None, "err": None, "before": None, "after": None, "procs": 0, "ms": 0}
_cycle = {"done": time.time()}


class _PE(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_void_p),
                ("mod", wintypes.DWORD), ("thr", wintypes.DWORD), ("ppid", wintypes.DWORD), ("pri", wintypes.LONG),
                ("flags", wintypes.DWORD), ("exe", ctypes.c_wchar * 260)]


class _MS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong), ("c", ctypes.c_ulonglong)]


# ---------------------------------------------------------------- المفتاح التلقائي والعدّ التنازلي
def auto_trim_on():
    """Automatic memory trim runs only when the settings row 'trim.auto' is true. Manual trim is not affected."""
    try:
        with db._lock:
            r = db.conn().execute("SELECT value FROM settings WHERE key='trim.auto'").fetchone()
        return bool(r and json.loads(r[0]) is True)
    except Exception:
        errlog.swallowed()
        return False


def set_trim_auto(on):
    with db._lock:
        c = db.conn()
        c.execute("INSERT INTO settings(key,value,ts) VALUES('trim.auto',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts", (json.dumps(bool(on)), int(time.time())))
        c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)", (int(time.time()), "trimauto", "setting", 1, "on=" + str(bool(on)).lower()))
        c.commit()


def guard_next():
    """Countdown to the next automatic trim: the agent's `trim` job (restarts after every trim, manual or automatic)."""
    if TRIM["running"]:  # countdown stops while a trim runs; it restarts when the trim ends (trim_memory sets the next run)
        return {"next": None, "period": AUTO_PERIOD, "running": True, "auto": auto_trim_on()}
    if not auto_trim_on():
        return {"next": None, "period": AUTO_PERIOD, "running": False, "auto": False}
    r = sched.next_run("trim")
    nxt = r["next_run"] if r and r["next_run"] else _cycle["done"] + AUTO_PERIOD
    running = bool(TRIM["running"] or (r and r["last_start"] and (not r["last_end"] or r["last_end"] < r["last_start"]) and time.time() - r["last_start"] < 300))
    return {"next": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(nxt)), "period": AUTO_PERIOD, "running": running}


def view():
    """Trim state for the UI: the in-process result merged with the newest row in the DB (the agent may have trimmed)."""
    t = dict(TRIM)
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


# ---------------------------------------------------------------- القياس والتنظيف داخل العملية
def _mem_status():
    m = _MS()
    m.l = ctypes.sizeof(_MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ap // (1024 * 1024), m.load


def _trim_all_working_sets():
    """PC-Manager style: EmptyWorkingSet on every process we may open (same user; no admin). Pages move to standby = available."""
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.OpenProcess.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    pe = _PE()
    pe.dwSize = ctypes.sizeof(_PE)
    n, me = 0, os.getpid()
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


def _visible_pids():
    """PIDs that own a visible top-level window (foreground or background apps with a window are never trimmed)."""
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
    class FT(ctypes.Structure):
        _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]
    c, e, k, u = FT(), FT(), FT(), FT()
    if not ctypes.windll.kernel32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
        return None

    def tot(f):
        return (f.hi << 32) | f.lo
    return (tot(k) + tot(u)) / 10000.0


def _trim_cold():
    """Empty the working set of background shells that have no window and used almost no CPU over a 2 s sample.
    Their pages move to standby; nothing is purged from the standby list, so no system cache is thrown away."""
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.OpenProcess.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(2, 0)
    pe = _PE()
    pe.dwSize = ctypes.sizeof(_PE)
    me, cands = os.getpid(), []
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
    TRIM.update(freedMB=a_mb - b_mb, before=b_load, after=a_load, procs=done, mode="medium", ms=ms, ts=ts, err=None,
                candidates=cands, idle=idle)
    try:
        db.add_trim(time.time(), b_load, a_load, a_mb - b_mb, ms, "medium")
    except Exception:
        errlog.swallowed()


# ---------------------------------------------------------------- مهمة purge المرتفعة الثابتة
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
    TRIM.update(running=True, err=None, mode=mode)
    t0 = time.time()
    try:
        if mode == "medium":
            _trim_medium(t0)
            return
        res = _purge_task(mode)
        if res:
            TRIM.update(freedMB=res.get("freedMB"), before=res.get("beforePct"), after=res.get("afterPct"), procs=0, mode=mode,
                        ms=int((time.time() - t0) * 1000), ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
        elif mode == "deep":
            b_mb, b_load = _mem_status()
            n = _trim_all_working_sets()
            time.sleep(0.6)
            a_mb, a_load = _mem_status()
            TRIM.update(freedMB=a_mb - b_mb, before=b_load, after=a_load, procs=n, mode="user",
                        ms=int((time.time() - t0) * 1000), ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
        else:
            raise RuntimeError("purge task not installed")
    except Exception as e:
        TRIM.update(err=str(e)[:200])
    finally:
        TRIM["running"] = False
        _cycle["done"] = time.time()
        try:
            sched.set_next("trim", time.time() + AUTO_PERIOD)
        except Exception:
            errlog.swallowed()
    try:
        if TRIM.get("ts") and not TRIM.get("err"):
            db.add_trim(time.time(), TRIM.get("before"), TRIM.get("after"), TRIM.get("freedMB"), TRIM.get("ms"), TRIM.get("mode"))
    except Exception:
        errlog.swallowed()


# ---------------------------------------------------------------- المسارات
def post(route, body, invalidate=lambda: None):
    """(رمز الحالة، الردّ) لمسار من ROUTES، أو None. invalidate تُستدعى بعد بدء تنظيف يدوي كما كان _cache["t"] = 0."""
    if route == "/api/trim-auto":
        on = body.get("on")
        if not isinstance(on, bool):
            return 400, {"error": "on must be boolean"}
        set_trim_auto(on)
        return 200, {"ok": True, "auto": on}
    if route == "/api/trim-memory":
        if TRIM["running"]:
            return 409, {"error": "trim already running"}
        mode = body.get("mode") if isinstance(body, dict) else None
        threading.Thread(target=trim_memory, args=(mode or "light",), daemon=True).start()
        invalidate()
        return 200, {"ok": True}
    return None
