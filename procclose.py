# -*- coding: utf-8 -*-
"""إغلاق عملية واحدة بطلب صريح من المالك (زر «إغلاق» في جدول الالتزام بصفحة الذاكرة): الطريق الوحيد الذي يخفّض الالتزام فعلًا.
لا إغلاق تلقائيًا أبدًا ولا بالجملة: طلب واحد لعملية واحدة بمعرّفها واسمها معًا (يُرفض إن تبدّل الاسم على المعرّف)، بصلاحية الخادم
العادية (ما يعمل بصلاحيات أعلى لا يُفتح فيُرفض)، وبقائمة منع ثابتة لعمليات النظام الحرجة ولبرامج Python الخلفية (خادم المرصد
ووكيله يعملان pythonw.exe). الوحدة لا تستورد شيئًا من التطبيق حتى تُختبر معزولة؛ التأكيد 428 والتسجيل في routes_destructive.

    check(pid, name, rows=None)                 -> (ok, reason)   السياسة النقية؛ rows لقطة {pid: name} للحقن في الاختبارات
    close(pid, name, force=False, wait=3.0)     -> dict           لطيف أولًا (WM_CLOSE لنوافذ العملية الرئيسية الظاهرة) ثم قسري إن طُلب أو لا نوافذ
    names_by_pid()                              -> {pid: name}    لقطة Toolhelp
"""
import ctypes
import os
from ctypes import wintypes

DENY_NAMES = frozenset({
    "system", "registry", "secure system", "memory compression", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe", "sihost.exe", "taskhostw.exe", "ctfmon.exe",
    "explorer.exe", "audiodg.exe", "spoolsv.exe", "msmpeng.exe", "securityhealthservice.exe", "conhost.exe", "runtimebroker.exe",
    "startmenuexperiencehost.exe", "searchhost.exe", "shellexperiencehost.exe", "textinputhost.exe", "wudfhost.exe", "dllhost.exe",
    "pythonw.exe",   # خادم المرصد ووكيله ومهام المالك الخلفية: لا تُغلق من الواجهة
})
WM_CLOSE = 0x0010
GW_OWNER = 4
PROCESS_TERMINATE, PROCESS_QUERY_LIMITED, SYNCHRONIZE = 0x0001, 0x1000, 0x00100000
WAIT_OBJECT_0 = 0
MB = 1048576.0
REASONS = ("ok", "bad params", "protected", "not_found", "name_mismatch", "access_denied")


class _PE32(ctypes.Structure):   # PROCESSENTRY32W
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_void_p),
                ("mod", wintypes.DWORD), ("thr", wintypes.DWORD), ("ppid", wintypes.DWORD), ("pri", ctypes.c_long),
                ("flags", wintypes.DWORD), ("exe", ctypes.c_wchar * 260)]


class _PMC(ctypes.Structure):   # PROCESS_MEMORY_COUNTERS_EX
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def _k32():
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.TerminateProcess.argtypes = [ctypes.c_void_p, wintypes.UINT]
    return k


def names_by_pid():
    """لقطة {pid: اسم الملف التنفيذي} لكل العمليات (بلا فتح أي عملية)."""
    k = _k32()
    out = {}
    snap = k.CreateToolhelp32Snapshot(0x2, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return out
    pe = _PE32()
    pe.dwSize = ctypes.sizeof(pe)
    ok = k.Process32FirstW(snap, ctypes.byref(pe))
    while ok:
        out[int(pe.pid)] = pe.exe
        ok = k.Process32NextW(snap, ctypes.byref(pe))
    k.CloseHandle(snap)
    return out


def check(pid, name, rows=None):
    """(True, "ok") حين يجوز طلب الإغلاق، وإلا (False, السبب): bad params / protected / not_found / name_mismatch."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 4:
        return False, "bad params"
    if not isinstance(name, str) or not name.strip() or len(name) > 260:
        return False, "bad params"
    n = name.strip().lower()
    if pid == os.getpid() or n in DENY_NAMES:
        return False, "protected"
    rows = names_by_pid() if rows is None else rows
    cur = rows.get(pid)
    if cur is None:
        return False, "not_found"
    if str(cur).strip().lower() != n:
        return False, "name_mismatch"
    return True, "ok"


def _main_windows(pid):
    """النوافذ الرئيسية الظاهرة للعملية (بلا مالك): هي التي تتلقى WM_CLOSE كما لو ضغط المستخدم زر الإغلاق."""
    u32 = ctypes.windll.user32
    u32.GetWindow.restype = wintypes.HWND
    u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    found = []
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(hwnd) and not u32.GetWindow(hwnd, GW_OWNER):
            found.append(hwnd)
        return True
    u32.EnumWindows(CB(cb), 0)
    return found


def close(pid, name, force=False, wait=3.0, rows=None):
    """إغلاق عملية واحدة بعد السياسة. مع نوافذ رئيسية وبلا force: WM_CLOSE ثم انتظار wait ثانية (قد يبقى البرنامج ينتظر حفظًا
    فيعود exited=False وwindows>0 ليطلب المستخدم الإنهاء القسري). بلا نوافذ أو مع force: TerminateProcess.
    commitMB التزام العملية قبل الإغلاق (ما سيتحرّر)."""
    ok, reason = check(pid, name, rows)
    if not ok:
        return {"ok": False, "error": reason, "pid": pid, "name": name}
    k, ps = _k32(), ctypes.windll.psapi
    ps.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
    h = k.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE | PROCESS_QUERY_LIMITED, False, pid)
    if not h:
        return {"ok": False, "error": "access_denied", "pid": pid, "name": name}
    try:
        m = _PMC()
        m.cb = ctypes.sizeof(m)
        commit_mb = int(round(m.PrivateUsage / MB)) if ps.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb) else None
        out = {"ok": True, "pid": pid, "name": name, "commitMB": commit_mb, "windows": 0, "method": "already", "exited": True}
        if k.WaitForSingleObject(h, 0) == WAIT_OBJECT_0:   # خرجت قبل أن نصل إليها
            return out
        wins = _main_windows(pid)
        out["windows"] = len(wins)
        ms = max(0, int(wait * 1000))
        if wins and not force:
            u32 = ctypes.windll.user32
            for w in wins:
                u32.PostMessageW(w, WM_CLOSE, 0, 0)
            out.update(method="window", exited=k.WaitForSingleObject(h, ms) == WAIT_OBJECT_0)
            return out
        if not k.TerminateProcess(h, 1):
            return {"ok": False, "error": "access_denied", "pid": pid, "name": name, "commitMB": commit_mb}
        out.update(method="terminate", exited=k.WaitForSingleObject(h, ms) == WAIT_OBJECT_0)
        return out
    finally:
        k.CloseHandle(h)
