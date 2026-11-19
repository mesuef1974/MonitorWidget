"""Startup manager: lists what starts with Windows and switches each item on or off (the same switch Task Manager uses)."""
import json, os, subprocess, threading, time, winreg
import db, health

CNW = 0x08000000
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
PROTECTED = {"SecurityHealth", "RtkAudUService", "MonitorWidget-Agent", "MonitorWidget-Launch",
             "MonitorWidget-AdminProbe", "MonitorWidget-Server-Logon", "MiniWidget.lnk"}
FOLDER = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
PWSH = getattr(health, "PWSH", "powershell.exe")
# البند 8 من المجلس 19/11: الخادم مرتفع، فتفعيل مهمة Highest يشغّل ملفها مسؤولًا. userWritable: ملف تنفيذي أو نصي في أوامرها
# (أو مجلده) يكتبه غير المسؤولين أو يملكه، أو ملف تعذّر تحديده؛ عندها يُرفض التفعيل.
TASK_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$trusted = @('S-1-5-18', 'S-1-5-32-544', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
function Test-UserWritable([string]$p, [int]$fileMask) {
  $acl = Get-Acl -LiteralPath $p
  if (-not $acl) { return $true }
  $own = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
  $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
  $ownerRights = @($rules | Where-Object { $_.IdentityReference.Value -eq 'S-1-3-4' }).Count -gt 0
  if ($own -notin $trusted -and -not $ownerRights) { return $true }
  foreach ($a in $rules) {
    if ($a.AccessControlType -ne 'Allow' -or ($a.PropagationFlags -band 2)) { continue }
    $sid = $a.IdentityReference.Value
    if ($sid -in $trusted -or $sid -eq 'S-1-3-4' -or $sid -eq 'S-1-3-0') { continue }
    if ([int]$a.FileSystemRights -band $fileMask) { return $true }
  }
  return $false
}
function Get-Writable($t) {
  $files = @()
  foreach ($a in @($t.Actions)) {
    $exe = [Environment]::ExpandEnvironmentVariables([string]$a.Execute).Trim('"')
    if ($exe -and -not [IO.Path]::IsPathRooted($exe)) { $c = Get-Command $exe -CommandType Application | Select-Object -First 1; $exe = if ($c) { $c.Source } else { '' } }
    if (-not $exe -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) { return $true }
    $files += $exe
    foreach ($m in [regex]::Matches([Environment]::ExpandEnvironmentVariables([string]$a.Arguments), '"([^"]+)"|(\S+)')) {
      $v = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
      if ([IO.Path]::IsPathRooted($v) -and (Test-Path -LiteralPath $v -PathType Leaf)) { $files += $v }
    }
  }
  foreach ($f in $files) {
    # الملف: كتابة أو إلحاق أو حذف أو تغيير الصلاحيات أو المالك؛ مجلده: إنشاء ملف مع حذف ما فيه (استبدال الملف)
    if (Test-UserWritable $f 0x100D0006) { return $true }
    $dir = Split-Path -Parent $f
    $acl = Get-Acl -LiteralPath $dir
    foreach ($a in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
      if ($a.AccessControlType -ne 'Allow' -or ($a.PropagationFlags -band 2)) { continue }
      $sid = $a.IdentityReference.Value
      if ($sid -in $trusted -or $sid -eq 'S-1-3-4' -or $sid -eq 'S-1-3-0') { continue }
      $r = [int]$a.FileSystemRights
      if (($r -band 0x10000000) -or (($r -band 0x2) -and ($r -band 0x40))) { return $true }
    }
    if ((Test-UserWritable $dir 0xC0000)) { return $true }
  }
  return $false
}
Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft*' -and ($_.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' }) } | ForEach-Object {
  $rl = [string]$_.Principal.RunLevel
  [pscustomobject]@{ name = $_.TaskName; path = $_.TaskPath; state = [string]$_.State; runLevel = $rl;
    userWritable = $(if ($rl -eq 'Highest') { [bool](Get-Writable $_) } else { $false });
    cmd = (($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join ' ; ') }
} | ConvertTo-Json -Compress
"""


def _enabled(hive, sub, name):
    """Windows keeps the switch in StartupApproved: an even first byte means enabled, odd means disabled."""
    try:
        with winreg.OpenKey(hive, APPROVED + "\\" + sub) as k:
            v, _ = winreg.QueryValueEx(k, name)
        return bytes(v)[0] % 2 == 0
    except OSError:
        return True  # no record: Windows treats the entry as enabled


def _set_enabled(hive, sub, name, enabled):
    with winreg.CreateKeyEx(hive, APPROVED + "\\" + sub, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_BINARY, bytes([2 if enabled else 3]) + bytes(11))


def _exe(cmd):
    c = (cmd or "").strip()
    if c.startswith('"'):
        end = c.find('"', 1)
        tok = c[1:end] if end > 0 else c[1:]
    else:
        i = c.lower().find(".exe")
        tok = c[:i + 4] if i > 0 else c.split(" ")[0]
    return os.path.basename(os.path.expandvars(tok)).lower()


def _running_map():
    """image name -> (process count, working set in KB), from tasklist."""
    out = {}
    try:
        r = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, timeout=15, creationflags=CNW)
        for line in r.stdout.decode("mbcs", "replace").splitlines():
            parts = [p.strip('"') for p in line.strip().strip('"').split('","')]
            if len(parts) >= 5:
                name = parts[0].lower()
                mem = int("".join(ch for ch in parts[4] if ch.isdigit()) or 0)
                cnt, tot = out.get(name, (0, 0))
                out[name] = (cnt + 1, tot + mem)
    except Exception:
        pass
    return out


def _item(source, name, cmd, enabled, writable, run=None):
    cnt, mem = run or (0, 0)
    return {"id": source + "|" + name, "source": source, "name": name, "command": (cmd or "")[:240],
            "enabled": bool(enabled), "protected": name in PROTECTED, "writable": writable and name not in PROTECTED,
            "running": cnt > 0, "memMB": round(mem / 1024)}


def _tasks():
    try:
        r = subprocess.run([PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", TASK_PS],
                           capture_output=True, timeout=40, creationflags=CNW)
        raw = r.stdout.decode("utf-8", "replace").strip()
        data = json.loads(raw) if raw else []
    except Exception:
        return []
    return [data] if isinstance(data, dict) else data


def _items():
    box = {}
    th = threading.Thread(target=lambda: box.update(t=_tasks()), daemon=True)
    th.start()          # tasklist وPowerShell مستقلان؛ تشغيلهما معًا يجعل الزمن أطولهما لا مجموعهما
    run = _running_map()
    out = []
    for source, hive, writable in (("reg-user", winreg.HKEY_CURRENT_USER, True),
                                   ("reg-machine", winreg.HKEY_LOCAL_MACHINE, False)):
        try:
            with winreg.OpenKey(hive, RUN) as k:
                i = 0
                while True:
                    try:
                        name, val, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    cmd = str(val)
                    out.append(_item(source, name, cmd, _enabled(hive, "Run", name), writable, run.get(_exe(cmd))))
        except OSError:
            pass
    if os.path.isdir(FOLDER):
        for fn in sorted(os.listdir(FOLDER), key=str.lower):
            if fn.lower() == "desktop.ini":
                continue
            out.append(_item("folder", fn, os.path.join(FOLDER, fn),
                             _enabled(winreg.HKEY_CURRENT_USER, "StartupFolder", fn), True))
    th.join()
    for t in box.get("t", []):
        name, path = t.get("name", ""), t.get("path", "\\")
        it = _item("task", name, t.get("cmd", ""), t.get("state") != "Disabled", True)
        it["id"] = "task|" + path + name
        it["path"] = path
        it["runLevel"] = t.get("runLevel", "")
        it["elevatedUnsafe"] = t.get("runLevel") == "Highest" and bool(t.get("userWritable", True))
        out.append(it)
    return out


_LIST = {"t": 0.0, "v": None, "busy": False}
LIST_TTL = 20


def _build():
    its = _items()
    its.sort(key=lambda i: (i["source"], not i["enabled"], i["name"].lower()))
    now = time.time()
    _LIST.update(t=now, v={"items": its, "ts": int(now)})


def _refresh():
    try:
        _build()
    except Exception:
        pass
    finally:
        _LIST["busy"] = False


def list_items():
    now = time.time()
    v = _LIST["v"]
    if v is not None:
        if now - _LIST["t"] >= LIST_TTL and not _LIST["busy"]:
            _LIST["busy"] = True   # القائمة القديمة تُعاد فورًا ويُحدَّث المسح (ثوانٍ) في خيط خلفي
            threading.Thread(target=_refresh, daemon=True).start()
        return v
    _build()
    return _LIST["v"]


def _elevated_unsafe(it):
    """يُعاد الفحص لحظة التفعيل لا من القائمة المخزَّنة (20 ث): الملف قد يتغيّر بينهما. ما لا يُقرأ يُعدّ غير آمن."""
    fresh = next((t for t in _tasks() if t.get("path", "\\") + t.get("name", "") == it["path"] + it["name"]), None)
    if fresh is None:
        return True
    return fresh.get("runLevel") == "Highest" and bool(fresh.get("userWritable", True))


def toggle(body):
    iid = str(body.get("id", ""))
    en = body.get("enabled")
    if not isinstance(en, bool):
        return 400, {"error": "enabled must be boolean"}
    it = next((i for i in list_items()["items"] if i["id"] == iid), None)
    if it is None:
        return 400, {"error": "unknown item"}
    if it["protected"] and not en:
        return 400, {"error": "protected item"}
    if not it["writable"]:
        return 400, {"error": "requires administrator"}
    if it["source"] == "reg-user":
        _set_enabled(winreg.HKEY_CURRENT_USER, "Run", it["name"], en)
    elif it["source"] == "folder":
        _set_enabled(winreg.HKEY_CURRENT_USER, "StartupFolder", it["name"], en)
    elif it["source"] == "task":
        if en and _elevated_unsafe(it):
            return 403, {"error": "مهمة بصلاحية مسؤول ملفها يكتبه المستخدم؛ لا تُفعَّل من هنا"}
        # values travel as environment variables, never interpolated into the command text; the verb comes from a fixed pair
        ps = ("Enable" if en else "Disable") + "-ScheduledTask -TaskName $env:MW_TN -TaskPath $env:MW_TP | Out-Null"
        r = subprocess.run([PWSH, "-NoProfile", "-Command", ps], env=dict(os.environ, MW_TN=it["name"], MW_TP=it["path"]),
                           capture_output=True, timeout=30, creationflags=CNW)
        if r.returncode != 0:
            return 500, {"error": "task change failed"}
    else:
        return 400, {"error": "unsupported"}
    it["enabled"] = en   # القائمة المخزَّنة تُحدَّث في مكانها فتظهر النتيجة فورًا دون مسح جديد
    with db._lock:
        c = db.conn()
        c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)",
                  (int(time.time()), "startup", iid, 1, "enabled=" + str(en).lower()))
        c.commit()
    return 200, {"ok": True, "id": iid, "enabled": en}