"""فحوصات سريعة لمرصد الجهاز. تشغيل: python tests/check_all.py  (يتطلب node للجزء الخاص بالواجهة)"""
import glob, os, py_compile, re, subprocess, sys, tempfile, html.parser

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

src = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()

# 2) وسوم <style> متوازنة وكل كتلة CSS لها أقواس متوازنة
styles = re.findall(r"<style[^>]*>(.*?)</style>", src, re.S)
check("style blocks found", len(styles) >= 1)
for i, css in enumerate(styles):
    css_nc = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    check(f"css braces balanced #{i}", css_nc.count("{") == css_nc.count("}"),
          f"{css_nc.count('{')} vs {css_nc.count('}')}")
check("style tags balanced", src.count("<style") == src.count("</style>"))

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

# 6) الخادم يعرّف كل المسارات التي تستدعيها الواجهة
server = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
called = set(re.findall(r'api\("(/api/[^"?]+)"', src)) | set(re.findall(r"api\(`(/api/[^`?]+)`", src))
called |= set(re.findall(r"['\"](/api/[a-z\-]+)['\"]", src))
for path in sorted(called):
    check("server route " + path, path in server, "غير موجود في server.py")


# 7) منع تكرار خطأين أُصلحا: العنصر المحمي لا يكون قابلاً للتعديل، ولا قاعدة تقول "معطّلة" وهي مفعّلة
sm = open(os.path.join(ROOT, "startup_mgr.py"), encoding="utf-8").read()
check("startup: protected items never writable", '"writable": writable and name not in PROTECTED' in sm or "writable and name not in PROTECTED" in sm)
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

print()
print("النتيجة:", "كل الفحوصات نجحت" if not fails else f"{len(fails)} فحص فشل")
sys.exit(1 if fails else 0)
