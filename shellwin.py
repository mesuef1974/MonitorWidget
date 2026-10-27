# -*- coding: utf-8 -*-
"""نافذة التطبيق وغلافها (البند 6/هـ من الخارطة): فتح لوحة التحكم بتذكرة من الخادم الحي (غلاف app_shell.ps1 بـ WebView2، وإلا
Edge --app، وإلا المتصفح)، وضع نافذة Edge في منطقة العمل، وحالة الغلاف التي تعرضها صفحة الصحة (/api/health → shell)
مع بلاغ الويدجت POST /api/shell. العقود كما كانت في server.py حرفيًا.

    ROUTES / post(route, body, invalidate)   POST /api/shell: (400 لمحرك أو سبب خارج القائمة البيضاء، وإلا 200 مع الحالة)
    shell_status()                           الجاهزية الآن وسببها ومحرك آخر فتح (لصفحة الصحة)
    open_window(token)                       يطلب تذكرة من الخادم الحي بالرمز ثم يفتح النافذة بأفضل محرك متاح
"""
import ctypes
import json
import os
import subprocess
import threading
import time
from ctypes import wintypes

import atomic_json
import infra
from paths import PORT, ROOT

ROUTES = ("/api/shell",)
TITLEBAR_RESERVE = 40  # شريط عنوان النافذة يُضاف فوق المقاس فيلزم طرحه حتى لا تغطي شريط المهام
WIDGET_RESERVE = 490  # عرض الويدجت (372 وحدة منطقية) مع هامش، بالبكسل الفعلي
SHELL_STATE_PATH = os.path.join(os.environ.get("LOCALAPPDATA", ROOT), "MonitorWidget", "shell_state.json")
SHELL_ENGINES = ("webview2", "edge", "browser")                     # من يكتب الحالة: open_window هنا، app_shell.ps1، وزر الويدجت
SHELL_REASONS = ("", "no_shell", "no_webview2", "init_failed")       # مفاتيح ثابتة تفسّرها صفحة الصحة


