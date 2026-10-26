"""فحص محرك التنظيف CleanupEngine.ps1 (ثابت) مع سياسة التنظيف. python engine_check.py [مجلد Cleanup\\v2]"""
import json, os, re, sys
DEFAULT = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Documents", "Scripts", "Cleanup", "v2")
D = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
fails = []
def check(n, ok, d=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok or not d else "  - " + d))
    if not ok: fails.append(n)
eng_p = os.path.join(D, "CleanupEngine.ps1"); pol_p = os.path.join(D, "cleanup-policy.json")
if not os.path.exists(eng_p):
    print("FAIL required engine missing:", eng_p); sys.exit(1)
if not os.path.exists(pol_p):
    print("FAIL required policy missing:", pol_p); sys.exit(1)
eng = open(eng_p, encoding="utf-8", errors="replace").read()
code = re.sub(r"<#.*?#>", "", eng, flags=re.S)
code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("#"))
# 1) توازن الأقواس والأقواس المعقوفة
for o, c in ["{}", "()", "[]"]:
    check(f"balanced {o}{c}", code.count(o) == code.count(c), f"{code.count(o)} vs {code.count(c)}")
# 2) كل إجراء في السياسة له معالج في المحرك
if os.path.exists(pol_p):
    rules = json.load(open(pol_p, encoding="utf-8-sig")).get("rules", [])
    for a in sorted({r["action"] for r in rules}):
        check(f"handler for action '{a}'", f'"{a}"' in eng)
    # 3) مسارات القواعد المفعّلة موجودة على هذا الجهاز (يعمل على ويندوز فقط)
    for r in rules:
        p = r.get("path")
        if r.get("enabled") and p and os.name == "nt":
            check(f"path exists: {r['id']}", os.path.exists(os.path.expandvars(p)), p)
# 4) احترام وضع التجربة
check("DryRun honored", bool(re.search(r"DryRun", code)))
# 5) تسجيل الأحداث موجود
check("event logging present", bool(re.search(r"Write-Event", code)))
# 6) لا يوجد حذف جماعي غير مقيّد
check("no Remove-Item -Recurse on root", not re.search(r"Remove-Item[^\n]*-Recurse[^\n]*\$env:(SystemDrive|USERPROFILE)\b", code))
# 8) OneDrive: الملفات المثبّتة (Always keep, 0x80000) لا تُلمس، ولا يُستخدم -P الذي يلغي التثبيت
check("online-only skips pinned files (0x80000)", "0x80000" in eng)
check("online-only never clears pin (-P)", not re.search(r"attrib\.exe\s+\+U\s+-P", eng))
# 9) تقرير WinSxS للقراءة فقط: لا حذف داخل الدالة، ومسار "unparsed" موجود
m = re.search(r"function Invoke-RuleDismReport \{(.*?)\n\}", code, re.S)
body = m.group(1) if m else ""
check("dism-report function found", bool(m))
check("dism-report deletes nothing", not re.search(r"Remove-Item|\bdel\b|Remove-Item|rmdir", body, re.I) and "/StartComponentCleanup" not in body)
check("dism-report handles unparsed language output", '"unparsed"' in body)
# 10) بوابة minFreeGB موجودة في محرك القواعد
check("minFreeGB gate present", "minFreeGB" in eng and '"gated"' in eng)
print("\nنتيجة المحرك:", "نجحت" if not fails else f"{len(fails)} فشل")
sys.exit(1 if fails else 0)
