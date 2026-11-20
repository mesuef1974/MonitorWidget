# -*- coding: utf-8 -*-
"""لقطة الحالة (البند 6/ز من الخارطة): القياسات الأصلية السريعة بلا عمليات فرعية، عيّنات الذاكرة لآخر 30 دقيقة، الجامع الثقيل
(collect.ps1) مع ذاكرته المؤقتة، الإضافات المقروءة من السياسة وسجل التشغيلات، الخيوط الخلفية (sampler/refresher/ingester)،
وحمولتا /api/state و/api/mini. يستعملها الخادم والوكيل (agent.py) والجامع المجدول (collector.py) بدل استيراد server.
العقود والسلوك كما كانت في server.py حرفيًا.

    native_metrics()           الذاكرة والالتزام والقرص وعدد العمليات (claude/pwsh/conhost) عبر Win32 مباشرة
    sampler() / refresher() / ingester()   خيوط خلفية يطلقها الخادم عند الإقلاع
    touch() / last_request()   زمن آخر طلب (watchdog)
    collect(force) / attach_manual(data) / refresh_extra()   الجامع الثقيل وإضافاته
    invalidate()               بعد تعديل ناجح من وحدة مسارات (كانت _after_mutation): تُجمع المهام عند الطلب التالي وتُقرأ المستويات الموقوفة
    tasks()                    قائمة المهام من الذاكرة المؤقتة أو من tasks_snapshot في القاعدة
    metrics_extra()            {claudeGB, wslGB} من آخر جمع ثقيل (لمسارات mem/advice)
    state()                    حمولة GET /api/state (الذاكرة المؤقتة إن كانت أحدث من 30 ث وإلا جمع)
    mini() / mini_payload()    حمولة الويدجت؛ mini يطلق الجمع الأول في خيط إن لم يُجمع بعد
"""
import collections
import ctypes
import json
import os
import shutil
import subprocess
import threading
import time
from ctypes import wintypes

import alertcfg
import cpuwatch
import battwatch
import cpuprio
import db
import errlog
import gpuwatch
import infra
import memtrim
import routes_destructive
import routes_settings
import mwsched as sched
import secure
from cfgjson import read_json
from paths import CNW, POLICY, PWSH, ROOT, RUNS, TIERS, TIER_TASK

_lock = threading.Lock()
_cache = {"t": 0, "data": None}
_first_kick = threading.Lock()   # يحرس خيط الجمع الأول الذي يطلقه mini(): واحد جارٍ فقط
_extra = {"tiers": {}, "paused": []}
_last_request = time.time()
_hist = collections.deque(maxlen=360)  # one sample / 5 s = 30 min
IS_ADMIN = secure.is_admin()


# ---------------------------------------------------------------- fast native metrics (no subprocess)
class MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD)] + [
        (n, ctypes.c_ulonglong) for n in ("tp", "ap", "tpf", "apf", "tv", "av", "aev")]


class PE32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD),
                ("heap", ctypes.c_void_p), ("mod", wintypes.DWORD), ("thr", wintypes.DWORD),
                ("ppid", wintypes.DWORD), ("pri", ctypes.c_long), ("flags", wintypes.DWORD),
                ("exe", ctypes.c_wchar * 260)]


def other_drives(system="C"):
    """الأقراص الثابتة غير قرص النظام: [{letter, freeGB, totalGB, pct}] (استعلام استخدام فقط، بلا مسح)."""
    out = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            letter = chr(65 + i)
            if not mask & (1 << i) or letter == system:
                continue
            root = letter + ":\\"
            if ctypes.windll.kernel32.GetDriveTypeW(root) != 3:   # DRIVE_FIXED
                continue
            try:
                du = shutil.disk_usage(root)
            except OSError:
                continue
            if du.total:
                out.append({"letter": letter, "freeGB": round(du.free / 1024 ** 3, 1), "totalGB": round(du.total / 1024 ** 3, 1),
                            "pct": round(100 * (du.total - du.free) / du.total, 1)})
    except OSError:
        pass
    return out


def native_metrics():
    ms = MEMSTAT()
    ms.dwLength = ctypes.sizeof(ms)
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
        pe = PE32()
        pe.dwSize = ctypes.sizeof(pe)
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
        "diskPct": round(100 * (du.total - du.free) / du.total, 1), "drives": other_drives(),
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


# ---------------------------------------------------------------- last request (watchdog)
def touch():
    global _last_request
    _last_request = time.time()


def last_request():
    return _last_request


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
    data["manual"] = routes_destructive.manual_status()   # التنظيف اليدوي تملكه وحدة المسارات الهدّامة
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
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 60000))
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


def invalidate():
    """بعد تعديل ناجح من وحدة مسارات: تُجمع حالة المهام عند الطلب التالي وتُقرأ المستويات الموقوفة من السياسة
    (كانت _after_mutation في server.py، وقبلها _cache["t"] = 0 و_extra["paused"] = ... داخل do_POST)."""
    _cache["t"] = 0
    refresh_extra()


def refresher():
    """يُبقي الذاكرة المؤقتة دافئة ما دام أحد يطلب. يحترم _collect_ttl: كل طلب يلمس _last_request (والويدجت يطلب كل 2 ث)،
    فالجمع القسري هنا كان يطلق pwsh كاملًا كل 20 ث ويُبطل الـ TTL (مراجعة 19/11/2026)."""
    while True:
        time.sleep(20)
        if time.time() - _last_request < 120:
            try:
                collect()
            except Exception:
                errlog.swallowed()


