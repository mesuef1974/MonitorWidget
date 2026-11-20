"""فحوصات سريعة لمرصد الجهاز. تشغيل: python tests/check_all.py  (يتطلب node للجزء الخاص بالواجهة)"""
import ast, glob, html.parser, importlib.machinery, os, py_compile, re, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails = []

def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  - " + detail) if detail and not ok else ""))
    if not ok: fails.append(name)

# 1) كل ملفات بايثون تُترجم بدون أخطاء صياغة
for f in sorted(glob.glob(os.path.join(ROOT, "*.py"))):
    try:
        compile(open(f, encoding="utf-8-sig").read(), f, "exec")
        check("compile " + os.path.basename(f), True)
    except (SyntaxError, UnicodeError, OSError) as e:
        check("compile " + os.path.basename(f), False, str(e)[:200])

# 1ب) لا وحدة في المشروع باسم وحدة قياسية (البند 19 من المجلس 19/11: sched.py حجبته sched القياسية تحت ._pth
# المنشور فسقط الجمع 13 دقيقة). وكل وحدة مشروع يستوردها الكود تُحلّ إلى ملف المشروع ولو سبقت المكتبةُ القياسية
# مجلدَ المشروع في sys.path، فلا يتوقف الاستيراد على ترتيب ._pth.
own = {os.path.splitext(os.path.basename(f))[0] for f in glob.glob(os.path.join(ROOT, "*.py"))}
own |= {d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "__init__.py"))}
clash = sorted(own & set(sys.stdlib_module_names))
check("no project module shadows a stdlib module", not clash, ", ".join(clash))
stdlib_first = [p for p in sys.path if p and os.path.abspath(p) != ROOT and "site-packages" not in p] + [ROOT]
used = set()
for f in glob.glob(os.path.join(ROOT, "*.py")) + glob.glob(os.path.join(ROOT, "tests", "*.py")):
    try:
        with open(f, encoding="utf-8-sig") as fh:
            tree = ast.parse(fh.read())
    except (SyntaxError, UnicodeError, OSError):
        continue
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            used |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            used.add(n.module.split(".")[0])
bad = []
for name in sorted(used & own):
    spec = importlib.machinery.PathFinder.find_spec(name, stdlib_first)
    if not (spec and spec.origin and os.path.dirname(os.path.abspath(spec.origin)).startswith(ROOT)):
        bad.append(name)
check("project imports resolve to project files with stdlib first on sys.path", not bad, ", ".join(bad))

src = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()

# 2) الأنماط مركزية في web/css: لا <style> مضمَّنة، الأقواس متوازنة، والألوان لا تُعرَّف إلا في tokens.css
check("no inline <style> blocks", "<style" not in src)
linked = re.findall(r'<link rel="stylesheet" href="/css/([a-z0-9_-]+\.css)"', src)
check("css files linked", len(linked) >= 4, str(linked))
CSSDIR = os.path.join(ROOT, "web", "css")
for name in sorted(os.listdir(CSSDIR)):
    if not name.endswith(".css"):
        continue
    css = open(os.path.join(CSSDIR, name), encoding="utf-8").read()
    css_nc = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    check(f"css linked: {name}", name in linked)
    check(f"css braces balanced: {name}", css_nc.count("{") == css_nc.count("}"), f"{css_nc.count('{')} vs {css_nc.count('}')}")
    if name != "tokens.css":
        raw_colors = re.findall(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)", css_nc)
        check(f"no raw colours outside tokens: {name}", not raw_colors, ", ".join(raw_colors[:5]))

# 3) أكواد JS داخل web/index.html صحيحة الصياغة
scripts = re.findall(r"<script(?![^>]*src=)[^>]*>(.*?)</script>", src, re.S)
sandbox = os.path.join(ROOT, "tests", ".sandboxes")
os.makedirs(sandbox, exist_ok=True)
for i, js in enumerate(scripts):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8", dir=sandbox) as t:
        t.write(js); p = t.name
    r = subprocess.run(["node", "--check", p], capture_output=True, text=True)
    os.unlink(p)
    check(f"js syntax #{i}", r.returncode == 0, r.stderr.strip()[:300])

