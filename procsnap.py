"""لقطة Toolhelp واحدة لكل العمليات: الدالة المشتركة بدل تسعة تعدادات مكرّرة (البند 35 من المجلس 19/11).
لا تفتح أي عملية؛ ما يحتاجه المستدعي بعدها (الذاكرة، الأزمنة، الإدخال والإخراج) يقرؤه هو بمقبضه."""
import ctypes
import sys
from collections import namedtuple

Proc = namedtuple("Proc", "pid ppid name threads")

if sys.platform == "win32":
    from ctypes import wintypes

    class _PE32(ctypes.Structure):   # PROCESSENTRY32W
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_void_p),
                    ("mod", wintypes.DWORD), ("thr", wintypes.DWORD), ("ppid", wintypes.DWORD), ("pri", ctypes.c_long),
                    ("flags", wintypes.DWORD), ("exe", ctypes.c_wchar * 260)]

    # نسخة kernel32 خاصة: ضبط argtypes على ctypes.windll.kernel32 المشترك يغيّر توقيع الدالة لكل الوحدات الأخرى.
    _k = ctypes.WinDLL("kernel32", use_last_error=True)
    _k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    _k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PE32)]
    _k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PE32)]
    _k.CloseHandle.argtypes = [ctypes.c_void_p]
    _INVALID = ctypes.c_void_p(-1).value


def processes():
    """[Proc(pid, ppid, name, threads)] لكل عملية في لحظة واحدة (يشمل 0 = System Idle Process)؛ [] عند الفشل أو خارج ويندوز."""
    if sys.platform != "win32":
        return []
    snap = _k.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if not snap or snap == _INVALID:
        return []
    out = []
    try:
        pe = _PE32()
        pe.dwSize = ctypes.sizeof(pe)
        ok = _k.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            out.append(Proc(int(pe.pid), int(pe.ppid), pe.exe, int(pe.thr)))
            ok = _k.Process32NextW(snap, ctypes.byref(pe))
    finally:
        _k.CloseHandle(snap)
    return out


if __name__ == "__main__":
    ps = processes()
    assert sys.platform != "win32" or (len(ps) > 5 and any(p.pid == 4 for p in ps)), ps[:5]
    print("selftest ok:", len(ps), "processes")
