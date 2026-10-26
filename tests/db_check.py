"""فحص قاعدة البيانات monitor.db للقراءة فقط."""
import os, sqlite3, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import shutil, tempfile, atexit
SRC = os.path.join(ROOT, "monitor.db")
DB = os.path.join(tempfile.mkdtemp(prefix="mwdb_"), "monitor.db")
atexit.register(lambda: shutil.rmtree(os.path.dirname(DB), ignore_errors=True))
if os.path.exists(SRC):
    shutil.copy2(SRC, DB)
    for ext in ("-wal", "-shm"):
        if os.path.exists(SRC + ext): shutil.copy2(SRC + ext, DB + ext)
fails = []
def check(n, ok, d=""):
    print(("PASS " if ok else "FAIL ") + n + ("" if ok or not d else "  - " + d))
    if not ok: fails.append(n)
if not os.path.exists(DB):
    print("FAIL database missing"); sys.exit(1)
c = sqlite3.connect(DB)  # نسخة مؤقتة: تجنب قفل الملفات على المجلد المربوط
ic = c.execute("PRAGMA integrity_check").fetchone()[0]
check("integrity_check ok", ic == "ok", ic)
tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
for t in ["actions", "cleanup_runs", "igpu", "quarantine", "samples"]:
    check(f"table {t} exists", t in tables)
if "igpu" in tables:
    bad = c.execute("SELECT COUNT(*) FROM igpu WHERE typeof(ts) != 'integer'").fetchone()[0]
    check("igpu.ts is integer everywhere", bad == 0, f"{bad} صف بنوع خاطئ")
if "samples" in tables:
    last = c.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
    if last:
        age = time.time() - (last / 1000 if last > 1e11 else last)
        check("samples fresh (< 15 min)", age < 900, f"آخر عينة قبل {int(age)} ثانية")
    else:
        check("samples present", False)
if "actions" in tables:
    kinds = {r[0] for r in c.execute("SELECT DISTINCT kind FROM actions")}
    check("actions logged", len(kinds) > 0, "لا توجد أفعال مسجلة")
if "cleanup_runs" in tables:
    fail_recent = c.execute("SELECT COUNT(*) FROM cleanup_runs WHERE status NOT IN ('ok','success','dry') AND id IN (SELECT id FROM cleanup_runs ORDER BY id DESC LIMIT 5)").fetchone()[0] if 'status' in [r[1] for r in c.execute("PRAGMA table_info(cleanup_runs)")] else 0
    check("last 5 cleanup runs not failed", fail_recent == 0, f"{fail_recent} فشل")
print("\nنتيجة القاعدة:", "نجحت" if not fails else f"{len(fails)} فشل")
sys.exit(1 if fails else 0)
