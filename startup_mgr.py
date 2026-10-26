"""Startup manager: lists what starts with Windows and switches each item on or off (the same switch Task Manager uses)."""
import json, os, subprocess, time, winreg
import db, health

CNW = 0x08000000
RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
PROTECTED = {"SecurityHealth", "RtkAudUService", "MonitorWidget-Agent", "MonitorWidget-Launch",
             "MonitorWidget-AdminProbe", "MiniWidget.lnk"}
FOLDER = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
PWSH = getattr(health, "PWSH", "powershell.exe")
TASK_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft*' -and ($_.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' }) } | ForEach-Object {
  [pscustomobject]@{ name = $_.TaskName; path = $_.TaskPath; state = [string]$_.State;
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
    for t in _tasks():
        name, path = t.get("name", ""), t.get("path", "\\")
        it = _item("task", name, t.get("cmd", ""), t.get("state") != "Disabled", True)
        it["id"] = "task|" + path + name
        it["path"] = path
        out.append(it)
    return out


def list_items():
    its = _items()
    its.sort(key=lambda i: (i["source"], not i["enabled"], i["name"].lower()))
    return {"items": its, "ts": int(time.time())}


def toggle(body):
    iid = str(body.get("id", ""))
    en = body.get("enabled")
    if not isinstance(en, bool):
        return 400, {"error": "enabled must be boolean"}
    it = next((i for i in _items() if i["id"] == iid), None)
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
        # values travel as environment variables, never interpolated into the command text; the verb comes from a fixed pair
        ps = ("Enable" if en else "Disable") + "-ScheduledTask -TaskName $env:MW_TN -TaskPath $env:MW_TP | Out-Null"
        r = subprocess.run([PWSH, "-NoProfile", "-Command", ps], env=dict(os.environ, MW_TN=it["name"], MW_TP=it["path"]),
                           capture_output=True, timeout=30, creationflags=CNW)
        if r.returncode != 0:
            return 500, {"error": "task change failed"}
    else:
        return 400, {"error": "unsupported"}
    with db._lock:
        c = db.conn()
        c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)",
                  (int(time.time()), "startup", iid, 1, "enabled=" + str(en).lower()))
        c.commit()
    return 200, {"ok": True, "id": iid, "enabled": en}