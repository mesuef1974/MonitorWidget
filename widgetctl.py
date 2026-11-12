# -*- coding: utf-8 -*-
"""ويدجت سطح المكتب (البند 6/و من الخارطة): هل يعمل وهل هو ظاهر، إيقافه بأمان (مطابقة سطر أوامر widget.ps1 في هذا المستودع
فقط وإعادة التحقق من وقت الإنشاء حتى لا يُستهدف PID معاد الاستعمال)، تشغيله عبر launch_mini.vbs، وإعادته إلى الشاشة إن كان
موضعه المحفوظ خارجها، مع GET/POST /api/widget. العقود والسلوك كما كانت في server.py حرفيًا.

    ROUTES / post(route, body, invalidate)   POST /api/widget {"show": bool} → 200 {"running": bool}
    status()                                 GET /api/widget: {"running": يعمل وظاهر}
    widget_running() / widget_visible() / widget_stop() / widget_start() / widget_fit_on_screen()
"""
import ctypes
import json
import os
import subprocess
import time

import infra
import sched
from paths import CNW, PWSH, ROOT

ROUTES = ("/api/widget",)
SETTINGS = os.path.join(ROOT, "widget.settings.json")


def widget_visible():
    """False while the widget window is hidden (the process stays alive; the flag is written by widget.ps1)."""
    try:
        return json.load(open(SETTINGS, encoding="utf-8-sig")).get("visible", True) is not False
    except Exception:
        return True


def widget_running():
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "Local\\Azkia-MiniWidget")
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def _widget_process_matches(process):
    """Match this checkout's direct -File invocation, never a substring in -Command."""
    import ntpath
    if not isinstance(process, dict) or str(process.get("Name", "")).casefold() not in ("pwsh.exe", "powershell.exe"):
        return False
    command = process.get("CommandLine")
    if not isinstance(command, str) or not command:
        return False
    argc = ctypes.c_int()
    parse = ctypes.windll.shell32.CommandLineToArgvW
    parse.argtypes = (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int))
    parse.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = parse(command, ctypes.byref(argc))
    if not argv:
        return False
    try:
        args = [argv[i] for i in range(argc.value)]
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = (ctypes.c_void_p,)
        free.restype = ctypes.c_void_p
        free(ctypes.cast(argv, ctypes.c_void_p))
    i = 1
    while i < len(args):
        option = args[i].casefold()
        if option in ("-file", "/file"):
            if i + 1 >= len(args) or not ntpath.isabs(args[i + 1]):
                return False
            expected = ntpath.normcase(ntpath.normpath(ntpath.join(ROOT, "widget.ps1")))
            return ntpath.normcase(ntpath.normpath(args[i + 1])) == expected
        if option in ("-noprofile", "-noninteractive", "-sta", "-mta", "-nologo", "-noexit"):
            i += 1
        elif option in ("-executionpolicy", "-windowstyle", "-workingdirectory", "-inputformat", "-outputformat") and i + 1 < len(args):
            i += 2
        else:
            # Reject command/encoded-command modes and ambiguous abbreviations.
            return False
    return False


def widget_stop():
    # Query only PowerShell hosts; process command lines stay in memory and are
    # never logged. No filesystem path or untrusted string is interpolated into PS.
    query = "$ErrorActionPreference='Stop'; $rows=@(Get-CimInstance Win32_Process -Filter \"Name = 'pwsh.exe' OR Name = 'powershell.exe'\" | Select-Object Name,ProcessId,CommandLine,@{n='CreationTicks';e={$_.CreationDate.ToUniversalTime().Ticks.ToString()}}); ConvertTo-Json -InputObject $rows -Compress"
    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", query], capture_output=True, timeout=sched.remaining_timeout(20), creationflags=CNW)
    if result.returncode != 0:
        raise RuntimeError("widget process query failed")
    try:
        processes = json.loads(result.stdout.decode("utf-8-sig"))
        if isinstance(processes, dict):
            processes = [processes]
        if not isinstance(processes, list):
            raise ValueError("invalid process list")
    except (ValueError, UnicodeError, AttributeError):
        raise RuntimeError("widget process query invalid") from None
    for process in processes:
        if not _widget_process_matches(process):
            continue
        pid, ticks = process.get("ProcessId"), str(process.get("CreationTicks", ""))
        if type(pid) is not int or pid <= 0 or pid == os.getpid() or not ticks.isascii() or not ticks.isdecimal():
            raise RuntimeError("widget process identity invalid")
        # Recheck creation time so PID reuse cannot target a replacement process.
        stop = "$ErrorActionPreference='Stop'; $p=Get-CimInstance Win32_Process -Filter 'ProcessId = %d'; if($p){if($p.CreationDate.ToUniversalTime().Ticks.ToString() -ne '%s'){throw 'identity changed'}; Stop-Process -Id %d -Force -ErrorAction Stop}" % (pid, ticks, pid)
        stopped = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", stop], capture_output=True, timeout=sched.remaining_timeout(20), creationflags=CNW)
        if stopped.returncode != 0:
            raise RuntimeError("widget stop failed")


def widget_start():
    subprocess.Popen(["wscript.exe", os.path.join(ROOT, "launch_mini.vbs")], creationflags=CNW)


def widget_fit_on_screen():
    """If the saved position is off-screen, move the widget back to the top-right of the primary screen."""
    w, h = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    try:
        st = json.load(open(SETTINGS, encoding="utf-8"))
    except Exception:
        st = {}
    left, top = st.get("left"), st.get("top")
    if left is None or top is None or left < 0 or top < 0 or left > w - 120 or top > h - 120:
        st["left"], st["top"] = max(0, w - 372), 0  # top-right, matches the widget default
        json.dump(st, open(SETTINGS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def status():
    return {"running": widget_running() and widget_visible()}


def post(route, body, invalidate=lambda: None):
    """(رمز الحالة، الردّ) لمسار من ROUTES، أو None إن لم يكن المسار من هذه الوحدة."""
    if route != "/api/widget":
        return None
    want = bool(body.get("show"))
    import widgetwatch   # استيراد متأخر: widgetwatch يستورد هذه الوحدة
    widgetwatch.set_wanted(want)   # إخفاء الويدجت من التطبيق قرار من المالك، فلا يعيده مراقب الوكيل
    if want:
        if widget_running():
            widget_stop()
            time.sleep(0.8)
        widget_fit_on_screen()
        widget_start()
        for _ in range(30):
            time.sleep(0.4)
            if widget_running() and widget_visible():
                break
    else:
        widget_stop()
        time.sleep(0.5)
    infra.log.info("widget %s -> running=%s", "show" if want else "hide", widget_running())
    return 200, status()
