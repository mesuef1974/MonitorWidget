"""SQLite history store for the monitor (stdlib only). One file next to the server: monitor.db (WAL).
Tables: samples, events, cleanup_runs, trims, meta. Importers are idempotent (UNIQUE keys + byte offsets in meta)."""
import contextlib, json, os, re, sqlite3, threading, time

ROOT = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("USERPROFILE", "")
DB_PATH = os.path.join(ROOT, "monitor.db")
GUARD_LOG = HOME + r"\.claude\logs\memory_guard.log"
MC_LOG = HOME + r"\Documents\Scripts\MachineCare\logs\machinecare.log"
RUNS = HOME + r"\Documents\Scripts\Cleanup\logs\runs.jsonl"
SCHEMA_VERSION = 3  # target schema version; actual databases report PRAGMA user_version

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples(
  ts INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'live',
  commit_pct REAL, ram_pct REAL, avail_gb REAL, disk_free_gb REAL, claude INTEGER, shells INTEGER,
  PRIMARY KEY(ts, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, source TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'info',
  title TEXT NOT NULL, detail TEXT, UNIQUE(ts, source, title));
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts DESC);
CREATE INDEX IF NOT EXISTS ix_events_sev ON events(severity, ts DESC);
CREATE TABLE IF NOT EXISTS cleanup_runs(
  run_id TEXT PRIMARY KEY, ts INTEGER NOT NULL, tier TEXT, dry INTEGER, rules_run INTEGER, rules_skip INTEGER, rules_failed INTEGER,
  files INTEGER, bytes_freed INTEGER, duration REAL, free_before REAL, free_after REAL);
