"""فحص حي: يتطلب تشغيل الخادم على 127.0.0.1:47832.
python live_check.py            فحوصات للقراءة فقط
python live_check.py --write    يضيف اختبار تبديل تفعيل عنصر بدء تشغيل واحد ثم يعيده (يتطلب موافقة)
"""
import json, os, sys, time, urllib.request, urllib.error, http.cookiejar

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "http://127.0.0.1:47832"
TOKEN_FILE = os.path.join(os.path.dirname(HERE), ".token")
WRITE = "--write" in sys.argv
results = []

def rec(name, ok, detail="", warn=False):
    status = "PASS" if ok else ("WARN" if warn else "FAIL")
    results.append({"name": name, "status": status, "detail": detail})
    print(f"{status} {name}" + (f"  - {detail}" if detail and status != "PASS" else ""))

def req(method, path, headers=None, body=None, follow=False):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", **(headers or {})}
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    t0 = time.time()
    try:
        with opener.open(r, timeout=30) as resp:
            raw = resp.read(); code = resp.status; ctype = resp.headers.get("Content-Type", ""); hd = resp.headers
    except urllib.error.HTTPError as e:
        raw = e.read(); code = e.code; ctype = e.headers.get("Content-Type", ""); hd = e.headers
    except Exception as e:
        return {"code": None, "err": str(e), "ms": 0}
    ms = int((time.time() - t0) * 1000)
    js = None
    if "json" in ctype:
        try: js = json.loads(raw.decode("utf-8"))
        except Exception: js = None
    return {"code": code, "json": js, "raw": raw, "ctype": ctype, "ms": ms, "headers": hd}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None

# 1) الخادم يعمل
hz = req("GET", "/api/healthz")
if hz.get("code") is None:
    rec("server reachable", False, "الخادم لا يستجيب، شغّله أولاً")
    print("\nتوقف الفحص الحي: الخادم غير متاح"); sys.exit(2)
rec("healthz 200 or 503", hz["code"] in (200, 503), str(hz["code"]))
rec("healthz integrity ok", bool(hz["json"] and hz["json"].get("ok")), json.dumps(hz["json"])[:120])

# 2) المصادقة
TOKEN = open(TOKEN_FILE, encoding="utf-8").read().strip()
rec("no token -> 401", req("GET", "/api/state")["code"] == 401)
rec("wrong token -> 401", req("GET", "/api/state", {"X-Token": "bad"})["code"] == 401)
H = {"X-Token": TOKEN}

# 3) تذاكر الدخول لمرة واحدة
t = req("POST", "/api/ticket", H, {})
tick = (t["json"] or {}).get("ticket", "")
rec("ticket issued", bool(tick))
first = req("GET", f"/?k={tick}")
rec("ticket exchange -> 302 + cookie", first["code"] == 302 and "mw_s=" in (first["headers"].get("Set-Cookie") or ""), str(first["code"]))
second = req("GET", f"/?k={tick}")
rec("ticket single-use (second use rejected)", second["code"] != 302, str(second["code"]))

# 4) نقاط النهاية الأساسية: الرمز والشكل والسرعة
ENDPOINTS = {
    "/api/state": ["rules", "tasks"],
    "/api/mini": [],
    "/api/space": ["quarantine"],
    "/api/startup": ["items"],
    "/api/health": [],
    "/api/widget": [],
    "/api/history": [],
    "/api/db-info": [],
    "/api/theme": [],
    "/api/infra": [],
    "/api/jobs": [],
    "/api/actions": [],
}
SLOW_MS = 8000
for path, keys in ENDPOINTS.items():
    r = req("GET", path, H)
    rec(f"GET {path} 200 JSON", r["code"] == 200 and r["json"] is not None, f"code={r['code']}")
    if r["code"] == 200 and r["json"] is not None:
        for k in keys:
            rec(f"{path} has '{k}'", k in r["json"])
        rec(f"{path} latency < {SLOW_MS}ms", r["ms"] < SLOW_MS, f"{r['ms']}ms", warn=True)

# 5) سلامة بيانات بدء التشغيل
st = req("GET", "/api/startup", H)["json"] or {}
items = st.get("items", [])
rec("startup items present", len(items) > 0, f"{len(items)} عنصر")
ids = [i.get("id") for i in items]
rec("startup ids unique", len(ids) == len(set(ids)))
need = {"id", "name", "source", "enabled", "writable", "protected"}
bad = [i.get("name") for i in items if not need.issubset(i.keys())]
rec("startup items have required fields", not bad, ", ".join(map(str, bad[:5])))
rec("protected items are not writable", all(not (i.get("protected") and i.get("writable")) for i in items))

# 6) سلامة قائمة القواعد
rules = (req("GET", "/api/state", H)["json"] or {}).get("rules", [])
if rules:
    rid = [r.get("id") for r in rules]
    rec("rule ids unique", len(rid) == len(set(rid)))
    # الأفعال التي يعرفها CleanupEngine.ps1 (ALLOW_ROOTS وswitch $rule.action): keep-latest وdev-cache أُضيفا مع تنظيفات extras
    allowed = {"purge", "quarantine", "command", "report", "online-only", "dism-report", "recycle-quarantine", "keep-latest", "dev-cache"}
    badact = [r.get("id") for r in rules if r.get("action") not in allowed]
    rec("rule actions valid", not badact, ", ".join(map(str, badact)))
else:
    rec("rules list available", False, "لم تُرجع القواعد", warn=True)

# 7) تبديل تفعيل عنصر بدء تشغيل (اختياري، يعيد الحالة الأصلية)
if WRITE:
    cand = next((i for i in items if i.get("writable") and not i.get("protected") and i.get("source") == "reg-user"), None)
    if cand:
        orig = bool(cand["enabled"])
        r1 = req("POST", "/api/startup", H, {"id": cand["id"], "enabled": not orig})
        rec("toggle startup -> ok", r1["code"] == 200, str(r1["code"]))
        now = next((i for i in (req("GET", "/api/startup", H)["json"] or {}).get("items", []) if i.get("id") == cand["id"]), {})
        rec("toggle startup -> state changed", bool(now.get("enabled")) == (not orig))
        r2 = req("POST", "/api/startup", H, {"id": cand["id"], "enabled": orig})
        rec("toggle startup -> restored", r2["code"] == 200)
    else:
        rec("toggle test candidate", False, "لا يوجد عنصر قابل للتجربة", warn=True)

fails = [r for r in results if r["status"] == "FAIL"]
json.dump(results, open(os.path.join(HERE, "live_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\nالنتيجة الحية:", "نجحت" if not fails else f"{len(fails)} فشل")
sys.exit(1 if fails else 0)
