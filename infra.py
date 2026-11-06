"""Infrastructure layer: versioned migrations, online backups, integrity checks,
structured rotating logs and request statistics for the monitor backend."""
import os, sqlite3, time, threading, logging, logging.handlers, glob, collections, hashlib, tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "logs")
BK_DIR = os.path.join(ROOT, "backups")
KEEP_BACKUPS = 14
START = time.time()

# ---------- logging ----------
def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    lg = logging.getLogger("monitor")
    if lg.handlers:
        return lg
    lg.setLevel(logging.INFO)
    h = logging.handlers.RotatingFileHandler(os.path.join(LOG_DIR, "server.log"), maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    lg.addHandler(h)
    return lg

log = setup_logging()

# ---------- migrations (PRAGMA user_version) ----------
def _run_sql(c, sql):
    """Run a multi-statement script inside the caller's transaction (a script call would COMMIT first)."""
    for stmt in sql.split(";"):
        if stmt.strip():
            c.execute(stmt)


def _m2(c):
    _run_sql(c, """
    CREATE TABLE IF NOT EXISTS job_runs(
      id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, name TEXT NOT NULL,
      ok INTEGER NOT NULL CHECK(ok IN (0,1)), ms INTEGER, detail TEXT);
    CREATE INDEX IF NOT EXISTS ix_jobs_name_ts ON job_runs(name, ts DESC);
    CREATE INDEX IF NOT EXISTS ix_recs_status ON recs(status, ts DESC);
    """)

def _m3(c):
    _run_sql(c, """
    CREATE TABLE IF NOT EXISTS jobs(
      name TEXT PRIMARY KEY, interval_s INTEGER NOT NULL, mode TEXT NOT NULL DEFAULT 'fixed', next_run INTEGER,
      last_start INTEGER, last_end INTEGER, last_ms INTEGER, last_status TEXT, last_error TEXT,
      runs INTEGER NOT NULL DEFAULT 0, fails INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1, timeout_s INTEGER NOT NULL DEFAULT 600);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, ts INTEGER);
    CREATE TABLE IF NOT EXISTS actions(
      id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, kind TEXT NOT NULL, src TEXT NOT NULL,
      expected_mb REAL, actual_mb REAL, files INTEGER, skipped INTEGER, free_before_gb REAL, free_after_gb REAL, ok INTEGER, detail TEXT);
    CREATE INDEX IF NOT EXISTS ix_actions_ts ON actions(ts DESC);
    CREATE TABLE IF NOT EXISTS fs_live(key TEXT PRIMARY KEY, gb REAL, files INTEGER, ts INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS quarantine(
      id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, orig TEXT NOT NULL, qpath TEXT NOT NULL, bytes INTEGER,
      expires INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'held', action_id INTEGER);
    CREATE INDEX IF NOT EXISTS ix_q_status ON quarantine(status, expires);
    """)

def _m4(c):
    # مستودعات المشروع: سجلّ يكتبه المالك وتعرضه صفحة قاعدة البيانات؛ IF NOT EXISTS يحفظ صفوف القواعد التي سبق إنشاؤه فيها يدويًا
    _run_sql(c, """
    CREATE TABLE IF NOT EXISTS project_repos(
      repo TEXT PRIMARY KEY, visibility TEXT NOT NULL, local_path TEXT, purpose TEXT NOT NULL, ts INTEGER NOT NULL);
    """)

MIGRATIONS = {2: _m2, 3: _m3, 4: _m4}  # v1 = baseline created by db.py / health.py (idempotent DDL)

def migrate(c):
    cur = c.execute("PRAGMA user_version").fetchone()[0]
    target = max(MIGRATIONS) if MIGRATIONS else cur
    if cur >= target:
        return cur
    for v in sorted(MIGRATIONS):
        if v > cur:
            c.execute("BEGIN")
            try:
                MIGRATIONS[v](c)
                c.execute("PRAGMA user_version=%d" % v)
                c.execute("COMMIT")
                log.info("migration applied v%d", v)
            except Exception:
                c.execute("ROLLBACK")
                log.exception("migration v%d failed", v)
                raise
    return target

# ---------- jobs ----------
def record_job(c, name, ok, ms, detail=None):
    import db
    try:
        with db._lock:   # one writer at a time on the shared connection; also keeps a maintenance connection from racing it
            c.execute("INSERT INTO job_runs(ts,name,ok,ms,detail) VALUES(?,?,?,?,?)", (int(time.time()), name, 1 if ok else 0, int(ms), (detail or "")[:300]))
            c.commit()
    except Exception:
        log.exception("record_job")

# ---------- integrity & backup ----------
def integrity(c, full=False):
    r = c.execute("PRAGMA %s" % ("integrity_check" if full else "quick_check")).fetchone()[0]
    return r == "ok", r


# ---------- cached quick_check: no request waits for it ----------
# quick_check يقرأ ملف القاعدة كله (نحو 50 MB؛ 0.1 ث دافئًا وحتى 5-6 ث بعد الإقلاع أو مع انشغال القرص)، وكان يُنفَّذ في كل
# GET /api/infra فتتأخر صفحة التحكم. الآن: آخر نتيجة محفوظة في الذاكرة، وتُجدَّد في خيط خلفي عند تقادمها (TTL) فلا ينتظرها أي طلب.
INTEGRITY_TTL = 300
_INTEG = {"t": 0.0, "ok": None, "msg": "pending", "ms": None, "running": False}
_INTEG_LOCK = threading.Lock()
_integ_thread = None


def _refresh_integrity():
    import db
    t0 = time.time()
    try:
        with db.ro() as c:
            ok, msg = integrity(c)
    except Exception as e:
        ok, msg = False, ("error: %s" % e)[:200]
    with _INTEG_LOCK:
        _INTEG.update(t=time.time(), ok=ok, msg=msg, ms=int((time.time() - t0) * 1000), running=False)


def integrity_cached(ttl=INTEGRITY_TTL):
    """آخر نتيجة quick_check: {t, ok, msg, ms, running}. لا تحجب أبدًا: إن تقادمت (أقدم من ttl ثانية) ولا فحص جارٍ
    يُطلق فحص في خيط خلفي وتُعاد النتيجة السابقة فورًا (قبل أول فحص: ok=None وmsg="pending")."""
    global _integ_thread
    with _INTEG_LOCK:
        if time.time() - _INTEG["t"] > ttl and not _INTEG["running"]:
            _INTEG["running"] = True
            _integ_thread = threading.Thread(target=_refresh_integrity, name="integrity", daemon=True)
            _integ_thread.start()
        return dict(_INTEG)

def _io_deadline(seconds):
    import sched
    bounded = sched.remaining_timeout(seconds) if hasattr(sched, "remaining_timeout") else seconds
    return time.monotonic() + bounded


def _check_io_deadline(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("backup IO deadline exceeded")


def backup(c):
    os.makedirs(BK_DIR, exist_ok=True)
    name = os.path.join(BK_DIR, "monitor-%s.db" % time.strftime("%Y%m%d"))
    tmp = name + ".tmp"
    t0 = time.time()
    dst = sqlite3.connect(tmp)
    try:
        deadline = _io_deadline(120)
        c.backup(dst, pages=256, progress=lambda *_: _check_io_deadline(deadline), sleep=0.05)
        dst.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
        ok, msg = integrity(dst, full=True)
    finally:
        dst.close()
    if not ok:
        os.remove(tmp)
        raise RuntimeError("backup failed integrity: " + msg)
    os.replace(tmp, name)
    files = sorted(glob.glob(os.path.join(BK_DIR, "monitor-*.db")))
    for f in files[:-KEEP_BACKUPS]:
        try:
            os.remove(f)
        except OSError:
            pass
    ms = (time.time() - t0) * 1000
    log.info("backup %s %.0f KB %.0f ms", os.path.basename(name), os.path.getsize(name) / 1024, ms)
    return name, ms

def backups():
    out = []
    for f in sorted(glob.glob(os.path.join(BK_DIR, "monitor-*.db")), reverse=True):
        out.append({"name": os.path.basename(f), "bytes": os.path.getsize(f), "ts": int(os.path.getmtime(f))})
    return out

def _extra_backup(src):
    """Copy the verified daily backup to an owner-chosen folder (setting backup.extra_dir, ideally another disk)."""
    import json, shutil
    import db
    with db.ro() as r:
        row = r.execute("SELECT value FROM settings WHERE key='backup.extra_dir'").fetchone()
    try:
        d = json.loads(row[0]) if row else ""
    except (ValueError, TypeError):
        d = ""
    if not d or not isinstance(d, str):
        return None
    os.makedirs(d, exist_ok=True)
    dst = os.path.join(d, os.path.basename(src))
    deadline = _io_deadline(120)
    handle, tmp = tempfile.mkstemp(prefix=os.path.basename(src) + ".", suffix=".tmp", dir=d)
    try:
        expected = hashlib.sha256()
        with os.fdopen(handle, "wb") as target, open(src, "rb") as source:
            while True:
                _check_io_deadline(deadline)
                block = source.read(1024 * 1024)
                if not block:
                    break
                target.write(block)
                expected.update(block)
            target.flush()
            os.fsync(target.fileno())
        actual = hashlib.sha256()
        with open(tmp, "rb") as copied:
            while True:
                _check_io_deadline(deadline)
                block = copied.read(1024 * 1024)
                if not block:
                    break
                actual.update(block)
        if actual.digest() != expected.digest():
            raise OSError("external backup checksum mismatch")
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    for f in sorted(glob.glob(os.path.join(d, "monitor-*.db")))[:-KEEP_BACKUPS]:
        try:
            os.remove(f)
        except OSError:
            pass
    return dst


def maintenance_if_due(c=None):
    """Daily: backup, PRAGMA optimize, WAL checkpoint; weekly: full integrity check.
    Runs on its own connection (c is accepted for compatibility and ignored) so the shared one is never blocked or committed."""
    import db
    c = db.maint()
    try:
        _maintenance(c)
    finally:
        c.close()


def _maintenance(c):
    now = int(time.time())
    def get(k):
        r = c.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
        return int(r[0]) if r else 0
    def put(k, v):
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
        c.commit()
    if now - get("last_backup") > 86400:
        t0 = time.time()
        try:
            bk, ms = backup(c)
            c.execute("PRAGMA optimize"); c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            record_job(c, "backup", True, ms)
            put("last_backup", now)
        except Exception as e:
            record_job(c, "backup", False, (time.time() - t0) * 1000, str(e))
    try:
        external_backup_retry()
    except Exception:
        log.exception("external backup retry")
    if now - get("last_integrity") > 7 * 86400:
        t0 = time.time()
        ok, msg = integrity(c, full=True)
        record_job(c, "integrity", ok, (time.time() - t0) * 1000, None if ok else msg)
        put("last_integrity", now)

# ---------- request stats ----------
_lock = threading.Lock()
_lat = collections.deque(maxlen=500)
_cnt = collections.Counter()
_err = collections.deque(maxlen=20)

def note_request(path, status, ms):
    with _lock:
        _lat.append(ms)
        _cnt["total"] += 1
        _cnt["s%dxx" % (status // 100)] += 1
        if status >= 500:
            _err.append({"ts": int(time.time()), "path": path, "status": status})

def stats():
    with _lock:
        l = sorted(_lat)
        p = lambda q: round(l[min(len(l) - 1, int(q * len(l)))], 1) if l else None
        return {"uptime_s": int(time.time() - START), "requests": dict(_cnt), "p50_ms": p(.5), "p95_ms": p(.95), "recent_errors": list(_err)}

def overview(c):
    ic = integrity_cached()   # مخزَّن ويُجدَّد في الخلفية: /api/infra لا ينتظر quick_check
    row = lambda sql: c.execute(sql).fetchall()
    jobs = [dict(r) for r in row("SELECT name, ts, ok, ms, detail FROM job_runs WHERE id IN (SELECT MAX(id) FROM job_runs GROUP BY name)")]
    page, pages, free = (c.execute("PRAGMA %s" % p).fetchone()[0] for p in ("page_size", "page_count", "freelist_count"))
    try:
        sched_rows = [dict(r) for r in c.execute("SELECT name, interval_s, mode, next_run, last_start, last_end, last_ms, last_status, last_error, runs, fails FROM jobs ORDER BY name")]
        hb = c.execute("SELECT value FROM meta WHERE key='agent_hb'").fetchone()
    except Exception:
        sched_rows, hb = [], None
    agent = {"alive": bool(hb and time.time() - int(hb[0]) < 60), "heartbeat": int(hb[0]) if hb else None}
    return {"scheduler": sched_rows, "agent": agent, "db": {"integrity": ic["msg"], "ok": ic["ok"], "checkedAt": int(ic["t"]) or None, "checkMs": ic["ms"], "schema": c.execute("PRAGMA user_version").fetchone()[0],
                   "journal": c.execute("PRAGMA journal_mode").fetchone()[0], "bytes": page * pages, "free_pages": free},
            "backups": backups(), "jobs": jobs, "server": stats(), "pid": os.getpid()}


_extra_backup_lock = threading.Lock()


def external_backup_retry():
    """Non-overlapping retries independent from successful local daily backups."""
    if not _extra_backup_lock.acquire(blocking=False):
        return "external backup already running"
    try:
        return _external_backup_retry()
    finally:
        _extra_backup_lock.release()


def _external_backup_retry():
    """Retry only the external destination; never triggers cleanup or DB maintenance."""
    import db
    from contextlib import closing
    with closing(db.maint()) as c:
        now = int(time.time())
        def get(key):
            row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return int(row[0]) if row else 0
        files = sorted(glob.glob(os.path.join(BK_DIR, "monitor-*.db")))
        if not files or now - get("backup_extra_last_attempt") < 300:
            return "not due"
        src = files[-1]
        stamp = int(os.path.basename(src)[8:16])
        row = c.execute("SELECT value FROM settings WHERE key='backup.extra_dir'").fetchone()
        config = row[0] if row else ""
        stat = os.stat(src)
        signature = hashlib.sha256((src + "|" + str(stat.st_size) + "|" + str(stat.st_mtime_ns) + "|" + config).encode("utf-8")).hexdigest()
        last = c.execute("SELECT value FROM meta WHERE key='backup_extra_source_signature'").fetchone()
        if stamp <= get("backup_extra_source_day") and last and last[0] == signature:
            return "current"
        def put(key, value):
            c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
            c.commit()
        put("backup_extra_last_attempt", now)
        t0 = time.time()
        try:
            dst = _extra_backup(src)
            if not dst:
                return "no external destination configured"
            put("backup_extra_source_day", stamp)
            put("backup_extra_source_signature", signature)
            put("backup_extra_last_success", now)
            record_job(c, "backup_extra", True, (time.time() - t0) * 1000, dst)
            return "external backup verified copy complete"
        except Exception as exc:
            record_job(c, "backup_extra", False, (time.time() - t0) * 1000, str(exc))
            raise