CREATE INDEX IF NOT EXISTS ix_runs_ts ON cleanup_runs(ts DESC);
CREATE TABLE IF NOT EXISTS trims(
  ts INTEGER PRIMARY KEY, before_pct REAL, after_pct REAL, freed_mb REAL, ms INTEGER, mode TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE VIEW IF NOT EXISTS v_daily AS
  SELECT date(ts,'unixepoch','localtime') AS day, COUNT(*) AS n,
         ROUND(AVG(commit_pct),1) AS commit_avg, ROUND(MAX(commit_pct),1) AS commit_max,
         ROUND(AVG(ram_pct),1) AS ram_avg, ROUND(MAX(ram_pct),1) AS ram_max, ROUND(MIN(avail_gb),2) AS avail_min
  FROM samples GROUP BY day;
"""


def conn():
    global _conn
    with _lock:
        if _conn is None:
            c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA synchronous=NORMAL"); c.execute("PRAGMA foreign_keys=ON")
            c.executescript(SCHEMA)
            try:
                import diskio
                diskio.init(c)          # disk I/O tables + views + default thresholds (additive, idempotent)
            except Exception:
                import errlog
                errlog.swallowed()
            c.execute("INSERT OR IGNORE INTO meta VALUES('schema_version',?)", (str(c.execute("PRAGMA user_version").fetchone()[0]),))
            # io_samples has PK(ts,dev): per-device latest/MAX(ts) needs (dev,ts); samples/gpu already have a PK on ts
            try:
                c.execute("CREATE INDEX IF NOT EXISTS ix_io_samples_dev_ts ON io_samples(dev, ts)")
                c.execute("CREATE INDEX IF NOT EXISTS ix_io_stalls_dev ON io_stalls(dev, ts_start)")
            except sqlite3.Error:
                import errlog
                errlog.swallowed()
            c.execute("DROP INDEX IF EXISTS ix_samples_ts"); c.execute("DROP INDEX IF EXISTS ix_gpu_ts")
            c.commit()
            _conn = c
        return _conn


def bootstrap():
    """Initialize every entry point in the same order, without starting probe workers."""
    import health, infra
    with _lock:
        c = conn()
        c.executescript(health.SCHEMA)
        c.commit()
        version = infra.migrate(c)
        c.execute("INSERT INTO meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(version),))
        c.commit()
        return c


@contextlib.contextmanager
def ro():
    """Own read-only connection (WAL allows it beside the writer): heavy or frequent reads never hold _lock."""
    c = sqlite3.connect("file:%s?mode=ro" % DB_PATH.replace("\\", "/"), uri=True, timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA query_only=ON")
        yield c
    finally:
        c.close()


def maint():
    """Own read-write connection for maintenance (backup, integrity, settings read); the caller closes it."""
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA synchronous=NORMAL"); c.execute("PRAGMA busy_timeout=30000")
    return c


def _meta_get(k, d=None):
    r = conn().execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
    return r[0] if r else d


def _meta_set(k, v):
    conn().execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))


def _epoch(s):
    """'2026-10-16 15:20:02' or ISO with offset -> epoch seconds."""
    s = s.strip()
    try:
        if re.search(r"[+-]\d\d:\d\d$|Z$", s):
            from datetime import datetime
            return int(datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))).timestamp())
        return int(time.mktime(time.strptime(s[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")))
    except Exception:
        return None


# ------------------------------------------------------------------ writers
def add_sample(ts, m, source="live"):
    with _lock:
        conn().execute("INSERT OR REPLACE INTO samples VALUES(?,?,?,?,?,?,?,?)",
                       (int(ts), source, m.get("commitPct"), m.get("ramPct"), m.get("availGB"), m.get("diskFreeGB"), m.get("claude"), m.get("shells")))
        conn().commit()


def add_event(ts, source, severity, title, detail=None):
    with _lock:
        conn().execute("INSERT OR IGNORE INTO events(ts,source,severity,title,detail) VALUES(?,?,?,?,?)", (int(ts), source, severity, title, detail))
        conn().commit()


def add_trim(ts, before, after, freed_mb, ms, mode):
    with _lock:
        conn().execute("INSERT OR REPLACE INTO trims VALUES(?,?,?,?,?,?)", (int(ts), before, after, freed_mb, ms, mode))
        c = conn()
        c.execute("INSERT OR IGNORE INTO events(ts,source,severity,title,detail) VALUES(?,?,?,?,?)",
                  (int(ts), "trim", "info", "تنظيف الذاكرة", "%s%% ← %s%% · %.2f GB · %s" % (before, after, (freed_mb or 0) / 1024, "كامل" if mode == "full" else "مستخدم فقط")))
        c.commit()


def set_json(key, value):
    """Small JSON document kept in the meta table (replaces side-car files)."""
    with _lock:
        c = conn()
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value, ensure_ascii=False)))
        c.commit()


def get_json(key, default=None):
    with _lock:
        r = conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    try:
        return json.loads(r[0]) if r else default
    except Exception:
        return default


def note_tasks(tasks):
    """Record a task's last run as an event once (dedup by ts+title)."""
    sev = {0: "ok", 267014: "warn", 267009: "info", 267011: "info"}
    for t in tasks or []:
        if t.get("missing") or not t.get("last"):
            continue
        ts = _epoch(str(t["last"]))
        if not ts or ts < 86400 * 365:
            continue
        code = t.get("result")
        s = sev.get(code, "bad") if code is not None else "info"
        title = "مهمة: %s" % t.get("label", t.get("name"))
        add_event(ts, "task", s, title, "النتيجة 0x%X" % (code & 0xFFFFFFFF) if code not in (None, 0) else "نجحت")


# ------------------------------------------------------------------ importers (idempotent, incremental)
def _read_new(path, key):
    if not os.path.exists(path):
        return [], None
    size = os.path.getsize(path)
    off = int(_meta_get(key, 0))
    if size < off:
        off = 0
    with open(path, "rb") as f:
        f.seek(off)
        data = f.read()
    # only consume complete lines
    cut = data.rfind(b"\n")
    if cut < 0:
        return [], None
    # the offset is returned, not stored: import_all stores it after the rows are inserted
    return data[:cut].decode("utf-8", "replace").splitlines(), off + cut + 1


GUARD_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[(\w+)\] commit_before=([\d.]+)% commit_after=([\d.]+)% available_before=([\d.]+)GB available_after=([\d.]+)GB threshold=\S+ actions=(\d+) :: ?(.*)$")
MC_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]\[([^\]]+)\]\[(\w+)\]\s*(.*)$")


