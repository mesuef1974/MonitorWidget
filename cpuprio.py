# -*- coding: utf-8 -*-
"""حارس المعالج: خفض أولوية العملية التي نبّه إليها cpuwatch (زر في الويدجت).

الخفض إلى BELOW_NORMAL فقط، لعمليات الاسم المطلوب التي تستهلك الآن نصف نواة فأكثر، ولا يمسّ عملية أولويتها
ليست NORMAL (ما رفعه أو خفضه صاحبه عمدًا يبقى). لا إيقاف ولا قتل: العملية تكمل عملها، والواجهات تأخذ المعالج
حين تحتاجه، والأثر يزول بانتهاء العملية. عمليات النظام والمرصد نفسه مستثناة بالاسم، والخادم مسؤول دائمًا فلا يكفي الاسم:
على المقبض المفتوح نفسه يُطابَق وقت الإنشاء المقيس (المعرّف لم يُعَد استعماله) وتُشترط ملكية المستخدم التفاعلي في جلسته
(procclose.owner_reason)، فلا تُخفض عملية SYSTEM أو خدمة أو مستخدم آخر.

    ROUTES / post(route, body, invalidate)   POST /api/cpu-lower {"name": "python.exe"}
    busy_pids(name, snap0, snap1, dt)        نقية: [(pid, وقت الإنشاء, الأنوية)] لعمليات الاسم المشغولة
    lower(name, ...)                         (رمز الحالة، الردّ)
    lowered_state(name)                      هل ما زالت عملية خُفضت بهذا الاسم حيّة (للحمولة المصغّرة)
"""
import ctypes
import json
import os
import time
from ctypes import wintypes

import cpuwatch
import db
import procclose

K = cpuwatch.K
K.SetPriorityClass.argtypes = [ctypes.c_void_p, wintypes.DWORD]
K.GetPriorityClass.argtypes = [ctypes.c_void_p]
K.GetPriorityClass.restype = wintypes.DWORD
K.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
K.WaitForSingleObject.restype = wintypes.DWORD

ROUTES = ("/api/cpu-lower",)
NORMAL, BELOW_NORMAL, IDLE = 0x20, 0x4000, 0x40
MIN_CORES = 0.5
MEASURE_S = 1.0
STATE_KEY = "cpu_lowered"
# لا تُخفض: نواة النظام وجلسته وسطح المكتب والصوت والحماية، والمرصد نفسه (pythonw.exe خادمه ووكيله، pwsh.exe ويدجته)
PROTECTED = frozenset({"system", "registry", "idle", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "smss.exe",
                       "dwm.exe", "explorer.exe", "audiodg.exe", "msmpeng.exe", "svchost.exe", "pythonw.exe", "pwsh.exe",
                       # مضيف COM (مصغّرات المستكشف وخدمات COM) وآلة WSL الافتراضية: خفضها يبطئ النظام كله لا برنامجًا واحدًا
                       "dllhost.exe", "vmmem", "vmmemwsl"})


def busy_pids(name, snap0, snap1, dt, min_cores=MIN_CORES):
    """عمليات الاسم التي استهلكت min_cores نواة فأكثر بين اللقطتين؛ يُتجاهل ما تبدّل وقت إنشائه (pid أُعيد استعماله)."""
    name = name.lower()
    out = []
    for pid, (exe, created, used) in snap1.items():
        before = snap0.get(pid)
        if pid <= 0 or exe.lower() != name or not before or before[1] != created or used < before[2] or dt <= 0:
            continue
        cores = (used - before[2]) / 1e7 / dt
        if cores >= min_cores:
            out.append((pid, created, round(cores, 2)))
    return sorted(out, key=lambda r: -r[2])


def _measure(name):
    a = cpuwatch._snap()
    t0 = time.time()
    time.sleep(MEASURE_S)
    b = cpuwatch._snap()
    return busy_pids(name, a, b, time.time() - t0)


