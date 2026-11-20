"""صحة المرصد نفسه وميزانيته (البندان 21 و22 من المجلس 19/11).

الصحة: عمر آخر نسخة خارجية، وعمر آخر تشغيل لكل جامع مجدول في الوكيل (جدول jobs)، وآخر فشل مهمة مجدولة (أحداث task/bad)،
وعدد الاستثناءات المبتلعة اليوم في server.log. الميزانية: ذاكرة كل عملية من عمليات المرصد (الخادم والوكيل والويدجت والغلاف
ومعها عملياتها الفرعية) ومعالجها ومقابضها وعدد الفرعيات، بحدود ثابتة؛ تجاوزها ثلاث عيّنات متتالية يكتب حدثًا (مرة كل 6 ساعات).

الدوال النقية تأخذ بياناتها حقنًا فتُختبر بلا نظام ولا قاعدة: collectors / count_swallowed / budget / summarize.
view() هي ما يقرؤه الخادم: مخزّنة 60 ثانية (الويدجت يستطلع /api/mini كل ثانيتين)، ولا ترمي أبدًا.

    view()          -> {"level", "text", "items": [...], "budget": {...}}   لصفحة «نظرة عامة»
    mini()          -> {"level", "text"}                                    لرأس الويدجت
"""
import ctypes
import os
import re
import threading
import time
from ctypes import wintypes

import errlog

TTL = 60
EXT_TTL = 600            # مجلد النسخ الخارجي قد يكون قرصًا خارجيًا بطيئًا: يُفحص كل 10 دقائق
TASK_FAIL_WINDOW = 24 * 3600
SWALLOWED_WARN = 200     # في اليوم؛ المجلس رأى 296 يوم 19/11
SWALLOWED_BAD = 1000
OVER_STREAK = 3          # عيّنات متتالية فوق الميزانية قبل الحدث
EVENT_EVERY = 6 * 3600

# ميزانية كل دور: الذاكرة الخاصة (MB) للدور كله مع فرعياته، والمقابض لكل عملية، والمعالج بالأنوية للدور كله
ROLES = (("server", "الخادم", "server.py"), ("agent", "الوكيل", "agent.py"),
         ("widget", "الويدجت", "widget.ps1"), ("shell", "الغلاف", "app_shell.ps1"))
BUDGET = {
    "server": {"mb": 300, "cores": 0.25},
    "agent": {"mb": 300, "cores": 0.25},
    "widget": {"mb": 400, "cores": 0.15},
    "shell": {"mb": 700, "cores": 0.25},
}
HANDLES_MAX = 5000
CHILDREN_MAX = 25
TOTAL_MB_MAX = 1200

LEVELS = ("ok", "warn", "bad")


def _worse(a, b):
    return a if LEVELS.index(a) >= LEVELS.index(b) else b


# ------------------------------------------------------------------ نقية
def collectors(jobs, now):
    """[{name, ageS, intervalS, level, why}] من صفوف جدول jobs. الجامع متأخر إن مضى على آخر انتهاء له أكثر من
    ضعفي فاصله و10 دقائق (هامش mwsched.future_slack نفسه)، أو عالق، أو انتهى آخر تشغيل بخطأ."""
    out = []
    for j in jobs:
        iv = int(j.get("interval_s") or 0)
        start, end = j.get("last_start"), j.get("last_end")
        age = now - end if end else None
        lvl, why = "ok", None
        if start and (not end or end < start) and now - start > (j.get("timeout_s") or 600):
            lvl, why = "bad", f"عالق منذ {_dur(now - start)}"
        elif end is None:
            lvl, why = ("warn", "لم يعمل بعد") if start is None and j.get("next_run") and j["next_run"] < now - 600 else ("ok", None)
        elif iv and age > 2 * iv + 600:
            lvl, why = ("bad" if age > 6 * iv + 3600 else "warn"), f"آخر تشغيل قبل {_dur(age)}"
        elif (j.get("last_status") or "") == "error":
            lvl, why = "warn", "آخر تشغيل انتهى بخطأ"
        out.append({"name": j.get("name"), "ageS": age, "intervalS": iv, "level": lvl, "why": why})
    return out


_HEAD = re.compile(r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2},\d+ (?:[A-Z]+ )?(.*)$")   # infra يكتب المستوى قبل النص


def count_swallowed(lines, day):
    """عدد مداخل «swallowed exception» في يوم day (YYYY-MM-DD) من أسطر السجل."""
    n = 0
    for ln in lines:
        m = _HEAD.match(ln)
        if m and m.group(1) == day and m.group(2).startswith("swallowed exception"):
            n += 1
    return n


