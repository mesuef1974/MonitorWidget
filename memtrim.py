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
    trim_memory()                            مرور واحد: light وحده (purge مرتفعة لصفحات الانتظار منخفضة الأولوية)؛ البند 24 من
                                             المجلس 19/11 حذف medium وdeep والتفريغ الشامل لكل العمليات
"""
import ctypes
import json
import os
import subprocess
import threading
import time

import db
import errlog
import mwsched as sched
import secure
from paths import CNW, ROOT

ROUTES = ("/api/trim-memory", "/api/trim-auto")
AUTO_PERIOD = 600
PURGE_TASK = "MemoryPurge-OnDemand"
PURGE_LOCK = os.path.join(ROOT, "purge.lock")

TRIM = {"running": False, "freedMB": None, "ts": None, "err": None, "before": None, "after": None, "procs": 0, "ms": 0}
_cycle = {"done": time.time()}
# مرور واحد في العملية: يُؤخذ القفل قبل بدء الخيط (كان علم running يُرفع داخل الخيط فتمرّ نقرتان متتاليتان معًا)
_run_lock = threading.Lock()


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
    mode 'light' only: low-priority standby pages; active working sets untouched."""
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


def trim_memory(mode="light", held=False):
    """One pass only (the old extra passes re-faulted pages and froze the machine). mode is accepted for old callers and ignored:
    only light remains (item 24 of the 19/11 council: purging all standby pages or every working set throws away cache that
    Windows counts as available anyway and makes it re-read from disk).
    held=True: المستدعي أخذ _run_lock بنفسه (مسار الزر). وإلا يُؤخذ هنا بلا انتظار؛ إن كان مرور جاريًا يعود False دون مسّ حالته."""
    if not held and not _run_lock.acquire(blocking=False):
        return False
    mode = "light"
    TRIM.update(running=True, err=None, mode=mode)
    t0 = time.time()
    try:
        res = _purge_task(mode)
        if not res:
            raise RuntimeError("purge task not installed")
        TRIM.update(freedMB=res.get("freedMB"), before=res.get("beforePct"), after=res.get("afterPct"), procs=0, mode=mode,
                    ms=int((time.time() - t0) * 1000), ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
    except Exception as e:
        TRIM.update(err=str(e)[:200])
    finally:
        TRIM["running"] = False
        _cycle["done"] = time.time()
        _run_lock.release()
        try:
            sched.set_next("trim", time.time() + AUTO_PERIOD)
        except Exception:
            errlog.swallowed()
    try:
        if TRIM.get("ts") and not TRIM.get("err"):
            db.add_trim(time.time(), TRIM.get("before"), TRIM.get("after"), TRIM.get("freedMB"), TRIM.get("ms"), TRIM.get("mode"))
    except Exception:
        errlog.swallowed()
    return True


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
        if TRIM["running"] or not _run_lock.acquire(blocking=False):
            return 409, {"error": "trim already running"}
        TRIM["running"] = True   # يُرفع قبل بدء الخيط فتراه الحالة فورًا
        try:   # mode في الطلب يُتجاهل: light وحده (البند 24)
            threading.Thread(target=trim_memory, args=("light", True), daemon=True).start()
        except BaseException:
            TRIM["running"] = False
            _run_lock.release()
            raise
        invalidate()
        return 200, {"ok": True}
    return None