# ---------------------------------------------------------------- views over the cache
def tasks():
    """قائمة المهام من آخر جمع ثقيل، أو من tasks_snapshot في القاعدة إن لم يُجمع بعد (health/explain)."""
    out = (_cache["data"] or {}).get("tasks")
    if not out:
        out = (db.get_json("tasks_snapshot") or {}).get("tasks") or []
    return out


def metrics_extra():
    """{claudeGB, wslGB} من آخر جمع ثقيل (قد تكون None قبل أول جمع)؛ تكمّل native_metrics في mem/advice."""
    dm = (_cache["data"] or {}).get("metrics", {})
    return {"claudeGB": dm.get("claudeGB"), "wslGB": dm.get("wslGB")}


def state():
    """حمولة GET /api/state: الذاكرة المؤقتة إن كانت أحدث من 30 ث، وإلا جمع (قد يرفع استثناء: الخادم يردّ 500 collect failed)."""
    if _cache["data"] and time.time() - _cache["t"] < 30:
        return attach_manual(_cache["data"])
    return collect()


def _cpu_alert_safe():
    try:
        alert = cpuwatch.alert_state()
        if alert:   # الويدجت يعرض زر «اخفض أولويتها» لغير المحمي، و«مخفّضة» إن سبق خفضها وما زالت تعمل
            alert["canLower"] = alert["name"].lower() not in cpuprio.PROTECTED
            alert["lowered"] = cpuprio.lowered_state(alert["name"])
        return alert
    except Exception:
        return None


def _gpu_safe():
    try:
        return gpuwatch.mini_view()
    except Exception:
        errlog.swallowed()
        return None


_score_cache = {"id": None, "t": 0.0, "v": None}


def _score_safe(m):
    """درجة الصحة لرأس الويدجت: تُحسب مرة لكل لقطة جمع أو كل 60 ثانية (الويدجت يستطلع /api/mini كل بضع ثوانٍ)، وNone قبل أول لقطة."""
    data = _cache["data"]
    if not data:
        return None
    now = time.time()
    if _score_cache["id"] == id(data) and now - _score_cache["t"] < 60:
        return _score_cache["v"]
    try:
        import health
        sc = health.score(m, tasks())
        v = {"score": sc.get("score"), "status": sc.get("status"), "coveragePct": sc.get("coveragePct")}
    except Exception:
        errlog.swallowed()
        v = None
    _score_cache.update(id=id(data), t=now, v=v)
    return v


def _agent_hb_age():
    try:
        v = db._meta_get("agent_hb", 0)
        return max(0, int(time.time()) - int(v)) if v else None
    except Exception:
        return None


def mini_payload():
    data = _cache["data"]
    tiers = {}
    tasks_by_name = {t["name"]: t for t in (data or {}).get("tasks", [])}
    for t in TIERS:
        task = tasks_by_name.get(TIER_TASK[t], {})
        tiers[t] = {"paused": t in _extra["paused"], "last": _extra["tiers"].get(t), "next": task.get("next"),
                    "schedule": task.get("schedule"), "taskState": task.get("state")}
    m = native_metrics()
    h = list(_hist)
    dm = (data or {}).get("metrics", {})
    m["claudeGB"] = dm.get("claudeGB")
    m["wslGB"] = dm.get("wslGB")
    manual = routes_destructive.manual_status()
    appearance_state = routes_settings.APPEARANCE.get()
    return {
        "now": time.strftime("%Y-%m-%dT%H:%M:%S"), "m": m,
        "hist": {"t": [x[0] for x in h], "commit": [x[1] for x in h], "ram": [x[2] for x in h]},
        "tiers": tiers, "guard": (data or {}).get("guard"),
        "manual": {"running": manual["running"], "tier": manual["tier"], "dry": manual["dry"]},
        "ready": data is not None, "guardNext": memtrim.guard_next(), "trim": memtrim.view(), "theme": appearance_state["mode"], "appearance": appearance_state, "cpuAlert": _cpu_alert_safe(), "battAlert": battwatch.mini(),
        "admin": IS_ADMIN, "agentAge": _agent_hb_age(), "cpuNow": cpuwatch.now_total(), "gpu": _gpu_safe(), "thr": alertcfg.get(), "score": _score_safe(m),
    }


def mini():
    """حمولة GET /api/mini: إن لم يُجمع بعد يُطلق الجمع الأول في خيط ويُعاد الردّ فورًا بـ ready=false."""
    if _cache["data"] is None and _first_kick.acquire(blocking=False):
        # جمع أول واحد جارٍ فقط: الويدجت يطلب كل 2 ث، وجامع بطيء أو معطّل كان يراكم خيطًا وpwsh لكل طلب
        try:
            threading.Thread(target=_first_collect, daemon=True, name="first-collect").start()
        except Exception:
            _first_kick.release()
            raise
    return mini_payload()


def _first_collect():
    try:
        collect()   # لا قسر: إن سبقه جمع آخر فالذاكرة المؤقتة تكفي
    except Exception:
        errlog.swallowed()
    finally:
        _first_kick.release()
