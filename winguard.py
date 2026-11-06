"""حارس النوافذ: يكشف المهام المجدولة التي قد تفتح نافذة طرفية فوق عمل المالك، ويُسكت ما يختاره.

سبب النوافذ: pythonw بلا وحدة تحكم، فكل برنامج طرفي يشغّله بلا CREATE_NO_WINDOW يأخذ نافذة جديدة
ويخطف التركيز، وpowershell بـ -WindowStyle Hidden يومض قبل أن يختفي. العلاج تشغيل المهمة تحت
conhost --headless: وحدة تحكم مخفية يرثها كل ما تشغّله المهمة، بلا تعديل كود المشاريع.

    classify(task)          -> (الصنف، قابل للإسكات)   نقية
    wrap(exe, args)         -> (exe, args) الجديدان    نقية
    check(name, tasks=None) -> (ok, reason)            السياسة قبل طلب التأكيد
    silence(name)           -> dict                    نسخة XML ثم تعديل إجراء المهمة والتحقق منه
    payload()               -> حمولة GET /api/winguard (الفحص يجري في خيط خلفي)
    watch()                 خيط: يسجّل كل نافذة طرفية جديدة تظهر على سطح المكتب
"""
import calendar
import collections
import ctypes
import json
import os
import subprocess
import threading
import time
from ctypes import wintypes

import errlog
from paths import CNW, PWSH, ROOT

CONHOST = r"C:\WINDOWS\System32\conhost.exe"
BACKUP_DIR = os.path.join(ROOT, "data", "task_backups")
SCAN_TTL = 300            # ثوانٍ قبل إعادة فحص المهام
WATCH_EVERY = 1.0
OWN_PREFIXES = ("monitorwidget-", "cleanup_v2_")   # مهام التطبيق صامتة ومحميّة: لا تُعدَّل من هنا
DESKTOP_LOGONS = ("interactive", "interactiveorpassword")
TERMINAL_CLASSES = ("CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass")
DOCKER_LOG = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Docker", "log", "host", "com.docker.backend.exe.log")
DOCKER_MARK = b"show-dashboard flag detected"

SCAN_PS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$rows = Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' -and $_.TaskPath -notlike '\Microsoft\*' } | ForEach-Object {
    $a = @($_.Actions)
    [pscustomobject]@{ name = $_.TaskName; path = $_.TaskPath; actions = $a.Count; exe = [string]$a[0].Execute
        args = [string]$a[0].Arguments; logon = [string]$_.Principal.LogonType; level = [string]$_.Principal.RunLevel }
}
ConvertTo-Json -InputObject @($rows) -Compress
"""

# القيم تصل بمتغيرات البيئة لا بلصق النص في الأمر. أي فشل يوقف السكربت قبل Set-ScheduledTask،
# فلا يبقى إجراء سابق في متغير يُطبَّق على مهمة أخرى.
SILENCE_PS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$t = Get-ScheduledTask -TaskName $env:WG_NAME -TaskPath $env:WG_PATH
New-Item -ItemType Directory -Force -Path $env:WG_BACKUP | Out-Null
$file = Join-Path $env:WG_BACKUP ($env:WG_NAME + '.xml')
if (-not (Test-Path -LiteralPath $file)) { Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath | Out-File -LiteralPath $file -Encoding utf8 }
$wd = [string]$t.Actions[0].WorkingDirectory
$p = @{ Execute = $env:WG_EXE; Argument = $env:WG_ARGS }
if ($wd) { $p.WorkingDirectory = $wd }
$new = New-ScheduledTaskAction @p
Set-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Action $new | Out-Null
$c = (Get-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath).Actions[0]
if ($c.Execute -ne $env:WG_EXE -or $c.Arguments -ne $env:WG_ARGS) { throw 'verify failed' }
'ok'
"""

_lock = threading.Lock()
_state = {"tasks": None, "at": 0.0, "busy": False, "error": None}
WINDOWS = collections.deque(maxlen=50)


def classify(task):
    """(الصنف، قابل للإسكات): silent صامتة، children قد يفتح ما تشغّله نوافذ، flash تومض، visible تظهر."""
    exe = os.path.basename(str(task.get("exe", "")).strip('"')).lower()
    args = str(task.get("args", "")).lower()
    if str(task.get("logon", "")).lower() not in DESKTOP_LOGONS:
        return "silent", False            # تعمل خارج جلسة سطح المكتب فلا نافذة لها
    if str(task.get("name", "")).lower().startswith(OWN_PREFIXES):
        return "silent", False
    if exe == "conhost.exe" and "--headless" in args:
        return "silent", False
    single = int(task.get("actions", 1) or 1) == 1
    if exe == "pythonw.exe":
        return "children", single
    if exe in ("powershell.exe", "pwsh.exe"):
        return "flash", single
    if exe in ("python.exe", "cmd.exe"):
        return "visible", single
    return "silent", False


def wrap(exe, args):
    """الإجراء الصامت: pythonw يصير python ليرث وحدة التحكم المخفية، وتبقى المعاملات كما هي."""
    exe = str(exe).strip('"')
    head, tail = os.path.split(exe)
    if tail.lower() == "pythonw.exe":
        exe = os.path.join(head, "python.exe")
    return CONHOST, f'--headless "{exe}" {args or ""}'.strip()