def budget(roles, prev=None, dt=None):
    """roles: {role: [{pid, mb, handles, cpu100ns}]} (العملية الجذر وفرعياتها). prev: لقطة سابقة {pid: cpu100ns} لحساب المعالج.
    -> {"level", "roles": [...], "totalMB", "over": [نصوص]}"""
    rows, over, total = [], [], 0.0
    for key, label, _script in ROLES:
        ps = roles.get(key) or []
        if not ps:
            continue
        mb = sum(p.get("mb") or 0 for p in ps)
        hmax = max((p.get("handles") or 0) for p in ps)
        kids = len(ps) - 1
        cores = None
        if prev is not None and dt and dt > 0:
            used = sum(max(0, (p.get("cpu100ns") or 0) - prev.get(p["pid"], p.get("cpu100ns") or 0)) for p in ps)
            cores = round(used / 1e7 / dt, 3)
        b = BUDGET[key]
        lvl, why = "ok", []
        if mb > b["mb"]:
            lvl = _worse(lvl, "bad" if mb > 2 * b["mb"] else "warn"); why.append(f"ذاكرته {mb:.0f} MB (الحد {b['mb']})")
        if cores is not None and cores > b["cores"]:
            lvl = _worse(lvl, "warn"); why.append("معالجه {:.2f} نواة (الحد {:.2f})".format(cores, b["cores"]))
        if hmax > HANDLES_MAX:
            lvl = _worse(lvl, "warn"); why.append(f"{hmax} مقبضًا في عملية واحدة")
        if kids > CHILDREN_MAX:
            lvl = _worse(lvl, "warn"); why.append(f"{kids} عملية فرعية")
        total += mb
        rows.append({"role": key, "label": label, "mb": round(mb), "cores": cores, "handles": hmax, "children": kids,
                     "procs": len(ps), "level": lvl, "why": "، ".join(why) or None})
        if why:
            over.append("{}: {}".format(label, "، ".join(why)))
    lvl = "ok"
    for r in rows:
        lvl = _worse(lvl, r["level"])
    if total > TOTAL_MB_MAX:
        lvl = _worse(lvl, "warn"); over.append(f"مجموع ذاكرة المرصد {total:.0f} MB (الحد {TOTAL_MB_MAX})")
    return {"level": lvl, "roles": rows, "totalMB": round(total), "over": over}


def summarize(items, bud):
    """المستوى الكلي ونصّ قصير للويدجت من البنود ونتيجة الميزانية."""
    lvl = "ok"
    bad = []
    for it in items:
        lvl = _worse(lvl, it["level"])
        if it["level"] != "ok":
            bad.append(it)
    if bud:
        lvl = _worse(lvl, bud["level"])
    bad.sort(key=lambda it: -LEVELS.index(it["level"]))
    parts = [it["text"] for it in bad]
    if bud and bud["over"]:
        parts.append(bud["over"][0])
    text = ("المرصد: " + "؛ ".join(parts[:2])) if parts else "المرصد سليم"
    return lvl, text


def _dur(s):
    s = int(max(0, s))
    if s < 3600:
        return f"{max(1, s // 60)} دقيقة"
    if s < 2 * 86400:
        return f"{s // 3600} ساعة"
    return f"{s // 86400} يومًا"


# ------------------------------------------------------------------ قراءة النظام
class _PE32(ctypes.Structure):   # PROCESSENTRY32W
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_void_p),
                ("mod", wintypes.DWORD), ("thr", wintypes.DWORD), ("ppid", wintypes.DWORD), ("pri", ctypes.c_long),
                ("flags", wintypes.DWORD), ("exe", ctypes.c_wchar * 260)]


class _PMC(ctypes.Structure):   # PROCESS_MEMORY_COUNTERS_EX
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


class _US(ctypes.Structure):   # UNICODE_STRING
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]


def _api():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
    k.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    ps = ctypes.WinDLL("psapi")
    ps.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
    nt = ctypes.WinDLL("ntdll")
    nt.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    nt.NtQueryInformationProcess.restype = ctypes.c_long
    return k, ps, nt


QUERY_LIMITED, VM_READ = 0x1000, 0x0010


def _tree():
    """[(pid, ppid, exe)] لكل العمليات."""
    k, _ps, _nt = _api()
    out = []
    snap = k.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return out
    try:
        pe = _PE32(); pe.dwSize = ctypes.sizeof(pe)
        ok = k.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            out.append((int(pe.pid), int(pe.ppid), pe.exe))
            ok = k.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k.CloseHandle(snap)
    return out


