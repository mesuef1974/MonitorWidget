# -*- coding: utf-8 -*-
"""التزام الذاكرة (Commit Charge): الرقم الذي يعرضه مدير المهام تحت «Committed»، أي الذاكرة الخاصة التي وعد النظام بتوفيرها
من الذاكرة الفعلية وملف الصفحات معًا. تنظيف الذاكرة (memtrim) لا يخفّضه: EmptyWorkingSet وتفريغ الاحتياطي ينقلان الصفحات
ولا يحرّران التزامها؛ لا ينخفض الالتزام إلا بإغلاق البرنامج أو بتحريره الذاكرة بنفسه، وتكبير ملف الصفحات يرفع الحدّ لا الاستهلاك.
هذه الوحدة قراءة فقط وبلا صلاحيات مسؤول، ولا تستورد شيئًا من التطبيق حتى تُختبر معزولة.

    status()                  -> الحالة الكلية من GetPerformanceInfo (الرقم نفسه الذي يعطيه GlobalMemoryStatusEx في snapshot)
    scan(with_cmd=False)      -> (صفوف لكل عملية يمكن فتحها بـ PROCESS_QUERY_LIMITED_INFORMATION، عدد المتعذّر فتحها)؛
                                 كل صف فيه ppid، ومع with_cmd سطر الأمر cmd (لتصنيف العمليات حسب المشروع في commitmap)
    rank(rows, n)             -> أعلى n عملية التزامًا (نقية؛ تُختبر بصفوف وهمية)
    payload(n, rows, st, now) -> حمولة GET /api/commit؛ rows وst للحقن في الاختبارات
"""
import ctypes
import os
import threading
import time
from ctypes import wintypes

SCAN_TTL = 3.0   # الواجهة والويدجت قد يطلبان المسار معًا؛ مسح العمليات يُعاد استعماله خلال هذه المدة
MB = 1048576.0
GB = 1073741824.0


class _PERF(ctypes.Structure):   # PERFORMANCE_INFORMATION
    _fields_ = [("cb", wintypes.DWORD), ("CommitTotal", ctypes.c_size_t), ("CommitLimit", ctypes.c_size_t), ("CommitPeak", ctypes.c_size_t),
                ("PhysicalTotal", ctypes.c_size_t), ("PhysicalAvailable", ctypes.c_size_t), ("SystemCache", ctypes.c_size_t),
                ("KernelTotal", ctypes.c_size_t), ("KernelPaged", ctypes.c_size_t), ("KernelNonpaged", ctypes.c_size_t),
                ("PageSize", ctypes.c_size_t), ("HandleCount", wintypes.DWORD), ("ProcessCount", wintypes.DWORD), ("ThreadCount", wintypes.DWORD)]


class _PE32(ctypes.Structure):   # PROCESSENTRY32W
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_void_p),
                ("mod", wintypes.DWORD), ("thr", wintypes.DWORD), ("ppid", wintypes.DWORD), ("pri", ctypes.c_long),
                ("flags", wintypes.DWORD), ("exe", ctypes.c_wchar * 260)]


class _PMC(ctypes.Structure):   # PROCESS_MEMORY_COUNTERS_EX؛ PrivateUsage = التزام العملية
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


class _US(ctypes.Structure):   # UNICODE_STRING في رأس ردّ ProcessCommandLineInformation
    _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]


def _cmdline(h):
    """سطر أمر العملية عبر NtQueryInformationProcess(ProcessCommandLineInformation=60)؛ يكفيه PROCESS_QUERY_LIMITED_INFORMATION
    منذ ويندوز 8.1 فلا يحتاج صلاحيات مسؤول ولا قراءة ذاكرة العملية. "" عند الفشل."""
    nt = ctypes.windll.ntdll
    nt.NtQueryInformationProcess.restype = ctypes.c_long
    nt.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    need = ctypes.c_ulong(0)
    nt.NtQueryInformationProcess(h, 60, None, 0, ctypes.byref(need))
    if not need.value or need.value > 1 << 20:
        return ""
    buf = ctypes.create_string_buffer(need.value)
    if nt.NtQueryInformationProcess(h, 60, buf, need.value, ctypes.byref(need)) != 0:
        return ""
    us = _US.from_buffer(buf)
    return ctypes.wstring_at(us.Buffer, us.Length // 2) if us.Buffer and us.Length else ""


_cache = {"t": 0.0, "rows": None, "skipped": 0}
_lock = threading.Lock()


def status():
    """الالتزام الكلي بالغيغابايت: المستخدم والحدّ والذروة منذ الإقلاع والهامش المتبقي ونسبته. None إن فشل الاستدعاء."""
    p = _PERF()
    p.cb = ctypes.sizeof(p)
    if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(p), p.cb) or not p.CommitLimit:
        return None
    g = p.PageSize / GB
    used, limit, peak = p.CommitTotal * g, p.CommitLimit * g, p.CommitPeak * g
    return {"usedGB": round(used, 2), "limitGB": round(limit, 2), "peakGB": round(peak, 2),
            "headroomGB": round(max(0.0, limit - used), 2), "pct": round(100.0 * used / limit, 1)}


