# -*- coding: utf-8 -*-
"""أولوية عملياتنا نحن (لا عمليات غيرنا: تلك في cpuprio.py).
مجدول المهام يشغّل كل مهمة بأولوية 7: صنف «أقل من العادي» للمعالج مع أولوية إدخال/إخراج وذاكرة منخفضتين، ويرثها كل ابن.
حين تشغل مهمة بأولوية عادية كل الأنوية (pytest موزّع مثلًا) تُجوَّع هذه العمليات: قيس 13/11/2026 تجمّد الويدجت
حتى 116 ث وتأخّر عيّنات الوكيل حتى 113 ث مع أن متوسط الحمل نصف الأنوية. العملية ترفع نفسها إلى «عادي» بلا صلاحية خاصة."""
import contextlib
import ctypes
import os

IDLE, BELOW_NORMAL, NORMAL = 0x40, 0x4000, 0x20
_IO_CLASS, _PAGE_CLASS = 33, 39          # ProcessIoPriority, ProcessPagePriority
IO_NORMAL, PAGE_NORMAL = 2, 5


def _info(k, nt, h, cls, value=None):
    """يقرأ (value=None) أو يكتب قيمة ULONG لصنف معلومات العملية. يعيد القيمة أو None عند الفشل."""
    v = ctypes.c_ulong(0 if value is None else value)
    if value is None:
        return v.value if nt.NtQueryInformationProcess(h, cls, ctypes.byref(v), ctypes.sizeof(v), None) == 0 else None
    return value if nt.NtSetInformationProcess(h, cls, ctypes.byref(v), ctypes.sizeof(v)) == 0 else None


def state():
    """{"cpu", "io", "page"} للعملية الحالية؛ {} خارج ويندوز أو عند الفشل."""
    if os.name != "nt":
        return {}
    try:
        k, nt = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("ntdll")
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.GetPriorityClass.argtypes = [ctypes.c_void_p]
        nt.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
        h = k.GetCurrentProcess()
        return {"cpu": k.GetPriorityClass(h), "io": _info(k, nt, h, _IO_CLASS), "page": _info(k, nt, h, _PAGE_CLASS)}
    except (OSError, AttributeError):
        return {}


def restore_normal(io=True):
    """يرفع العملية الحالية إلى الأولوية العادية إن بدأت دونها، ولا يخفض عملية أعلى. io=False يبقي الإدخال/الإخراج والذاكرة
    كما ورثتهما (للوكيل: مسوحه الخلفية تبقى مهذّبة على القرص). يعيد {"before", "after"} ولا يرمي."""
    before = state()
    if not before:
        return {"before": {}, "after": {}}
    try:
        k, nt = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("ntdll")
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        nt.NtSetInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        h = k.GetCurrentProcess()
        if before["cpu"] in (IDLE, BELOW_NORMAL):
            k.SetPriorityClass(h, NORMAL)
        if io:
            if before["io"] is not None and before["io"] < IO_NORMAL:
                _info(k, nt, h, _IO_CLASS, IO_NORMAL)
            if before["page"] is not None and before["page"] < PAGE_NORMAL:
                _info(k, nt, h, _PAGE_CLASS, PAGE_NORMAL)
    except (OSError, AttributeError):
        pass
    return {"before": before, "after": state()}


_BG_BEGIN, _BG_END = 0x00010000, 0x00020000   # THREAD_MODE_BACKGROUND_BEGIN/END: معالج وإدخال/إخراج وذاكرة منخفضة للخيط وحده


@contextlib.contextmanager
def background_thread():
    """يشغّل كتلة في الخيط الحالي بوضع الخلفية (أولوية قرص «منخفضة جدًا» ومعالج وذاكرة منخفضة) ثم يعيده في finally،
    فمسح طويل كـ scan_big لا يزاحم القرص في عملية الخادم (زر «أعد القياس») ولا في الوكيل. يُنتج True إن دخل الوضع، ولا يرمي.
    الوضع للخيط نفسه فقط: لا يرثه خيط يبدأ داخله، ولا يمسّ خيوط العملية الأخرى."""
    on = False
    if os.name == "nt":
        try:
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.GetCurrentThread.restype = ctypes.c_void_p
            k.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
            on = bool(k.SetThreadPriority(k.GetCurrentThread(), _BG_BEGIN))
        except (OSError, AttributeError):
            on = False
    try:
        yield on
    finally:
        if on:
            try:
                k.SetThreadPriority(k.GetCurrentThread(), _BG_END)
            except (OSError, AttributeError):
                pass
