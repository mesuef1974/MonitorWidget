"""فحص الإقلاع (البند 11 من المجلس 19/11): هل يشغّل شيءٌ مرتفعٌ ملفًا يكتبه المستخدم العادي؟

الخادم مسؤول (قرار 13/11)، فأي ملف تنفيذي أو مكتبة أو نص برمجي يشغّله وهو قابل لكتابة المستخدم = تنفيذ مسؤول لأي برنامج
عادي. يُفحص مرة بعد كل إقلاع للخادم (في خيط خلفي، نحو 10-30 ث) ثلاثة مصادر:
  1. tasks: مهام المجدول خارج \\Microsoft\\ التي تعمل مرتفعة (RunLevel Highest، أو باسم SYSTEM أو خدمة محلية أو مجموعة
     المسؤولين): ملفها التنفيذي وكل ملف بمسار مطلق في وسائطها، ومجلد كل منها.
  2. procs: الخادم نفسه وكل عملية سطر أوامرها في مجلد الكود (الوكيل والويدجت والغلاف) وأبناؤها حتى جيلين (WebView2):
     كل مكتبة محمَّلة فيها فعلًا، مع ملفها التنفيذي.
  3. files: الملفات التنفيذية في المجلد المحمي (الكود ومفسّر Python المعزول وbin والتنظيف ومكتبات الغلاف) و__pycache__.
«قابل لكتابة المستخدم»: الملف يكتبه أو يلحق به أو يحذفه أو يغيّر صلاحياته أو مالكه غيرُ المسؤولين والنظام وTrustedInstaller،
أو يملكه غيرهم بلا قيد OWNER RIGHTS، أو مجلده يسمح لغيرهم بإنشاء ملف مع حذف ما فيه (استبدال)؛ وما تعذّر قراءته يُعدّ غير آمن.
النتيجة: تنبيه حرج في سجل التنبيهات (المصدر audit) لكل إقلاع فيه مخالفة، وGET /api/boot-audit بالتفاصيل. الرفض الفعلي
لتفعيل مهمة مخالفة في startup_mgr (البند 8) يستعمل الدوال نفسها في WRITABLE_PS. بلا صلاحية مسؤول لا معنى للفحص (كل شيء
يعمل بصلاحية المستخدم أصلًا) فالحالة skipped.
"""
import json
import os
import subprocess
import threading
import time

ROUTES = ("/api/boot-audit",)
DELAY_S = 90            # بعد الإقلاع: الويدجت والغلاف وWebView2 تكون قد حمّلت مكتباتها
TIMEOUT_S = 180
MAX_LIST = 50           # حدّ كل قائمة في الردّ وفي نص التنبيه
EXEC_EXT = (".exe", ".dll", ".pyd", ".py", ".pyc", ".pyw", ".ps1", ".psm1", ".psd1", ".vbs", ".js", ".cmd", ".bat", ".cs", ".sys")

# دوال PowerShell المشتركة مع startup_mgr (لا تكرار): Test-UserWritable وTest-DirReplaceable وTest-FileUnsafe
# وGet-UnsafeFiles (الملفات المخالفة في أوامر مهمة؛ ما لا يُحدَّد يُعاد «?» متبوعًا بالأمر) وGet-Writable (منطقي).
WRITABLE_PS = r"""
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
    if ($sid -in $trusted -or $sid -eq 'S-1-3-0') { continue }
    # OWNER RIGHTS يمنح المالك نفسه: آمن إن كان المالك موثوقًا، وإلا فهي صلاحيات المستخدم (خطأ البند 8 حتى 20/11)
    if ($sid -eq 'S-1-3-4' -and $own -in $trusted) { continue }
    if ([int]$a.FileSystemRights -band $fileMask) { return $true }
  }
  return $false
}
$dirMemo = @{}
function Test-DirReplaceable([string]$dir) {
  # المجلد: إنشاء ملف مع حذف ما فيه (استبدال الملف)، أو كل الصلاحيات، أو تغيير صلاحياته أو مالكه
  if ($dirMemo.ContainsKey($dir)) { return $dirMemo[$dir] }
  $bad = $false
  $acl = Get-Acl -LiteralPath $dir
  foreach ($a in @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))) {
    if ($a.AccessControlType -ne 'Allow' -or ($a.PropagationFlags -band 2)) { continue }
    $sid = $a.IdentityReference.Value
    if ($sid -in $trusted -or $sid -eq 'S-1-3-0') { continue }
    if ($sid -eq 'S-1-3-4' -and $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -in $trusted) { continue }
    $r = [int]$a.FileSystemRights
    if (($r -band 0x10000000) -or (($r -band 0x2) -and ($r -band 0x40))) { $bad = $true; break }
  }
  if (-not $bad) { $bad = [bool](Test-UserWritable $dir 0xC0000) }
  $dirMemo[$dir] = $bad
  return $bad
}
$fileMemo = @{}
function Test-FileUnsafe([string]$f) {
  # الملف: كتابة أو إلحاق أو حذف أو تغيير الصلاحيات أو المالك؛ ثم مجلده
  if ($fileMemo.ContainsKey($f)) { return $fileMemo[$f] }
  $bad = [bool](Test-UserWritable $f 0x100D0006)
  if (-not $bad) { $bad = [bool](Test-DirReplaceable (Split-Path -Parent $f)) }
  $fileMemo[$f] = $bad
  return $bad
}
function Get-UnsafeFiles($t) {
  foreach ($a in @($t.Actions)) {
    $exe = [Environment]::ExpandEnvironmentVariables([string]$a.Execute).Trim('"')
    if ($exe -and -not [IO.Path]::IsPathRooted($exe)) { $c = Get-Command $exe -CommandType Application | Select-Object -First 1; $exe = if ($c) { $c.Source } else { '' } }
    if (-not $exe -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) { '?' + [string]$a.Execute; continue }
    $files = @($exe)
    foreach ($m in [regex]::Matches([Environment]::ExpandEnvironmentVariables([string]$a.Arguments), '"([^"]+)"|(\S+)')) {
      $v = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
      if ([IO.Path]::IsPathRooted($v) -and (Test-Path -LiteralPath $v -PathType Leaf)) { $files += $v }
    }
    foreach ($f in $files) { if (Test-FileUnsafe $f) { $f } }
  }
}
function Get-Writable($t) { return @(Get-UnsafeFiles $t).Count -gt 0 }
"""

