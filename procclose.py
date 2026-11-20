"""إغلاق عملية واحدة بطلب صريح من المالك (زر «إغلاق» في جدول الالتزام بصفحة الذاكرة): الطريق الوحيد الذي يخفّض الالتزام فعلًا.
لا إغلاق تلقائيًا أبدًا ولا بالجملة: طلب واحد لعملية واحدة بمعرّفها واسمها ووقت إنشائها معًا كما رآها المالك في الجدول
(يُرفض إن تبدّل الاسم أو وقت الإنشاء على المعرّف، أي أُعيد استعماله)، وبقائمة منع ثابتة لعمليات النظام الحرجة ولبرامج Python
الخلفية (خادم المرصد ووكيله يعملان pythonw.exe). الخادم يعمل بصلاحية مسؤول دائمًا، فلا تكفي قائمة الأسماء: تُرفض كل عملية
لا يملكها المستخدم التفاعلي نفسه (مالك رمز الخادم) في جلسته نفسها، فتخرج عمليات SYSTEM وLocalService وNetworkService والخدمات
وجلسة الخدمات 0 ونوافذ DWM/UMFD. التحقق النهائي يجري على المقبض المفتوح نفسه قبل الإنهاء، والمعرّف لا يُعاد استعماله ما دام
المقبض مفتوحًا، فلا سباق بين الفحص والإنهاء. الوحدة لا تستورد شيئًا من التطبيق حتى تُختبر معزولة؛ التأكيد 428 والتسجيل في
routes_destructive.

    check(pid, name, created, rows=None, probe=None) -> (ok, reason)   السياسة؛ rows لقطة {pid: name} وprobe(pid) للحقن في الاختبارات
    owner_reason(sid, session, me)                   -> None أو "not_owner"   نقية: ملكية المستخدم التفاعلي
    close(pid, name, created, force=False, wait=3.0) -> dict           لطيف أولًا (WM_CLOSE لنوافذ العملية الرئيسية) ثم قسري
    names_by_pid()                                   -> {pid: name}    لقطة Toolhelp
    identity(pid)                                    -> (created, sid, session) أو None
"""
import ctypes
import os
import procsnap
from ctypes import wintypes

DENY_NAMES = frozenset({
    "system", "registry", "secure system", "memory compression", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe", "sihost.exe", "taskhostw.exe", "ctfmon.exe",
    "explorer.exe", "audiodg.exe", "spoolsv.exe", "msmpeng.exe", "securityhealthservice.exe", "conhost.exe", "runtimebroker.exe",
    "startmenuexperiencehost.exe", "searchhost.exe", "shellexperiencehost.exe", "textinputhost.exe", "wudfhost.exe", "dllhost.exe",
    "pythonw.exe",   # خادم المرصد ووكيله ومهام المالك الخلفية: لا تُغلق من الواجهة
})
# حسابات الخدمات المعروفة: SYSTEM وLocalService وNetworkService. إن عمل الخادم بأحدها فالمستخدم التفاعلي مجهول فيُرفض كل شيء
SERVICE_SIDS = frozenset({"S-1-5-18", "S-1-5-19", "S-1-5-20"})
WM_CLOSE = 0x0010
GW_OWNER = 4
PROCESS_TERMINATE, PROCESS_QUERY_LIMITED, SYNCHRONIZE = 0x0001, 0x1000, 0x00100000
TOKEN_QUERY, TOKEN_USER_CLASS = 0x0008, 1
WAIT_OBJECT_0 = 0
MB = 1048576.0
REASONS = ("ok", "bad params", "protected", "not_found", "name_mismatch", "created_mismatch", "not_owner", "access_denied")


class _PMC(ctypes.Structure):   # PROCESS_MEMORY_COUNTERS_EX
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


def _k32():
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.TerminateProcess.argtypes = [ctypes.c_void_p, wintypes.UINT]
    k.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
    k.GetCurrentProcess.restype = ctypes.c_void_p
    k.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    k.LocalFree.argtypes = [ctypes.c_void_p]
    return k


def _adv():
    a = ctypes.windll.advapi32
    a.OpenProcessToken.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    a.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    return a


def names_by_pid():
    """لقطة {pid: اسم الملف التنفيذي} لكل العمليات (بلا فتح أي عملية)."""
    return {p.pid: p.name for p in procsnap.processes()}


def created_of(h):
    """وقت إنشاء العملية من مقبضها نصًّا عشريًّا (FILETIME بوحدات 100 نانوثانية)، أو None. نصّ لأن الرقم يتجاوز دقة أعداد JavaScript."""
    k = _k32()
    c, e, kt, ut = (ctypes.c_ulonglong() for _ in range(4))
    if not k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(kt), ctypes.byref(ut)) or not c.value:
        return None
    return str(c.value)