# 4) لا يوجد معرّف id مكرر
ids = re.findall(r'\sid="([^"]+)"', src)
# pf/pl/tip تتكرر عمداً في قوالب عرض متبادلة (لا تظهر معاً)
ALLOWED_DUP = {"pf", "pl", "tip"}
dups = sorted({x for x in ids if ids.count(x) > 1 and "${" not in x} - ALLOWED_DUP)   # ${..} = placeholder inside a JS template literal, not a real id
check("no duplicate ids", not dups, ", ".join(dups[:10]))

# 5) كل تبويب في القائمة له عرض ومسمى
tabs = re.findall(r'data-tab="([^"]+)"', src)
for t in sorted(set(tabs)):
    check(f"tab '{t}' has view", f"{t}:" in src or f'tab === "{t}"' in src or f"VIEWS.{t}" in src or f'"{t}"' in src)
    check(f"tab '{t}' has title", re.search(r"TITLES\s*=\s*\{[^}]*\b" + t + r"\s*:", src, re.S) is not None or ("TITLES." + t) in src)

# 6) الخادم يعرّف كل المسارات التي تستدعيها الواجهة (في server.py أو في وحدة مسارات تعلن ROUTES بعد التجزئة، البند 6)
server = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
for rf in sorted(glob.glob(os.path.join(ROOT, "*.py"))):
    txt = open(rf, encoding="utf-8-sig").read()
    if os.path.basename(rf) != "server.py" and re.search(r"^ROUTES\s*=", txt, re.M):
        server += txt
called = set(re.findall(r'api\("(/api/[^"?]+)"', src)) | set(re.findall(r"api\(`(/api/[^`?]+)`", src))
called |= set(re.findall(r"['\"](/api/[a-z\-]+)['\"]", src))
for path in sorted(called):
    check("server route " + path, path in server, "غير موجود في server.py ولا في وحدات المسارات")


# 7) منع تكرار خطأين أُصلحا: العنصر المحمي لا يكون قابلاً للتعديل، ولا قاعدة تقول "معطّلة" وهي مفعّلة
sm = open(os.path.join(ROOT, "startup_mgr.py"), encoding="utf-8").read()
check("startup: protected items never writable", '"writable": writable and name not in PROTECTED' in sm or "writable and name not in PROTECTED" in sm)
check("startup: server logon task protected (council 8)", '"MonitorWidget-Server-Logon"' in sm.split("FOLDER =")[0])
check("startup: enabling a Highest task with a user-writable file is refused, rechecked live (council 8)", "if en and _elevated_unsafe(it):" in sm and "fresh = next((t for t in _tasks()" in sm)
pol_candidates = [os.path.join(os.path.expandvars(r"%USERPROFILE%"), "Documents", "Scripts", "Cleanup", "v2", "cleanup-policy.json"),
                  os.path.join(os.path.dirname(ROOT), "Cleanup", "v2", "cleanup-policy.json")]
for pc in pol_candidates:
    if os.path.exists(pc):
        import json as _j
        for r in _j.load(open(pc, encoding="utf-8-sig")).get("rules", []):
            d = (r.get("description") or "")
            if d.startswith("DISABLED") and r.get("enabled"):
                check(f"rule {r['id']}: description says DISABLED but enabled is true", False)
        break

# 8) سكربتات PowerShell: الصنف [\/] في regex الخاص بـ .NET يطابق الشرطة الأمامية وحدها، فمطابقة مسار ويندوز تفشل بصمت
#    (لم يكن install_admin_backend.ps1 يقتل الخلفية القديمة). الصحيح [\\/]. يُرفض في أي .ps1 حتى في التعليقات.
SKIP_DIRS = {".git", ".claude", "node_modules", ".sandboxes", "runtime", "__pycache__", "backups", "quarantine"}  # backups وquarantine مستثنيان من git: نسخ قديمة لا تُفحص
bad_slash = []
for base, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for fn in files:
        if fn.lower().endswith((".ps1", ".psm1")):
            p = os.path.join(base, fn)
            with open(p, encoding="utf-8-sig", errors="replace") as fh:
                lines = fh.read().splitlines()
            bad_slash += [f"{os.path.relpath(p, ROOT)}:{n}" for n, line in enumerate(lines, 1) if re.search(r"(?<!\\)\[\\/\]", line)]