# الفحص نفسه: القيم تصل بمتغيرات البيئة لا بنص الأمر.
AUDIT_PS = WRITABLE_PS + r"""
$elevatedSids = @('S-1-5-18', 'S-1-5-19', 'S-1-5-20', 'SYSTEM', 'LOCAL SERVICE', 'NETWORK SERVICE', 'NT AUTHORITY\SYSTEM')
$checked = 0
$tasks = @(foreach ($t in Get-ScheduledTask) {
  if ($t.TaskPath -like '\Microsoft\*') { continue }
  $pr = $t.Principal
  $high = ([string]$pr.RunLevel -eq 'Highest') -or ([string]$pr.UserId -in $elevatedSids) -or ([string]$pr.GroupId -match 'S-1-5-32-544|Administrators')
  if (-not $high) { continue }
  $checked++
  $bad = @(Get-UnsafeFiles $t)
  if ($bad.Count) { [pscustomobject]@{ name = $t.TaskName; path = $t.TaskPath; state = [string]$t.State; bad = $bad } }
})
$root = $env:MW_ROOT
$all = @(Get-CimInstance Win32_Process)
$sel = @{}
foreach ($p in $all) {
  if ($p.ProcessId -eq [int]$env:MW_PID -or ($p.CommandLine -and $p.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0)) { $sel[[int]$p.ProcessId] = $p }
}
foreach ($gen in 1, 2) {
  foreach ($p in $all) { if (-not $sel.ContainsKey([int]$p.ProcessId) -and $sel.ContainsKey([int]$p.ParentProcessId)) { $sel[[int]$p.ProcessId] = $p } }
}
$procs = @(foreach ($id in $sel.Keys) {
  $gp = Get-Process -Id $id
  if (-not $gp) { continue }
  $mods = @($gp.Modules | ForEach-Object { $_.FileName } | Where-Object { $_ } | Sort-Object -Unique)
  if (-not $mods.Count) { [pscustomobject]@{ pid = $id; name = $sel[$id].Name; unreadable = $true; bad = @() }; continue }
  $checked += $mods.Count
  $bad = @($mods | Where-Object { Test-FileUnsafe $_ })
  if ($bad.Count) { [pscustomobject]@{ pid = $id; name = $sel[$id].Name; unreadable = $false; bad = $bad } }
})
$ext = $env:MW_EXT -split ';'
$files = @(foreach ($d in ($env:MW_DIRS -split ';')) {
  if (-not $d -or -not (Test-Path -LiteralPath $d -PathType Container)) { continue }
  $deep = $d.EndsWith('*')
  $d = $d.TrimEnd('*')
  if (Test-DirReplaceable $d) { $d }
  foreach ($f in @(Get-ChildItem -LiteralPath $d -File -Force -Recurse:$deep)) {
    if ($f.Extension.ToLowerInvariant() -notin $ext) { continue }
    $checked++
    if (Test-FileUnsafe $f.FullName) { $f.FullName }
  }
})
[pscustomobject]@{ tasks = $tasks; procs = $procs; files = $files; checked = $checked } | ConvertTo-Json -Depth 5 -Compress
"""