def _cmdline(h, nt):
    """سطر أوامر العملية (ProcessCommandLineInformation = 60، يكفيه مقبض استعلام محدود)، أو ""."""
    need = wintypes.ULONG(0)
    nt.NtQueryInformationProcess(h, 60, None, 0, ctypes.byref(need))
    if not need.value or need.value > 65536:
        return ""
    buf = ctypes.create_string_buffer(need.value)
    if nt.NtQueryInformationProcess(h, 60, buf, need.value, ctypes.byref(need)) != 0:
        return ""
    us = _US.from_buffer(buf)
    return ctypes.wstring_at(us.Buffer, us.Length // 2) if us.Buffer and us.Length else ""


def _stats(h, k, ps):
    m = _PMC(); m.cb = ctypes.sizeof(m)
    mb = m.PrivateUsage / 1048576.0 if ps.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb) else 0.0
    hc = wintypes.DWORD(0)
    handles = int(hc.value) if k.GetProcessHandleCount(h, ctypes.byref(hc)) else 0
    c, e, kt, ut = (ctypes.c_ulonglong() for _ in range(4))
    cpu = int(kt.value + ut.value) if k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)) else 0
    return mb, handles, cpu


def _roles(app_dir=None, me=None):
    """{role: [{pid, mb, handles, cpu100ns}]}: الجذر بسطر أوامره (السكربت داخل مجلد التطبيق نفسه)، ثم كل نسله."""
    app_dir = os.path.normcase(os.path.abspath(app_dir or os.path.dirname(os.path.abspath(__file__))))
    me = os.getpid() if me is None else me
    k, ps, nt = _api()
    tree = _tree()
    kids = {}
    for pid, ppid, _exe in tree:
        kids.setdefault(ppid, []).append(pid)
    want = {os.path.normcase(os.path.join(app_dir, script)): key for key, _l, script in ROLES}
    roots = {}
    for pid, _ppid, exe in tree:
        n = exe.lower()
        if pid == me:
            roots[pid] = "server"
            continue
        if n not in ("pythonw.exe", "python.exe", "pwsh.exe", "powershell.exe"):
            continue
        h = k.OpenProcess(QUERY_LIMITED, False, pid)
        if not h:
            continue
        try:
            cl = os.path.normcase(_cmdline(h, nt))
        finally:
            k.CloseHandle(h)
        for path, key in want.items():
            if path in cl:
                roots[pid] = key
                break
    out = {}
    for root, key in roots.items():
        seen, stack = set(), [root]
        while stack:   # النسل كله: conhost وpwsh الجمع وعمليات WebView2
            p = stack.pop()
            if p in seen or (p != root and p in roots):
                continue
            seen.add(p)
            stack.extend(kids.get(p, ()))
        for p in seen:
            h = k.OpenProcess(QUERY_LIMITED, False, p)
            if not h:
                continue
            try:
                mb, handles, cpu = _stats(h, k, ps)
            finally:
                k.CloseHandle(h)
            out.setdefault(key, []).append({"pid": p, "mb": mb, "handles": handles, "cpu100ns": cpu})
    return out


def _log_lines(root=None):
    """أسطر رؤوس «swallowed exception» من server.log وserver.log.1 (infra.LOG_DIR: الخادم والوكيل يكتبان فيه عبر مسجّل
    monitor؛ التدوير عند 1 MB فاليوم في أحدثهما غالبًا)، مخزّنة بالحجم ووقت التعديل فلا يُعاد قراءة ملف لم يتغيّر."""
    if root is None:
        import infra
        root = infra.LOG_DIR
    lines = []
    for name in ("server.log.1", "server.log"):
        p = os.path.join(root, name)
        try:
            st = os.stat(p)
        except OSError:
            continue
        key = (p, st.st_size, st.st_mtime)
        hit = _logcache.get(p)
        if not hit or hit[0] != key:
            try:
                with open(p, encoding="utf-8", errors="replace") as fh:
                    hit = (key, [ln for ln in fh.read().splitlines() if "swallowed exception" in ln and _HEAD.match(ln)])
            except OSError:
                continue
            _logcache[p] = hit
        lines.extend(hit[1])
    return lines


_logcache = {}
_lock = threading.Lock()
_state = {"t": 0.0, "v": None, "cpu": None, "cpuT": 0.0, "streak": 0, "lastEvent": 0.0, "ext": None, "extT": 0.0}