def _work_area():
    """منطقة العمل الفعلية للشاشة الأساسية بالبكسل الفعلي (بدون شريط المهام)."""
    import ctypes.wintypes as wt
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI: الأرقام بالبكسل الفعلي
    except Exception:
        pass
    r = wt.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)  # SPI_GETWORKAREA
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _place_window(title, x, y, w, h):
    """Place only the exact dashboard title owned by Edge, never a WPF widget."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.EnumWindows.argtypes = (CB, wintypes.LPARAM)
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.MoveWindow.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL)
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    def owned_by_edge(hwnd):
        pid = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            return False
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            image = ctypes.create_unicode_buffer(32768)
            count = wintypes.DWORD(len(image))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(count)):
                return False
            return os.path.basename(image.value).casefold() == "msedge.exe"
        finally:
            kernel32.CloseHandle(handle)

    for _ in range(40):
        time.sleep(0.15)
        found = []

        def cb(hwnd, _l):
            if user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if buf.value == title and owned_by_edge(hwnd):
                    found.append(hwnd)
                    return False
            return True
        user32.EnumWindows(CB(cb), 0)
        if found:
            # Explicit Open must restore a previously minimized application.
            # MoveWindow alone leaves that window minimized and appears to do nothing.
            user32.ShowWindow(found[0], 9)  # SW_RESTORE
            user32.MoveWindow(found[0], x, y, w, h, True)
            user32.SetForegroundWindow(found[0])
            return


def _ticket_for_window(token):
    """Ask the running server to issue its ticket, even from a --open helper.

    A helper process has a different in-memory ticket store. Issuing a ticket
    locally there opens an unauthenticated dashboard beside a healthy backend.
    """
    import urllib.request
    request = urllib.request.Request(
        "http://127.0.0.1:%d/api/ticket" % PORT, data=b"{}", method="POST",
        headers={"X-Token": token, "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        value = json.load(response)
    ticket = value.get("ticket") if isinstance(value, dict) else None
    if not isinstance(ticket, str) or not ticket:
        raise RuntimeError("The running server did not issue a dashboard ticket")
    return ticket


def _shell_readiness():
    """(ready, reason): هل غلاف app_shell.ps1 ومكتبات WebView2 متوفران؟ وإلا نرجع إلى Edge --app. السبب مفتاح ثابت للواجهة."""
    if not os.path.exists(os.path.join(ROOT, "app_shell.ps1")):
        return False, "no_shell"
    base = os.environ.get("LOCALAPPDATA", "")
    if os.path.exists(os.path.join(base, "MonitorWidget", "webview2", "Microsoft.Web.WebView2.Wpf.dll")):
        return True, ""
    teams = os.path.join(base, "Microsoft", "TeamsMeetingAdd-in")
    try:
        if any(os.path.exists(os.path.join(teams, v, "x64", "Microsoft.Web.WebView2.Wpf.dll")) for v in os.listdir(teams)):
            return True, ""
    except OSError:
        pass
    return False, "no_webview2"


def _shell_ready():
    return _shell_readiness()[0]


def _record_shell(engine, reason=""):
    """يسجّل محرك آخر فتح للنافذة (webview2 / edge / browser) في ملف صغير؛ عملية --open المساعدة تنتهي فورًا فلا تكفي الذاكرة."""
    try:
        os.makedirs(os.path.dirname(SHELL_STATE_PATH), exist_ok=True)
        atomic_json.write(SHELL_STATE_PATH, False, {"engine": engine, "reason": reason, "ts": int(time.time())})
    except Exception:
        infra.log.exception("shell state not recorded")


def shell_status():
    """حالة الغلاف لصفحة الصحة: الجاهزية الآن وسببها، ومحرك آخر فتح. fallback=True حين لا يعمل WebView2."""
    ready, reason = _shell_readiness()
    last = {}
    try:
        last = atomic_json.read(SHELL_STATE_PATH)[1] or {}
    except Exception:
        last = {}
    engine = last.get("engine") if isinstance(last, dict) else None
    last_reason = (last.get("reason") if isinstance(last, dict) else "") or ""
    detail = ""
    if last_reason == "init_failed":   # الغلاف كتب الاستثناء في shell_error.log بجانب ملف الحالة؛ سطره الأول يكفي الصفحة
        try:
            with open(os.path.join(os.path.dirname(SHELL_STATE_PATH), "shell_error.log"), encoding="utf-8-sig", errors="replace") as fh:
                detail = fh.read(400).strip()
        except OSError:
            detail = ""
    return {"ready": ready, "reason": reason, "engine": engine, "lastReason": last_reason, "detail": detail,
            "ts": (last.get("ts") if isinstance(last, dict) else None), "fallback": (not ready) or (engine in ("edge", "browser"))}


def record_shell_report(body):
    """POST /api/shell من الويدجت: (رمز الحالة، الردّ). محرك وسبب من القائمة البيضاء فقط، ثم حالة الغلاف المحدّثة."""
    engine, reason = body.get("engine"), body.get("reason", "")
    if engine not in SHELL_ENGINES or reason not in SHELL_REASONS:
        return 400, {"error": "unknown engine or reason"}
    _record_shell(engine, reason)
    return 200, {"ok": True, "shell": shell_status()}


def open_window(token):
    time.sleep(0.8)
    url = "http://127.0.0.1:%d/?k=%s" % (PORT, _ticket_for_window(token))
    try:
        x, y, w, h = _work_area()
        size, pos = "%d,%d" % (max(900, w - WIDGET_RESERVE), h - TITLEBAR_RESERVE), "--window-position=%d,%d" % (x, y)
        _place_args = (x, y, max(900, w - WIDGET_RESERVE), h - TITLEBAR_RESERVE)
    except Exception:
        size, pos = "1280,1000", "--window-position=0,0"
        _place_args = (0, 0, 1280, 1000)
    ready, reason = _shell_readiness()
    if ready:
        _record_shell("webview2")
        # غلاف بلا إطار بحواف مستديرة مثل الويدجت؛ يضع نفسه بالمقاس والموضع المطلوبين فلا حاجة لـ _place_window
        sx, sy, sw, sh = _place_args
        subprocess.Popen(["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", os.path.join(ROOT, "app_shell.ps1"),
                          "-Url", url, "-X", str(sx), "-Y", str(sy), "-W", str(sw), "-H", str(sh + TITLEBAR_RESERVE)], creationflags=0x08000000)
        return
    for edge in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if os.path.exists(edge):
            prof = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "MonitorWidget", "appwin")  # ملف تعريف منفصل: نافذة جديدة دائماً بالمقاس الصحيح
            os.makedirs(prof, exist_ok=True)
            _record_shell("edge", reason)
            subprocess.Popen([edge, "--app=" + url, "--user-data-dir=" + prof, "--window-size=" + size, pos])
            # A --open helper exits immediately afterward. Keep placement alive
            # long enough to restore the window instead of killing a daemon thread.
            threading.Thread(target=_place_window, args=("مرصد الجهاز",) + _place_args, daemon=False).start()
            return
    _record_shell("browser", reason)
    os.startfile(url)


def post(route, body, invalidate=lambda: None):
    """(رمز الحالة، الردّ) لمسار من ROUTES، أو None إن لم يكن المسار من هذه الوحدة."""
    if route == "/api/shell":   # زر الويدجت يبلّغ محرك النافذة حين يسقط إلى Edge مباشرة؛ غير هدّام
        return record_shell_report(body)
    return None