def _bad_line(src, ln):
    try:
        import errlog  # noqa: F401  (sets up the "monitor" logger)
        import logging
        logging.getLogger("monitor").warning("db.import: skipped bad %s line: %s", src, ln[:120])
    except Exception:
        pass


def import_all():
    n = {"guard": 0, "mc": 0, "runs": 0}
    with _lock:
        c = conn()
        offs = {}
        lines, offs["off_guard"] = _read_new(GUARD_LOG, "off_guard")
        for ln in lines:
            m = GUARD_RE.match(ln.strip())
            if not m:
                continue
            ts = _epoch(m.group(1))
            if not ts:
                _bad_line("guard", ln)
                continue
            c.execute("INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?,?,?,?)", (ts, "guard", float(m.group(4)), None, float(m.group(6)), None, None, None))
            fr = re.search(r"freed=(\d+)MB", m.group(8))
            if int(m.group(7)) > 0 and m.group(2).upper() == "APPLY" and fr and int(fr.group(1)) >= 300:
                c.execute("INSERT OR IGNORE INTO events(ts,source,severity,title,detail) VALUES(?,?,?,?,?)",
                          (ts, "guard", "info", "حارس الذاكرة نفّذ إجراءً", "%s · حُرّر %s MB" % (m.group(8).strip()[:80], fr.group(1))))
            n["guard"] += 1
        lines, offs["off_mc"] = _read_new(MC_LOG, "off_mc")
        for ln in lines:
            m = MC_RE.match(ln.strip())
            if not m:
                continue
            ts = _epoch(m.group(1)); lv = m.group(3).upper()
            if not ts:
                _bad_line("mc", ln)
                continue
            sev = {"WARN": "warn", "ERROR": "bad", "ALERT": "bad", "KILLED": "bad", "INFO": "info"}.get(lv, "info")
            c.execute("INSERT OR IGNORE INTO events(ts,source,severity,title,detail) VALUES(?,?,?,?,?)", (ts, m.group(2).lower(), sev, m.group(4)[:160], None))
            n["mc"] += 1
        lines, offs["off_runs"] = _read_new(RUNS, "off_runs")
        for ln in lines:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            ts = _epoch(r.get("Started", ""))
            if not ts or not r.get("RunId"):
                continue
            c.execute("INSERT OR REPLACE INTO cleanup_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (r["RunId"], ts, r.get("Tier"), 1 if r.get("DryRun") else 0, r.get("RulesRun"), r.get("RulesSkip"), r.get("RulesFailed"),
                       r.get("FilesActed"), r.get("BytesFreed"), r.get("Duration"), r.get("FreeGBBefore"), r.get("FreeGBAfter")))
            if r.get("RulesFailed"):
                c.execute("INSERT OR IGNORE INTO events(ts,source,severity,title,detail) VALUES(?,?,?,?,?)",
                          (ts, "cleanup", "bad", "فشل في تنظيف %s" % r.get("Tier"), "قواعد فاشلة: %s" % r.get("RulesFailed")))
            n["runs"] += 1
        # offsets advance only now, in the same transaction as the inserts
        for k, v in offs.items():
            if v is not None:
                _meta_set(k, v)
        c.commit()
    return n


# ------------------------------------------------------------------ readers
RANGES = {"24h": (86400, 300), "7d": (7 * 86400, 1800), "30d": (30 * 86400, 7200), "all": (None, None)}