check("ps1: no single-backslash [\\/] class (use [\\\\/])", not bad_slash, ", ".join(bad_slash[:10]))

# 8ب) دالة باسم برنامج أصلي تحجبه: PowerShell لا يفرّق بين الحروف ويقدّم الدالة، فـ "& git" داخل "function Git"
#     يستدعي الدالة نفسها بلا نهاية (فشل deploy_protected.ps1 في 19/11 بـ call depth overflow).
NATIVE = {"git", "icacls", "robocopy", "schtasks", "takeown", "reg", "sc", "netsh", "python", "pythonw", "pwsh",
          "powershell", "cmd", "wscript", "cscript", "dism", "nvidia-smi", "curl", "where", "pnputil", "wevtutil"}
shadow = []
for base, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for fn in files:
        if fn.lower().endswith((".ps1", ".psm1")):
            p = os.path.join(base, fn)
            with open(p, encoding="utf-8-sig", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    m = re.match(r"\s*function\s+([\w-]+)", line, re.I)
                    if m and m.group(1).lower() in NATIVE:
                        shadow.append(f"{os.path.relpath(p, ROOT)}:{n} {m.group(1)}")
check("ps1: no function named after a native command (shadows it)", not shadow, ", ".join(shadow[:10]))

# 9) المهام المرتفعة تُسجَّل من النسخة المحمية في ProgramData لا من مجلد المستخدم القابل للكتابة
def _text(name):
    with open(os.path.join(ROOT, name), encoding="utf-8-sig") as fh:
        return fh.read()
for fn in ("_register_admin_probe.ps1", "register_boot_trace.ps1"):
    t = _text(fn)
    check(f"{fn}: registers from protected ProgramData copy",
          "CommonApplicationData" in t and "ExecutionPolicy Bypass" not in t and "C:\\Users" not in t
          and "{0}\\boot_trace.ps1" not in t and "$root\\healthprobe" not in t)
ra = _text("restart_admin.ps1")
check("restart_admin.ps1: kills by exact folder path only", "-match 'MonitorWidget'" not in ra and "[regex]::Escape($root)" in ra)
# البند 1 من المجلس 19/11: لا مكتبة WebView2 ولا ملف تعريف متصفح من مجلد يكتبه المستخدم في المسار المرتفع
wg, sh, dp = _text("widget.ps1"), _text("app_shell.ps1"), _text("deploy_protected.ps1")
for fn, t in (("widget.ps1", wg), ("restart_admin.ps1", ra)):
    check(f"{fn}: no user-writable WebView2/Edge profile", "TeamsMeetingAdd-in" not in t and "MonitorWidget\\appwin" not in t
          and "MonitorWidget\\webview2" not in t and "shellpaths.ps1" in t)
check("app_shell.ps1: paths from shellpaths, overrides cleared, no Teams copy when elevated",
      "Get-MwShellPaths" in sh and "Clear-MwWebView2Overrides" in sh and "AllowTeamsCopy" in sh
      and "Join-Path $env:LOCALAPPDATA 'MonitorWidget" not in sh)
check("deploy_protected.ps1: protected shell dir + OWNER RIGHTS + admin ownership",
      "Install-ShellDir" in dp and "Test-MicrosoftSigned" in dp and "S-1-3-4" in dp and "Protect-SecureTree" in dp)
check("build_secure_install.py: OWNER RIGHTS and admin ownership", "S-1-3-4" in _text("build_secure_install.py")
      and "/setowner" in _text("build_secure_install.py"))
ua = _text("uninstall_admin_backend.ps1")
check("uninstall_admin_backend.ps1: reverts all backend tasks", "set_backend_privilege.ps1" in ua and "-Level Limited" in ua)

print()
print("النتيجة:", "كل الفحوصات نجحت" if not fails else f"{len(fails)} فحص فشل")
sys.exit(1 if fails else 0)