def _set_below(pid, created, mine=None):
    """lowered | already | skipped (أولوية غير عادية أو تبدّل وقت الإنشاء) | foreign (ليست للمستخدم التفاعلي) | denied.
    الفحص على المقبض المفتوح نفسه، والمعرّف لا يُعاد استعماله ما دام مفتوحًا، فلا سباق بين الفحص والتغيير."""
    h = K.OpenProcess(0x0200 | 0x1000, False, pid)   # PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return "denied"
    try:
        cur_created, sid, session = procclose._ident(h, pid)
        if cur_created is None:
            return "denied"
        if cur_created != str(created):
            return "skipped"
        if procclose.owner_reason(sid, session, procclose.me() if mine is None else mine):
            return "foreign"
        cur = K.GetPriorityClass(h)
        if cur in (BELOW_NORMAL, IDLE):
            return "already"
        if cur != NORMAL:
            return "skipped"
        return "lowered" if K.SetPriorityClass(h, BELOW_NORMAL) else "denied"
    finally:
        K.CloseHandle(h)


def lower(name, measure=_measure, setter=_set_below, now=None):
    name = str(name or "").strip()
    if not name or len(name) > 260 or name.lower() in PROTECTED:
        return 400, {"error": "عملية محمية أو اسم غير صالح"}
    busy = measure(name)
    if not busy:
        return 200, {"ok": True, "name": name, "lowered": 0, "already": 0, "denied": 0, "skipped": 0, "foreign": 0,
                     "text": "لا عملية بهذا الاسم تستهلك المعالج الآن"}
    counts = {"lowered": 0, "already": 0, "denied": 0, "skipped": 0, "foreign": 0}
    kept = []
    for pid, created, _cores in busy:
        if pid == os.getpid():
            continue
        res = setter(pid, created)
        counts[res] = counts.get(res, 0) + 1
        if res in ("lowered", "already"):
            kept.append([pid, created])
    now = now or time.time()
    if kept:
        cpuwatch._meta_set(STATE_KEY, json.dumps({"name": name, "ts": int(now), "pids": kept}))
    if counts["lowered"]:
        db.add_event(now, "cpu", "info", "خُفضت أولوية %s" % name,
                     "%d عملية إلى «أقل من العادي» بطلب من الويدجت؛ تكمل عملها ويزول الأثر بانتهائها." % counts["lowered"])
    if counts["lowered"]:
        text = "خُفضت أولوية %s (%d)" % (name, counts["lowered"])
    elif counts["already"]:
        text = "أولوية %s مخفّضة من قبل" % name
    elif counts["foreign"]:
        text = "العملية للنظام أو لمستخدم آخر فلا تُخفض"
    elif counts["denied"]:
        text = "تعذّر: العملية تعمل بصلاحية أعلى من الخادم"
    else:
        text = "أولوية العملية معدّلة يدويًا فتُركت كما هي"
    return 200, dict(counts, ok=True, name=name, text=text)


def _alive(pid, created):
    h = K.OpenProcess(0x1000 | 0x00100000, False, pid)   # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if not h:
        return False
    try:
        c, e, k, u = (wintypes.FILETIME() for _ in range(4))
        if not K.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
            return False
        return cpuwatch._ft(c) == created and K.WaitForSingleObject(h, 0) == 0x102   # WAIT_TIMEOUT = ما زالت تعمل
    finally:
        K.CloseHandle(h)


def lowered_state(name, alive=_alive):
    """True إن خُفضت عمليات بهذا الاسم وما زالت إحداها حيّة (الويدجت يعرض «مخفّضة» بدل الزر)."""
    try:
        st = json.loads(db._meta_get(STATE_KEY, "") or "{}")
    except Exception:
        return False
    if not name or str(st.get("name", "")).lower() != str(name).lower():
        return False
    return any(alive(int(p), int(c)) for p, c in st.get("pids") or [])


def post(route, body, invalidate=lambda: None):
    if route != "/api/cpu-lower":
        return None
    return lower(body.get("name"))
