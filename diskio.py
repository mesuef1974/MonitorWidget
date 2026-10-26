# -*- coding: utf-8 -*-
"""Disk I/O store for the monitor (stdlib only): schema, defaults, writers, rollups, retention.

Tables (all CREATE IF NOT EXISTS, safe next to the live monitor.db):
  io_samples   raw readings every ~10 s per device (read/write bytes+ops, latency, size, queue, busy, split I/O)
  io_min       per-minute rollup (volume in MB, averages, peaks)          kept 30 d
  io_hour      per-hour rollup (feeds the heat map and long ranges)       kept 400 d
  io_day       per-day rollup (written/read GB per day)                   kept forever
  io_procs     top processes per minute (MB and ops read/written)         kept 3 d
  io_procs_h   top processes per hour                                     kept 90 d
  io_health    drive health snapshots (temperature, wear, errors)         kept 90 d
  io_stalls    detected stalls (latency above the limit for N seconds) with the top processes at the time  kept 180 d
  io_cfg       thresholds and retention (editable without code changes)
Views for the dashboard API: v_io_now, v_io_heat, v_io_top_writers_24h, v_io_vs_space.
This module never imports db.py: it only receives a sqlite3 connection (db.conn() calls init() once).
Run:  python diskio.py --init      (create tables in monitor.db and list them)
      python diskio.py --selftest  (in-memory test of schema, rollups, views, retention)"""

from sqlite_runtime import ensure_sqlite
ensure_sqlite()
import json, os, sqlite3, sys, time