def _run_ps(script, env=None, timeout=60):
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=CNW, env={**os.environ, **(env or {})}, check=False)


def scan():
    """قائمة المهام بصنف كل منها، مرتبة: ما يزعج أولًا."""
    p = _run_ps(SCAN_PS)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "scan failed").strip()[:200])
    rows = json.loads(p.stdout or "[]")
    for r in rows:
        r["kind"], r["fixable"] = classify(r)
    order = {"visible": 0, "children": 1, "flash": 2, "silent": 3}
    return sorted(rows, key=lambda r: (order[r["kind"]], r["name"].lower()))


def _refresh():
    try:
        rows, err = scan(), None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        errlog.swallowed()
        rows, err = None, str(e)[:200]
    with _lock:
        if rows is not None:
            _state.update(tasks=rows, at=time.time())
        _state.update(busy=False, error=err)


def _kick(force=False):
    with _lock:
        stale = force or _state["tasks"] is None or time.time() - _state["at"] > SCAN_TTL
        if _state["busy"] or not stale:
            return
        _state["busy"] = True
    threading.Thread(target=_refresh, daemon=True).start()


def docker_opens(path=DOCKER_LOG, now=None, tail=4 * 1048576):
    """كم مرة فُتحت لوحة Docker في 24 ساعة، من نهاية سجله. {count, last} أو None بلا سجل."""
    now = time.time() if now is None else now
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - tail))
            lines = f.read().split(b"\n")
    except OSError:
        return None
    count, last = 0, None
    for ln in lines:
        if DOCKER_MARK not in ln or len(ln) < 21:
            continue
        try:
            ts = calendar.timegm(time.strptime(ln[1:20].decode("ascii"), "%Y-%m-%dT%H:%M:%S"))   # سجل Docker بتوقيت UTC
        except ValueError:
            continue
        if now - ts <= 86400:
            count, last = count + 1, ts
    return {"count": count, "last": int(last) if last else None}


def payload():
    _kick()
    with _lock:
        rows = _state["tasks"]
        out = {"ready": rows is not None, "busy": _state["busy"], "error": _state["error"], "scannedAt": int(_state["at"]) or None}
    rows = rows or []
    out["tasks"] = [{k: r.get(k) for k in ("name", "exe", "kind", "fixable")} for r in rows if r["kind"] != "silent"]
    out["silent"] = sum(1 for r in rows if r["kind"] == "silent")
    out["windows"] = list(WINDOWS)[::-1]
    out["docker"] = docker_opens()
    return out


def _find(name, tasks=None):
    if tasks is None:
        with _lock:
            tasks = _state["tasks"] or []
    return next((t for t in tasks if t["name"] == name), None)


def check(name, tasks=None):
    """(True, "ok") حين يجوز إسكات المهمة، وإلا السبب: bad params / not_found / not_fixable."""
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        return False, "bad params"
    t = _find(name, tasks)
    if t is None:
        return False, "not_found"
    if not t.get("fixable"):
        return False, "not_fixable"
    return True, "ok"


def silence(name, tasks=None):
    """يعدّل إجراء مهمة واحدة بعد نسخ XML. تحتاج المهام المرتفعة صلاحية لا يملكها الخادم فتُرفض."""
    ok, reason = check(name, tasks)
    if not ok:
        return {"ok": False, "error": reason, "name": name}
    t = _find(name, tasks)
    exe, args = wrap(t["exe"], t["args"])
    p = _run_ps(SILENCE_PS, {"WG_NAME": t["name"], "WG_PATH": t["path"], "WG_BACKUP": BACKUP_DIR, "WG_EXE": exe, "WG_ARGS": args})
    if p.returncode != 0 or "ok" not in (p.stdout or ""):
        msg = (p.stderr or p.stdout or "").strip()
        denied = "access is denied" in msg.lower() or "0x80070005" in msg
        return {"ok": False, "error": "needs_admin" if denied else "failed", "detail": msg[:200], "name": name}
    _kick(force=True)
    return {"ok": True, "name": name, "exe": exe, "args": args}


def _terminal_windows():
    """{hwnd: (الصنف، العنوان)} لنوافذ الطرفية الظاهرة الآن."""
    u32 = ctypes.windll.user32
    found = {}
    cb_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if u32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(80)
            u32.GetClassNameW(hwnd, cls, 80)
            if cls.value in TERMINAL_CLASSES:
                title = ctypes.create_unicode_buffer(200)
                u32.GetWindowTextW(hwnd, title, 200)
                found[hwnd] = (cls.value, title.value)
        return True
    u32.EnumWindows(cb_type(cb), 0)
    return found


def note_new(seen, current, now):
    """يضيف إلى WINDOWS ما ظهر ولم يكن في seen، ويعيد المجموعة الجديدة."""
    for hwnd, (_, title) in current.items():
        if hwnd not in seen:
            WINDOWS.append({"ts": int(now), "title": title[:120]})
    return set(current)


def watch():
    seen = None
    while True:
        try:
            cur = _terminal_windows()
            seen = set(cur) if seen is None else note_new(seen, cur, time.time())
        except OSError:
            errlog.swallowed()
        time.sleep(WATCH_EVERY)
