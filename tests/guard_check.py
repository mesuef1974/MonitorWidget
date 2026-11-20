"""فحص حارس الذاكرة (حراس): السجل، قاعدة البيانات، والواجهة الحية.
python guard_check.py [مسار memory_guard.log]
الفحوصات الحية تُتخطى تلقائياً إذا لم يكن الخادم يعمل."""
import atexit
import json, os, re, shutil, sqlite3, sys, tempfile, time, urllib.request, urllib.error
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.environ.get("USERPROFILE", os.path.expanduser("~"))
LOG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HOME, ".claude", "logs", "memory_guard.log")
GUARD_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[(\w+)\] commit_before=([\d.]+)% commit_after=([\d.]+)% available_before=([\d.]+)GB available_after=([\d.]+)GB threshold=\S+ actions=(\d+) :: ?(.*)$")
fails, warns = [], []

def check(n, ok, d="", warn=False):
    tag = "PASS" if ok else ("WARN" if warn else "FAIL")
    print(f"{tag} {n}" + ("" if ok or not d else f"  - {d}"))
    if not ok:
        (warns if warn else fails).append(n)

# 1) تحليل الأسطر: نموذج معروف يجب أن يُقبل، وأسطر تالفة يجب أن تُرفض
SAMPLE = "2026-10-17 00:00:00 [APPLY] commit_before=86.0% commit_after=71.5% available_before=2.1GB available_after=3.4GB threshold=85 actions=3 :: freed=512MB procs=claude"
check("parser accepts well-formed line", GUARD_RE.match(SAMPLE) is not None)
check("parser rejects malformed line", GUARD_RE.match("2026-10-17 garbage") is None)
m = GUARD_RE.match(SAMPLE)
check("parser extracts numbers", m and float(m.group(3)) == 86.0 and int(m.group(7)) == 3)

# 2) سجل الحارس الفعلي على الجهاز
if os.path.exists(LOG):
    lines = [l.strip() for l in open(LOG, encoding="utf-8", errors="replace") if l.strip()]
    parsed, junk = [], []
    for ln in lines:
        g = GUARD_RE.match(ln)
        (parsed if g else junk).append(ln if not g else g)
    check("log has runs", len(parsed) > 0, "السجل فارغ من أسطر الحارس", warn=True)
    check("log lines parse", not junk, f"{len(junk)} سطر غير مفهوم، مثال: {junk[0][:80] if junk else ''}", warn=True)
    times = [datetime.strptime(g.group(1), "%Y-%m-%d %H:%M:%S") for g in parsed]
    check("timestamps non-decreasing (15 min tolerance for overlapping runs)", all((a - b).total_seconds() <= 900 for a, b in zip(times, times[1:])))
    if times:
        age_h = (datetime.now() - times[-1]).total_seconds() / 3600
        check("last run < 2h ago", age_h < 2, f"آخر تشغيل قبل {age_h:.1f} ساعة", warn=True)
    for g in parsed:
        mode, cb, ca, n = g.group(2).upper(), float(g.group(3)), float(g.group(4)), int(g.group(7))
        if mode == "APPLY" and n > 0:
            check(f"{g.group(1)} APPLY does not raise commit", ca <= cb + 0.5, f"{cb}% -> {ca}%", warn=True)
        check(f"{g.group(1)} percentages in range", 0 <= cb <= 100 and 0 <= ca <= 100)
    modes = {g.group(2).upper() for g in parsed}
    check("only known modes", modes <= {"APPLY", "DRYRUN"}, ", ".join(modes))
else:
    print("SKIP سجل الحارس غير موجود:", LOG)

# 3) قاعدة البيانات (نسخة مؤقتة لتفادي قفل المجلد المربوط)
SRC = os.path.join(ROOT, "monitor.db")
if os.path.exists(SRC):
    tmp = os.path.join(tempfile.mkdtemp(prefix="mwg_"), "m.db")
    atexit.register(lambda: shutil.rmtree(os.path.dirname(tmp), ignore_errors=True))
    shutil.copy2(SRC, tmp)
    for ext in ("-wal", "-shm"):
        if os.path.exists(SRC + ext): shutil.copy2(SRC + ext, tmp + ext)
    c = sqlite3.connect(tmp)
    rows = c.execute("SELECT ts, commit_pct, avail_gb FROM samples WHERE source='guard' ORDER BY ts").fetchall()
    check("guard samples exist", len(rows) > 0, "لا توجد عينات للحارس", warn=True)
    if rows:
        check("guard commit_pct in 0..100", all(r[1] is None or 0 <= r[1] <= 100 for r in rows))
        check("guard avail_gb non-negative", all(r[2] is None or r[2] >= 0 for r in rows))
        age_h = (time.time() - rows[-1][0]) / 3600
        check("guard samples fresh (< 2h)", age_h < 2, f"آخر عينة قبل {age_h:.1f} ساعة", warn=True)
    ev = c.execute("SELECT COUNT(*) FROM events WHERE source='guard'").fetchone()[0]
    check("guard events table readable", ev >= 0)
else:
    print("SKIP قاعدة البيانات غير موجودة")

# 4) الواجهة الحية: حالة الحارس والعد التنازلي
def get(path, token):
    try:
        r = urllib.request.Request("http://127.0.0.1:47832" + path, headers={"X-Token": token})
        with urllib.request.urlopen(r, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
TOK = os.path.join(ROOT, ".token")
st = get("/api/state", open(TOK, encoding="utf-8").read().strip()) if os.path.exists(TOK) else None
if st is None:
    print("SKIP الواجهة الحية: الخادم غير متاح أو لا يوجد رمز")
else:
    g = st.get("guard")
    check("state exposes guard", g is not None, "حقل guard غائب", warn=True)
    if g:
        check("guard.lastMode known", g.get("lastMode") in (None, "APPLY", "DRYRUN"), str(g.get("lastMode")))
        check("guard acts (applyInArgs)", bool(g.get("applyInArgs")), "الحارس في وضع التجربة فقط ولا يحرر شيئاً", warn=True)
    # قرار المالك 20/11/2026: حُذف التنظيف الخفيف وعدّه التنازلي؛ لا guardNext ولا trim في الحالة
    check("no trim countdown in state", "guardNext" not in st and "trim" not in st, "حقل guardNext أو trim ما زال في الحالة")

print("\nنتيجة الحارس:", "نجحت" if not fails else f"{len(fails)} فشل")
if warns: print("تحذيرات:", len(warns))
sys.exit(1 if fails else 0)