def _token_sid(h):
    """SID مالك رمز العملية نصًّا (S-1-5-...)، أو None إن تعذّرت قراءته."""
    k, a = _k32(), _adv()
    tok = ctypes.c_void_p()
    if not a.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
        return None
    try:
        need = wintypes.DWORD(0)
        a.GetTokenInformation(tok, TOKEN_USER_CLASS, None, 0, ctypes.byref(need))
        if not need.value or need.value > 4096:
            return None
        buf = ctypes.create_string_buffer(need.value)
        if not a.GetTokenInformation(tok, TOKEN_USER_CLASS, buf, need.value, ctypes.byref(need)):
            return None
        psid = ctypes.c_void_p.from_buffer(buf).value   # TOKEN_USER يبدأ بـ SID_AND_ATTRIBUTES.Sid
        s = ctypes.c_void_p()
        if not psid or not a.ConvertSidToStringSidW(psid, ctypes.byref(s)):
            return None
        try:
            return ctypes.wstring_at(s.value)
        finally:
            k.LocalFree(s)
    finally:
        k.CloseHandle(tok)


def _session(pid):
    k = _k32()
    s = wintypes.DWORD()
    return int(s.value) if k.ProcessIdToSessionId(pid, ctypes.byref(s)) else None


def _ident(h, pid):
    """(وقت الإنشاء، SID المالك، رقم الجلسة) لعملية مقبضها مفتوح؛ المعرّف لا يُعاد استعماله ما دام المقبض مفتوحًا."""
    return created_of(h), _token_sid(h), _session(pid)


def identity(pid):
    """هوية العملية بمقبض استعلام محدود يُغلق فورًا، أو None إن تعذّر فتحها."""
    k = _k32()
    h = k.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
    if not h:
        return None
    try:
        return _ident(h, pid)
    finally:
        k.CloseHandle(h)


def me():
    """(SID، الجلسة) للخادم نفسه: المستخدم التفاعلي الذي يعمل الخادم برمزه (مرتفعًا) في جلسته."""
    k = _k32()
    return _token_sid(k.GetCurrentProcess()), _session(os.getpid())


def owner_reason(sid, session, mine):
    """None إن كانت العملية للمستخدم التفاعلي نفسه في جلسته نفسها، وإلا "not_owner". نقية.
    mine = (SID الخادم، جلسته). أي مجهول يعني الرفض؛ وإن عمل الخادم بحساب خدمة فلا مستخدم تفاعليًا يُطمأن إليه."""
    my_sid, my_session = mine
    if not sid or not my_sid or session is None or my_session is None:
        return "not_owner"
    if my_sid in SERVICE_SIDS or sid in SERVICE_SIDS or session == 0:
        return "not_owner"
    if sid != my_sid or session != my_session:
        return "not_owner"
    return None


def _created_ok(created):
    return isinstance(created, str) and 0 < len(created) <= 20 and created.isascii() and created.isdigit() and int(created) > 0


def _verdict(ident, created, mine):
    """سبب الرفض من هوية العملية الحية، أو None."""
    if not ident:
        return "access_denied"
    cur_created, sid, session = ident
    if cur_created is None:
        return "access_denied"
    if cur_created != created:
        return "created_mismatch"
    return owner_reason(sid, session, mine)


def check(pid, name, created, rows=None, probe=None, mine=None):
    """(True, "ok") حين يجوز طلب الإغلاق، وإلا (False, السبب): bad params / protected / not_found / name_mismatch /
    created_mismatch / not_owner / access_denied. probe(pid) -> (created, sid, session) وmine للحقن في الاختبارات."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 4:
        return False, "bad params"
    if not isinstance(name, str) or not name.strip() or len(name) > 260:
        return False, "bad params"
    if not _created_ok(created):
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
    why = _verdict((probe or identity)(pid), created, me() if mine is None else mine)
    if why:
        return False, why
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


def close(pid, name, created, force=False, wait=3.0, rows=None, probe=None, mine=None):
    """إغلاق عملية واحدة بعد السياسة. مع نوافذ رئيسية وبلا force: WM_CLOSE ثم انتظار wait ثانية (قد يبقى البرنامج ينتظر حفظًا
    فيعود exited=False وwindows>0 ليطلب المستخدم الإنهاء القسري). بلا نوافذ أو مع force: TerminateProcess.
    بعد فتح المقبض تُعاد مطابقة وقت الإنشاء والملكية عليه نفسه (لا على probe المحقون): هذا الفحص الحاسم.
    commitMB التزام العملية قبل الإغلاق (ما سيتحرّر)."""
    ok, reason = check(pid, name, created, rows, probe, mine)
    if not ok:
        return {"ok": False, "error": reason, "pid": pid, "name": name}
    k, ps = _k32(), ctypes.windll.psapi
    ps.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
    h = k.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE | PROCESS_QUERY_LIMITED, False, pid)
    if not h:
        return {"ok": False, "error": "access_denied", "pid": pid, "name": name}
    try:
        why = _verdict(_ident(h, pid), created, me() if mine is None else mine)
        if why:
            return {"ok": False, "error": why, "pid": pid, "name": name}
        m = _PMC()
        m.cb = ctypes.sizeof(m)
        commit_mb = round(m.PrivateUsage / MB) if ps.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb) else None
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
