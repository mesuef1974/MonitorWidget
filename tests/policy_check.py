"""فحص ملف سياسة التنظيف. python policy_check.py [مسار cleanup-policy.json]"""
import json, os, sys
DEFAULT = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Documents", "Scripts", "Cleanup", "v2", "cleanup-policy.json")
path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
fails = []
def check(n, ok, d=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok or not d else "  - " + d))
    if not ok: fails.append(n)
if not os.path.exists(path):
    print("FAIL required policy not found:", path); sys.exit(1)
try:
    data = json.load(open(path, encoding="utf-8-sig"))
except Exception as e:
    print("FAIL policy parses", e); sys.exit(1)
rules = data.get("rules", data) if isinstance(data, dict) else data
check("policy parses", True)
ids = [r.get("id") for r in rules]
check("rule ids unique", len(ids) == len(set(ids)))
TIERS = {"daily", "weekly", "monthly", "quarterly"}
ACTS = {"purge", "quarantine", "command", "report", "online-only", "dism-report", "recycle-quarantine"}   # recycle-quarantine: CleanupEngine.ps1 dispatch
for r in rules:
    rid = r.get("id", "?")
    check(f"{rid}: tier valid", r.get("tier") in TIERS, str(r.get("tier")))
    check(f"{rid}: action valid", r.get("action") in ACTS, str(r.get("action")))
    check(f"{rid}: enabled is bool", isinstance(r.get("enabled"), bool))
    if r.get("enabled") and r.get("action") in ("purge", "quarantine", "online-only"):
        p = (r.get("path") or "").lower()
        check(f"{rid}: path set", bool(p))
        check(f"{rid}: no protected project path", not any(x in p for x in ("schoolos", "sma", "azkia")),
              "مسار مشروع محمي ضمن قاعدة مفعّلة")
    if "olderThanDays" in r:
        check(f"{rid}: olderThanDays int >= 0", isinstance(r["olderThanDays"], int) and r["olderThanDays"] >= 0)
print("\nنتيجة السياسة:", "نجحت" if not fails else f"{len(fails)} فشل")
sys.exit(1 if fails else 0)