DEFAULT_CFG = {
    "sample_s": 10,          # seconds between raw readings
    "proc_top_n": 10,        # processes kept per minute
    "lat_good_ms": 10,       # latency bands for the chart: green below, amber up to warn, red above bad
    "lat_warn_ms": 20,
    "lat_bad_ms": 50,
    "busy_warn": 80,         # % disk time
    "busy_bad": 95,
    "stall_lat_ms": 100,     # a stall = latency above this ...
    "stall_min_s": 5,        # ... for at least this many seconds
    "ret_samples_d": 7,
    "ret_min_d": 30,
    "ret_procs_d": 3,
    "ret_procs_h_d": 90,
    "ret_hour_d": 400,
    "ret_stalls_d": 180,
    "ret_health_d": 90,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS io_samples(
  ts INTEGER NOT NULL,              -- unix seconds
  dev TEXT NOT NULL,                -- PhysicalDisk instance (e.g. '0 C: D:') or '_total'
  dt REAL NOT NULL DEFAULT 10,      -- seconds this reading covers (volume = bytes/s x dt)
  r_bps REAL, w_bps REAL,           -- Disk Read/Write Bytes/sec
  r_iops REAL, w_iops REAL,         -- Disk Reads/Writes per sec
  lat_r_ms REAL, lat_w_ms REAL,     -- Avg. Disk sec/Read and /Write, in ms
  r_size_kb REAL, w_size_kb REAL,   -- Avg. Disk Bytes/Read and /Write, in KB
  q_avg REAL, q_cur REAL,           -- Avg. and Current Disk Queue Length
  busy_pct REAL, idle_pct REAL,     -- % Disk Time, % Idle Time
  split_iops REAL,                  -- Split IO/Sec
  PRIMARY KEY(ts, dev)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS io_min(
  ts INTEGER NOT NULL, dev TEXT NOT NULL,          -- minute start
  r_mb REAL, w_mb REAL, r_ops REAL, w_ops REAL,    -- volume and operation counts in the minute
  r_bps_avg REAL, r_bps_max REAL, w_bps_avg REAL, w_bps_max REAL,
  lat_r_avg REAL, lat_r_max REAL, lat_w_avg REAL, lat_w_max REAL,
  q_max REAL, busy_avg REAL, busy_max REAL, n INTEGER,
  PRIMARY KEY(ts, dev)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS io_hour(
  ts INTEGER NOT NULL, dev TEXT NOT NULL,          -- hour start
  r_mb REAL, w_mb REAL, r_ops REAL, w_ops REAL,
  r_bps_max REAL, w_bps_max REAL,
  lat_r_avg REAL, lat_r_max REAL, lat_w_avg REAL, lat_w_max REAL,
  q_max REAL, busy_avg REAL, busy_max REAL, n INTEGER,
  PRIMARY KEY(ts, dev)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS io_day(
  day TEXT NOT NULL, dev TEXT NOT NULL,            -- local date YYYY-MM-DD
  r_gb REAL, w_gb REAL, busy_avg REAL, busy_max REAL, lat_w_max REAL, lat_r_max REAL, stalls INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(day, dev)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS io_procs(
  ts INTEGER NOT NULL, name TEXT NOT NULL,         -- minute start, process image name
  r_mb REAL, w_mb REAL, r_ops REAL, w_ops REAL, procs INTEGER,
  PRIMARY KEY(ts, name)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_io_procs_name ON io_procs(name, ts);

CREATE TABLE IF NOT EXISTS io_procs_h(
  ts INTEGER NOT NULL, name TEXT NOT NULL,         -- hour start
  r_mb REAL, w_mb REAL, r_ops REAL, w_ops REAL,
  PRIMARY KEY(ts, name)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS io_health(
  ts INTEGER NOT NULL, dev TEXT NOT NULL,
  model TEXT, media TEXT, size_gb REAL, health TEXT, op_status TEXT,
  temp_c REAL, wear_pct REAL, read_err INTEGER, write_err INTEGER, power_hours INTEGER,
  PRIMARY KEY(ts, dev)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS io_stalls(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_start INTEGER NOT NULL, ts_end INTEGER, dev TEXT,
  peak_lat_ms REAL, peak_q REAL, busy_avg REAL, r_mb REAL, w_mb REAL,
  top_json TEXT,                    -- [{"name":..,"r_mb":..,"w_mb":..}, ...] at the time
  free_gb REAL, ram_pct REAL, note TEXT);
CREATE INDEX IF NOT EXISTS ix_io_stalls_ts ON io_stalls(ts_start DESC);

CREATE TABLE IF NOT EXISTS io_cfg(key TEXT PRIMARY KEY, value TEXT);

CREATE VIEW IF NOT EXISTS v_io_now AS
  SELECT s.* FROM io_samples s JOIN (SELECT dev, MAX(ts) t FROM io_samples GROUP BY dev) m ON s.dev = m.dev AND s.ts = m.t;

CREATE VIEW IF NOT EXISTS v_io_heat AS
  SELECT date(ts,'unixepoch','localtime') AS day,
         CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) AS hour,
         dev, ROUND(busy_avg,1) AS busy_avg, ROUND(busy_max,1) AS busy_max,
         ROUND(w_mb/1024.0,3) AS w_gb, ROUND(r_mb/1024.0,3) AS r_gb, ROUND(lat_w_max,1) AS lat_w_max
  FROM io_hour;

CREATE VIEW IF NOT EXISTS v_io_top_writers_24h AS
  SELECT name, ROUND(SUM(w_mb)/1024.0,2) AS w_gb, ROUND(SUM(r_mb)/1024.0,2) AS r_gb, ROUND(SUM(w_ops)) AS w_ops
  FROM io_procs WHERE ts > CAST(strftime('%s','now') AS INTEGER) - 86400
  GROUP BY name ORDER BY w_gb DESC;

CREATE VIEW IF NOT EXISTS v_io_vs_space AS
  SELECT m.ts AS ts, m.dev AS dev, m.w_mb AS w_mb, m.r_mb AS r_mb,
         (SELECT s.disk_free_gb FROM samples s WHERE s.source='live' AND s.ts BETWEEN m.ts AND m.ts + 59 ORDER BY s.ts LIMIT 1) AS disk_free_gb
  FROM io_min m;
"""

_COLS = ("ts", "dev", "dt", "r_bps", "w_bps", "r_iops", "w_iops", "lat_r_ms", "lat_w_ms", "r_size_kb", "w_size_kb",
         "q_avg", "q_cur", "busy_pct", "idle_pct", "split_iops")


def init(conn):
    """Create tables/views and seed missing config defaults. Idempotent; safe while other processes use the DB."""
    conn.executescript(SCHEMA)
    for k, v in DEFAULT_CFG.items():
        conn.execute("INSERT OR IGNORE INTO io_cfg(key,value) VALUES(?,?)", (k, str(v)))
    conn.commit()


def cfg(conn, key):
    r = conn.execute("SELECT value FROM io_cfg WHERE key=?", (key,)).fetchone()
    v = r[0] if r else DEFAULT_CFG.get(key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def add_samples(conn, rows):
    """rows: dicts or tuples in _COLS order; missing keys become NULL."""
    data = [tuple(r.get(c) for c in _COLS) if isinstance(r, dict) else tuple(r) for r in rows]
    conn.executemany("INSERT OR REPLACE INTO io_samples(%s) VALUES(%s)" % (",".join(_COLS), ",".join("?" * len(_COLS))), data)
    conn.commit()


def add_procs(conn, ts, rows):
    """rows: (name, r_mb, w_mb, r_ops, w_ops, procs). Keeps the top proc_top_n by total volume."""
    top = sorted(rows, key=lambda r: (r[1] or 0) + (r[2] or 0), reverse=True)[: int(cfg(conn, "proc_top_n"))]
    m = int(ts) // 60 * 60
    conn.executemany("INSERT OR REPLACE INTO io_procs(ts,name,r_mb,w_mb,r_ops,w_ops,procs) VALUES(?,?,?,?,?,?,?)", [(m,) + tuple(r) for r in top])
    conn.commit()


def add_health(conn, ts, dev, **kw):
    cols = ("model", "media", "size_gb", "health", "op_status", "temp_c", "wear_pct", "read_err", "write_err", "power_hours")
    conn.execute("INSERT OR REPLACE INTO io_health(ts,dev,%s) VALUES(?,?,%s)" % (",".join(cols), ",".join("?" * len(cols))),
                 (int(ts), dev) + tuple(kw.get(c) for c in cols))
    conn.commit()


def add_stall(conn, ts_start, ts_end, dev, peak_lat_ms, peak_q, busy_avg, r_mb, w_mb, top, free_gb=None, ram_pct=None, note=None):
    conn.execute("INSERT INTO io_stalls(ts_start,ts_end,dev,peak_lat_ms,peak_q,busy_avg,r_mb,w_mb,top_json,free_gb,ram_pct,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (int(ts_start), int(ts_end), dev, peak_lat_ms, peak_q, busy_avg, r_mb, w_mb, json.dumps(top, ensure_ascii=False), free_gb, ram_pct, note))
    conn.commit()


def rollup_min(conn, since_ts):
    """Recompute minute buckets from raw samples at/after since_ts (idempotent)."""
    since = int(since_ts) // 60 * 60
    conn.execute("""INSERT OR REPLACE INTO io_min
        SELECT ts/60*60, dev,
               SUM(r_bps*dt)/1048576.0, SUM(w_bps*dt)/1048576.0, SUM(r_iops*dt), SUM(w_iops*dt),
               AVG(r_bps), MAX(r_bps), AVG(w_bps), MAX(w_bps),
               AVG(lat_r_ms), MAX(lat_r_ms), AVG(lat_w_ms), MAX(lat_w_ms),
               MAX(q_avg), AVG(busy_pct), MAX(busy_pct), COUNT(*)
        FROM io_samples WHERE ts >= ? GROUP BY ts/60*60, dev""", (since,))
    conn.commit()


def rollup_hour(conn, since_ts):
    since = int(since_ts) // 3600 * 3600
    conn.execute("""INSERT OR REPLACE INTO io_hour
        SELECT ts/3600*3600, dev, SUM(r_mb), SUM(w_mb), SUM(r_ops), SUM(w_ops), MAX(r_bps_max), MAX(w_bps_max),
               AVG(lat_r_avg), MAX(lat_r_max), AVG(lat_w_avg), MAX(lat_w_max), MAX(q_max), AVG(busy_avg), MAX(busy_max), SUM(n)
        FROM io_min WHERE ts >= ? GROUP BY ts/3600*3600, dev""", (since,))
    conn.execute("""INSERT OR REPLACE INTO io_procs_h(ts,name,r_mb,w_mb,r_ops,w_ops)
        SELECT h, name, r, w, ro, wo FROM (
          SELECT ts/3600*3600 AS h, name, SUM(r_mb) AS r, SUM(w_mb) AS w, SUM(r_ops) AS ro, SUM(w_ops) AS wo,
                 ROW_NUMBER() OVER (PARTITION BY ts/3600*3600 ORDER BY SUM(w_mb)+SUM(r_mb) DESC) AS rn
          FROM io_procs WHERE ts >= ? GROUP BY ts/3600*3600, name) WHERE rn <= 15""", (since,))
    conn.commit()


def rollup_day(conn, since_ts):
    since = int(since_ts) // 3600 * 3600 - 86400
    conn.execute("""INSERT OR REPLACE INTO io_day(day,dev,r_gb,w_gb,busy_avg,busy_max,lat_w_max,lat_r_max,stalls)
        SELECT d, dev, r/1024.0, w/1024.0, bavg, bmax, lw, lr,
               (SELECT COUNT(*) FROM io_stalls x WHERE date(x.ts_start,'unixepoch','localtime') = d AND (x.dev = t.dev OR x.dev IS NULL))
        FROM (SELECT date(ts,'unixepoch','localtime') AS d, dev, SUM(r_mb) AS r, SUM(w_mb) AS w, AVG(busy_avg) AS bavg,
                     MAX(busy_max) AS bmax, MAX(lat_w_max) AS lw, MAX(lat_r_max) AS lr
              FROM io_hour WHERE ts >= ? GROUP BY d, dev) t""", (since,))
    conn.commit()


def prune(conn, now=None):
    now = int(now or time.time())
    for table, key in (("io_samples", "ret_samples_d"), ("io_min", "ret_min_d"), ("io_procs", "ret_procs_d"), ("io_procs_h", "ret_procs_h_d"),
                       ("io_hour", "ret_hour_d"), ("io_health", "ret_health_d")):
        conn.execute("DELETE FROM %s WHERE ts < ?" % table, (now - int(cfg(conn, key) * 86400),))
    conn.execute("DELETE FROM io_stalls WHERE ts_start < ?", (now - int(cfg(conn, "ret_stalls_d") * 86400),))
    conn.commit()


def maintenance(conn, now=None):
    """Hourly job body: refresh the last 3 hours of rollups, then apply retention."""
    now = int(now or time.time())
    rollup_min(conn, now - 3 * 3600); rollup_hour(conn, now - 3 * 3600); rollup_day(conn, now - 3 * 3600)
    prune(conn, now)


def tables(conn):
    return [(r[0], conn.execute("SELECT COUNT(*) FROM %s" % r[0]).fetchone()[0]) for r in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'io\\_%' ESCAPE '\\' ORDER BY name")]


def _selftest():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE samples(ts INTEGER, source TEXT, commit_pct REAL, ram_pct REAL, avail_gb REAL, disk_free_gb REAL, claude INTEGER, shells INTEGER)")
    init(c); init(c)  # idempotent
    base = 1_800_000_000 // 3600 * 3600
    rows = []
    for i in range(18):   # 3 minutes of 10 s samples on two devices
        for dev, w in (("0 C: D:", 50e6 + i * 1e6), ("_total", 55e6)):
            rows.append(dict(ts=base + i * 10, dev=dev, dt=10, r_bps=10e6, w_bps=w, r_iops=100, w_iops=200, lat_r_ms=2.0, lat_w_ms=3.0 + i,
                             r_size_kb=64, w_size_kb=128, q_avg=0.5, q_cur=1, busy_pct=40 + i, idle_pct=60 - i, split_iops=1))
    add_samples(c, rows)
    add_procs(c, base + 5, [("a.exe", 100, 900, 10, 90, 2), ("b.exe", 1, 1, 1, 1, 1)] + [("p%d.exe" % k, 0, k, 0, 0, 1) for k in range(20)])
    add_health(c, base, "0 C: D:", model="SSD", media="SSD", size_gb=477, health="Healthy", op_status="OK", temp_c=39, wear_pct=0, read_err=0, write_err=0)
    add_stall(c, base + 20, base + 30, "0 C: D:", 250, 8, 99, 5, 80, [{"name": "a.exe", "w_mb": 80}], 100.5, 70, "test")
    c.execute("INSERT INTO samples VALUES(?, 'live', 10, 30, 20, 100.5, 5, 1)", (base + 3,))
    rollup_min(c, base); rollup_hour(c, base); rollup_day(c, base)
    m = c.execute("SELECT ts,w_mb,w_ops,lat_w_max,n FROM io_min WHERE dev='0 C: D:' ORDER BY ts").fetchall()
    assert len(m) == 3 and all(r[4] == 6 for r in m), m
    exp = sum(50e6 + i * 1e6 for i in range(6)) * 10 / 1048576.0
    assert abs(m[0][1] - exp) < 1e-6, (m[0][1], exp)
    assert c.execute("SELECT COUNT(*) FROM io_procs").fetchone()[0] == 10
    assert c.execute("SELECT COUNT(*) FROM io_hour").fetchone()[0] == 2
    assert c.execute("SELECT stalls FROM io_day WHERE dev='0 C: D:'").fetchone()[0] == 1
    assert c.execute("SELECT name FROM io_procs_h ORDER BY w_mb DESC LIMIT 1").fetchone()[0] == "a.exe"
    assert c.execute("SELECT COUNT(*) FROM v_io_now").fetchone()[0] == 2
    assert c.execute("SELECT disk_free_gb FROM v_io_vs_space WHERE dev='0 C: D:' AND ts=?", (base,)).fetchone()[0] == 100.5
    assert c.execute("SELECT COUNT(*) FROM v_io_heat").fetchone()[0] == 2
    c.execute("SELECT * FROM v_io_top_writers_24h").fetchall()
    prune(c, base + 100 * 86400)
    assert c.execute("SELECT COUNT(*) FROM io_samples").fetchone()[0] == 0 and c.execute("SELECT COUNT(*) FROM io_day").fetchone()[0] == 2
    maintenance(c, base + 60)
    print("selftest OK:", len(tables(c)), "tables")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--init" in sys.argv:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.db")
        cn = sqlite3.connect(p, timeout=15)
        cn.execute("PRAGMA journal_mode=WAL")
        init(cn)
        for n, k in tables(cn):
            print("%-12s %d rows" % (n, k))
        print("OK:", p)
    else:
        print(__doc__)