def history(rng="24h"):
    span, bucket = RANGES.get(rng, RANGES["24h"])
    now = int(time.time())
    with _lock:
        c = conn()
        lo = now - span if span else 0
        if not span:
            r = c.execute("SELECT MIN(ts) FROM samples").fetchone()[0] or now
            bucket = max(300, int((now - r) / 200) or 300)
        rows = c.execute(
            "SELECT (ts/?)*? AS b, ROUND(AVG(commit_pct),1) c, ROUND(MAX(commit_pct),1) cm, ROUND(AVG(ram_pct),1) r, ROUND(MIN(avail_gb),2) a "
            "FROM samples WHERE ts>=? GROUP BY b ORDER BY b", (bucket, bucket, lo)).fetchall()
        series = [{"t": r["b"], "commit": r["c"], "commitMax": r["cm"], "ram": r["r"], "avail": r["a"]} for r in rows]
        k = c.execute("SELECT COUNT(*) n, ROUND(AVG(commit_pct),1) ca, ROUND(MAX(commit_pct),1) cm, ROUND(MIN(avail_gb),2) amin, MIN(ts) t0 FROM samples WHERE ts>=?", (lo,)).fetchone()
        tr = c.execute("SELECT COUNT(*) n, COALESCE(SUM(freed_mb),0) f FROM trims WHERE ts>=?", (lo,)).fetchone()
        cr = c.execute("SELECT COUNT(*) n, COALESCE(SUM(bytes_freed),0) b, COALESCE(SUM(files),0) f, COALESCE(SUM(rules_failed),0) fl FROM cleanup_runs WHERE dry=0 AND ts>=?", (lo,)).fetchone()
        ev = {r["severity"]: r["n"] for r in c.execute("SELECT severity, COUNT(*) n FROM events WHERE ts>=? GROUP BY severity", (lo,))}
        daily = [dict(r) for r in c.execute("SELECT * FROM v_daily ORDER BY day DESC LIMIT 60")]
        marks = [{"t": r["ts"], "kind": "trim"} for r in c.execute("SELECT ts FROM trims WHERE ts>=?", (lo,))]
        marks += [{"t": r["ts"], "kind": "cleanup"} for r in c.execute("SELECT ts FROM cleanup_runs WHERE dry=0 AND ts>=? AND bytes_freed>0", (lo,))]
    return {"range": rng, "from": lo, "to": now, "bucket": bucket, "series": series, "marks": marks, "daily": daily,
            "kpi": {"samples": k["n"], "commitAvg": k["ca"], "commitMax": k["cm"], "availMin": k["amin"], "since": k["t0"],
                    "trims": tr["n"], "trimFreedMB": round(tr["f"]), "cleanups": cr["n"], "cleanupBytes": cr["b"], "cleanupFiles": cr["f"], "cleanupFailed": cr["fl"],
                    "events": ev}}


def events(limit=10, offset=0, sev=None, q=None, rng=None):
    span = RANGES.get(rng or "all", (None, None))[0]
    w, a = ["1=1"], []
    if sev and sev != "all":
        w.append("severity=?"); a.append(sev)
    if q:
        w.append("(title LIKE ? OR detail LIKE ? OR source LIKE ?)"); a += ["%" + q + "%"] * 3
    if span:
        w.append("ts>=?"); a.append(int(time.time()) - span)
    sql = " AND ".join(w)
    with _lock:
        c = conn()
        total = c.execute("SELECT COUNT(*) FROM events WHERE " + sql, a).fetchone()[0]
        rows = [dict(r) for r in c.execute("SELECT ts,source,severity,title,detail FROM events WHERE " + sql + " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?", a + [limit, offset])]
    return {"total": total, "rows": rows}


def runs(limit=10, offset=0):
    with _lock:
        c = conn()
        total = c.execute("SELECT COUNT(*) FROM cleanup_runs").fetchone()[0]
        rows = [dict(r) for r in c.execute("SELECT * FROM cleanup_runs ORDER BY ts DESC LIMIT ? OFFSET ?", (limit, offset))]
    return {"total": total, "rows": rows}


def info():
    with _lock:
        c = conn()
        t = {x: c.execute("SELECT COUNT(*) FROM " + x).fetchone()[0] for x in ("samples", "events", "cleanup_runs", "trims")}
        for x in ("procs", "gpu", "score_daily"):
            try:
                t[x] = c.execute("SELECT COUNT(*) FROM " + x).fetchone()[0]
            except Exception:
                pass
        r = c.execute("SELECT MIN(ts) a, MAX(ts) b FROM samples").fetchone()
        ver = c.execute("select sqlite_version()").fetchone()[0]
        schema_version = c.execute("PRAGMA user_version").fetchone()[0]
    size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    return {"path": DB_PATH, "bytes": size, "tables": t, "first": r["a"], "last": r["b"], "sqlite": ver, "schema": schema_version}