_STATE = {"state": "pending", "ts": None, "ms": None, "checked": 0, "tasks": [], "procs": [], "files": [], "error": None}
_lock = threading.Lock()


def _scan_dirs():
    """مجلدات الملفات التنفيذية في التثبيت المحمي؛ «*» في آخر المسار = مع المجلدات الفرعية."""
    import secure
    from paths import ROOT
    s = secure.SECURE_DIR
    return [ROOT, os.path.join(ROOT, "__pycache__"), os.path.join(ROOT, "runtime") + "*", os.path.join(s, "python") + "*",
            secure.BIN + "*", os.path.join(s, "cleanup") + "*", os.path.join(s, "shell") + "*"]


def _as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def parse(raw):
    """يحوّل ناتج AUDIT_PS إلى (tasks, procs, files, checked) بقوائم محدودة؛ يرمي ValueError إن لم يكن بالشكل المتوقع."""
    d = json.loads(raw)
    if not isinstance(d, dict):
        raise TypeError("audit output is not an object")
    tasks = [{"name": str(t.get("name", "")), "path": str(t.get("path", "")), "state": str(t.get("state", "")),
              "bad": [str(x) for x in _as_list(t.get("bad"))][:MAX_LIST]} for t in _as_list(d.get("tasks")) if isinstance(t, dict)]
    procs = [{"pid": int(p.get("pid") or 0), "name": str(p.get("name", "")), "unreadable": bool(p.get("unreadable")),
              "bad": [str(x) for x in _as_list(p.get("bad"))][:MAX_LIST]} for p in _as_list(d.get("procs")) if isinstance(p, dict)]
    files = [str(f) for f in _as_list(d.get("files"))]
    return tasks[:MAX_LIST], procs[:MAX_LIST], files[:MAX_LIST], int(d.get("checked") or 0)


def violations(st):
    """عدد المخالفات: كل ملف في مهمة مفعّلة أو عملية، وكل ملف في المجلد المحمي. المهمة المعطّلة تُعرض ولا تُعدّ (تفعيلها
    يحتاج مسؤولًا، ومن صفحة بدء التشغيل يُرفض: البند 8)، والعملية التي لا تُقرأ مكتباتها ليست مخالفة."""
    return sum(len(t["bad"]) for t in st["tasks"] if t["state"] != "Disabled") + sum(len(p["bad"]) for p in st["procs"]) + len(st["files"])


def _summary(st):
    parts = [f"مهمة {t['path']}{t['name']}: {'، '.join(t['bad'][:3])}" for t in st["tasks"] if t["state"] != "Disabled"][:5]
    parts += [f"عملية {p['name']} ({p['pid']}): {'، '.join(p['bad'][:3])}" for p in st["procs"] if p["bad"]][:5]
    parts += st["files"][:5]
    return " | ".join(parts)


def run_once():
    """يشغّل الفحص ويحدّث الحالة ويسجّل تنبيهًا حرجًا عند المخالفة. يعيد الحالة."""
    import infra
    import secure
    from paths import CNW, PWSH, ROOT
    if not secure.is_admin():
        with _lock:
            _STATE.update(state="skipped", ts=int(time.time()), error=None)
        return dict(_STATE)
    t0 = time.time()
    env = dict(os.environ, MW_ROOT=ROOT, MW_PID=str(os.getpid()), MW_DIRS=";".join(_scan_dirs()), MW_EXT=";".join(EXEC_EXT))
    try:
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", AUDIT_PS],
                           env=env, capture_output=True, timeout=TIMEOUT_S, creationflags=CNW, check=False)
        tasks, procs, files, checked = parse(r.stdout.decode("utf-8", "replace").strip() or "null")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as e:
        with _lock:
            _STATE.update(state="error", ts=int(time.time()), error=str(e)[:200])
        infra.log.warning("boot audit failed: %s", e)
        return dict(_STATE)
    with _lock:
        _STATE.update(tasks=tasks, procs=procs, files=files, checked=checked, ts=int(time.time()),
                      ms=int((time.time() - t0) * 1000), error=None)
        n = violations(_STATE)
        _STATE["state"] = "bad" if n else "ok"
        st = dict(_STATE)
    infra.log.info("boot audit: %s checked=%d violations=%d ms=%d", st["state"], checked, n, st["ms"])
    if n:
        import db
        db.add_event(st["ts"], "audit", "crit", f"فحص الإقلاع: ملفات يكتبها المستخدم ويشغّلها شيء مرتفع ({n})", _summary(st)[:1000])
    return st


def watch():
    """خيط الإقلاع: ينتظر DELAY_S ثم يفحص مرة واحدة."""
    time.sleep(DELAY_S)
    run_once()


def payload():
    with _lock:
        return dict(_STATE, violations=violations(_STATE))