def _external(now):
    import health_score
    import infra
    if _state["ext"] is None or now - _state["extT"] > EXT_TTL:
        try:
            ext = infra.external_backup_state()
        except Exception as e:
            ext = {"error": str(e)[:80]}
        _state.update(ext=ext, extT=now)
    ext = _state["ext"]
    if not ext:
        return {"key": "backup", "label": "النسخة الخارجية", "level": "warn", "text": "لا مجلد نسخ خارجي مضبوط", "value": None}
    why = health_score.external_problem(ext, now)
    copy = ext.get("copy") or {}
    age = now - copy["ts"] if copy.get("ts") else None
    return {"key": "backup", "label": "النسخة الخارجية", "level": "bad" if why else "ok",
            "text": why or f"آخر نسخة قبل {_dur(age)}", "value": age}


def _last_task_fail(now):
    import db
    with db.ro() as c:
        r = c.execute("SELECT ts,title FROM events WHERE source='task' AND severity='bad' AND ts>? AND ts<=? ORDER BY ts DESC LIMIT 1",
                      (int(now - TASK_FAIL_WINDOW), int(now + 600))).fetchone()
    if not r:
        return {"key": "tasks", "label": "المهام المجدولة", "level": "ok", "text": "لا فشل في آخر 24 ساعة", "value": None}
    return {"key": "tasks", "label": "المهام المجدولة", "level": "warn", "text": "فشلت {} قبل {}".format(str(r[1]).replace("مهمة: ", ""), _dur(now - r[0])),
            "value": int(r[0])}


def _collectors_item(now):
    import db
    with db.ro() as c:
        c.row_factory = None
        cols = [d[1] for d in c.execute("PRAGMA table_info(jobs)")]
        jobs = [dict(zip(cols, r)) for r in c.execute("SELECT * FROM jobs")]
    rows = collectors(jobs, now)
    late = [r for r in rows if r["level"] != "ok"]
    lvl = "ok"
    for r in late:
        lvl = _worse(lvl, r["level"])
    text = ("، ".join("{} {}".format(r["name"], r["why"]) for r in late[:3])) if late else f"{len(rows)} جامعًا في موعدها"
    return {"key": "collectors", "label": "الجوامع", "level": lvl, "text": text, "value": len(late), "rows": rows}


def _swallowed_item(now):
    n = count_swallowed(_log_lines(), time.strftime("%Y-%m-%d", time.localtime(now)))
    lvl = "bad" if n >= SWALLOWED_BAD else "warn" if n >= SWALLOWED_WARN else "ok"
    return {"key": "swallowed", "label": "استثناءات مبتلعة اليوم", "level": lvl, "text": f"{n} استثناءً مبتلعًا اليوم", "value": n}


def _budget_now(now):
    roles = _roles()
    cur = {p["pid"]: p["cpu100ns"] for ps in roles.values() for p in ps}
    b = budget(roles, _state["cpu"], now - _state["cpuT"] if _state["cpu"] is not None else None)
    _state.update(cpu=cur, cpuT=now)
    return b


def _note(b, now):
    """حدث «المرصد يتجاوز ميزانيته» بعد OVER_STREAK عيّنات متتالية، مرة كل EVENT_EVERY."""
    _state["streak"] = _state["streak"] + 1 if b["over"] else 0
    if _state["streak"] >= OVER_STREAK and now - _state["lastEvent"] > EVENT_EVERY:
        import db
        db.add_event(now, "self", "warn", "المرصد يتجاوز ميزانيته", "؛ ".join(b["over"])[:500])
        _state["lastEvent"] = now


def _safe(fn, now, key, label):
    try:
        return fn(now)
    except Exception:
        errlog.swallowed()
        return {"key": key, "label": label, "level": "warn", "text": "تعذّرت القراءة", "value": None}


def view(now=None):
    now = time.time() if now is None else now
    with _lock:
        if _state["v"] is not None and 0 <= now - _state["t"] < TTL:
            return _state["v"]
        items = [_safe(_external, now, "backup", "النسخة الخارجية"), _safe(_collectors_item, now, "collectors", "الجوامع"),
                 _safe(_last_task_fail, now, "tasks", "المهام المجدولة"), _safe(_swallowed_item, now, "swallowed", "استثناءات مبتلعة اليوم")]
        try:
            b = _budget_now(now)
            _note(b, now)
        except Exception:
            errlog.swallowed()
            b = None
        lvl, text = summarize(items, b)
        v = {"level": lvl, "text": text, "items": items, "budget": b, "ts": int(now)}
        _state.update(t=now, v=v)
        return v


def mini():
    try:
        v = view()
        return {"level": v["level"], "text": v["text"]}
    except Exception:
        errlog.swallowed()
        return None