def prune_if_due():
    with _lock:
        last = int(_meta_get("last_prune", 0))
        if time.time() - last > 86400:
            prune(); _meta_set("last_prune", int(time.time())); conn().commit()


# The single retention table: table -> (timestamp column, days to keep, extra WHERE or None).
# No other module deletes by age; names and clauses come from this constant, never from input.
RETENTION = {
    "samples": ("ts", 400, None), "events": ("ts", 400, None),
    "procs": ("ts", 14, None), "gpu": ("ts", 30, None), "gpu_procs": ("ts", 7, None), "igpu": ("ts", 30, None),
    "cpu_top": ("ts", 3, None), "power_samples": ("ts", 30, None), "dirsize": ("ts", 60, None),
    "job_runs": ("ts", 180, None), "space_runs": ("ts", 365, None), "trims": ("ts", 365, None),
    "cleanup_runs": ("ts", 365, None),
    "actions": ("ts", 365, None), "rel_events": ("ts", 365, None), "score_daily": ("ts", 730, None),
    "recs": ("ts", 365, "status<>'open'"), "quarantine": ("ts", 90, "status<>'held'"),
}
VACUUM_EVERY_S = 7 * 86400
VACUUM_MIN_FREE_PAGES = 0.15   # share of the file that must be free pages before a VACUUM is worth it


def _vacuum_if_due(c):
    """Full VACUUM (auto_vacuum is off): weekly at most, only when the freelist is large. Caller holds _lock."""
    if time.time() - int(_meta_get("last_vacuum", 0)) < VACUUM_EVERY_S:
        return False
    pages = c.execute("PRAGMA page_count").fetchone()[0]
    free = c.execute("PRAGMA freelist_count").fetchone()[0]
    if not pages or free / pages < VACUUM_MIN_FREE_PAGES:
        return False
    c.commit()
    try:
        c.execute("VACUUM")
    except sqlite3.Error:
        import errlog
        errlog.swallowed()
        return False
    _meta_set("last_vacuum", int(time.time())); c.commit()
    return True


def prune(days=400):
    """Apply RETENTION (days argument overrides the samples/events window), thin live samples, VACUUM when due."""
    with _lock:
        c = conn(); now = int(time.time())
        have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for tbl, (col, keep, extra) in RETENTION.items():
            if tbl not in have:
                continue
            keep = days if tbl in ("samples", "events") else keep
            sql = "DELETE FROM %s WHERE %s<?" % (tbl, col) + (" AND " + extra if extra else "")
            c.execute(sql, (now - keep * 86400,))
            c.commit()
        # Keep one measured worst-pressure representative per nonempty five-minute bucket.
        # Sampling is not clock-aligned; modulo filtering can erase an entire period.
        thin_live_samples(c, now - 14 * 86400)
        c.commit()
        _vacuum_if_due(c)


def thin_live_samples(c, cutoff):
    """Preserve one real measurement per old live bucket, preferring peak pressure.
    NULL metrics sort after measured metrics; no measurements are invented or averaged.
    Non-live and recent samples are untouched. Caller owns the transaction.
    """
    c.execute("""DELETE FROM samples WHERE source='live' AND ts IN (
        SELECT ts FROM (
          SELECT ts, ROW_NUMBER() OVER (
            PARTITION BY CAST(ts / 300 AS INTEGER)
            ORDER BY commit_pct IS NULL, commit_pct DESC,
                     ram_pct IS NULL, ram_pct DESC,
                     avail_gb IS NULL, avail_gb ASC, ts DESC) AS rank
          FROM samples WHERE source='live' AND ts<?
        ) WHERE rank>1)""", (cutoff,))