def scan(with_cmd=False):
    """صفوف {pid, ppid, name, commitMB, wsMB[, cmd]} لكل عملية يمكن فتحها بصلاحية المستخدم، وعدد العمليات المحجوبة (عمليات النظام غالبًا)."""
    k, ps = ctypes.windll.kernel32, ctypes.windll.psapi
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    ps.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
    rows, skipped = [], 0
    snap = k.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return rows, skipped
    pe = _PE32()
    pe.dwSize = ctypes.sizeof(pe)
    ok = k.Process32FirstW(snap, ctypes.byref(pe))
    while ok:
        if pe.pid:   # 0 = System Idle Process؛ لا يُفتح ولا يُعدّ
            h = k.OpenProcess(0x1000, False, pe.pid)   # PROCESS_QUERY_LIMITED_INFORMATION: يكفي لـ GetProcessMemoryInfo بلا صلاحيات مسؤول
            if h:
                m = _PMC()
                m.cb = ctypes.sizeof(m)
                if ps.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb):
                    row = {"pid": int(pe.pid), "ppid": int(pe.ppid), "name": pe.exe, "commitMB": m.PrivateUsage / MB, "wsMB": m.WorkingSetSize / MB}
                    if with_cmd:
                        row["cmd"] = _cmdline(h)
                    rows.append(row)
                else:
                    skipped += 1
                k.CloseHandle(h)
            else:
                skipped += 1
        ok = k.Process32NextW(snap, ctypes.byref(pe))
    k.CloseHandle(snap)
    return rows, skipped


def _scan_cached():
    with _lock:
        if _cache["rows"] is None or time.time() - _cache["t"] > SCAN_TTL:
            rows, skipped = scan()
            _cache.update(t=time.time(), rows=rows, skipped=skipped)
        return _cache["rows"], _cache["skipped"]


def rank(rows, n=10):
    """أعلى n عملية التزامًا، تنازليًا ثم بالاسم والمعرّف للثبات؛ الأحجام بالميغابايت مقرّبة لأعداد صحيحة."""
    n = max(1, min(50, int(n)))
    top = sorted(rows, key=lambda r: (-float(r.get("commitMB") or 0), str(r.get("name") or "").lower(), int(r.get("pid") or 0)))[:n]
    return [{"pid": int(r.get("pid") or 0), "name": str(r.get("name") or ""), "commitMB": int(round(float(r.get("commitMB") or 0))),
             "wsMB": int(round(float(r.get("wsMB") or 0)))} for r in top]


def payload(n=10, rows=None, st=None, now=None):
    """حمولة GET /api/commit: الحالة الكلية مفرودة + top + عدد العمليات المقروءة والمحجوبة + مجموع التزام المقروءة (procsGB).
    الفرق بين usedGB وprocsGB هو التزام النواة والعمليات المحجوبة (لا تُفتح بلا صلاحيات مسؤول)."""
    skipped = 0
    if rows is None:
        rows, skipped = _scan_cached()
    if st is None:
        st = status() or {}
    out = {"ok": True, "now": int(time.time() if now is None else now), "self": os.getpid()}
    out.update({k: st.get(k) for k in ("usedGB", "limitGB", "peakGB", "headroomGB", "pct")})
    out.update({"top": rank(rows, n), "procs": len(rows), "skipped": int(skipped),
                "procsGB": round(sum(float(r.get("commitMB") or 0) for r in rows) / 1024.0, 2)})
    return out
