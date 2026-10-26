# -*- coding: utf-8 -*-
"""Read-only window onto monitor.db for the Database page.
Uses its own SQLite connection opened with mode=ro, so browsing cannot write, cannot change data and cannot
block the agent. Table names are taken from sqlite_master and validated before they are quoted into SQL."""
import os, re, sqlite3, time

import db

LIMIT_MAX = 200
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
# never browsable: byte offsets / agent pid / task snapshot (meta), file paths (quarantine), stored settings
HIDDEN = {"meta", "quarantine", "settings"}
OFFSET_MAX = 100000
TS_COL = re.compile(r"(^ts$|_ts$|^ts_|expires|next_run|last_start|last_end|done_ts|^t$)")


def _ro():
    return sqlite3.connect("file:%s?mode=ro" % db.DB_PATH.replace("\\", "/"), uri=True, timeout=10, check_same_thread=False)


def _names(c):
    out = []
    for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        if NAME_RE.match(n) and n not in HIDDEN:
            out.append(n)
    return out


def _cols(c, n):
    return [{"name": r[1], "type": r[2] or "", "pk": bool(r[5])} for r in c.execute('PRAGMA table_info("%s")' % n)]


def overview():
    c = _ro()
    try:
        tables = []
        for n in _names(c):
            cols = _cols(c, n)
            cnt = c.execute('SELECT COUNT(*) FROM "%s"' % n).fetchone()[0]
            tables.append({"name": n, "rows": cnt, "cols": cols})
        page = c.execute("PRAGMA page_size").fetchone()[0]
        pages = c.execute("PRAGMA page_count").fetchone()[0]
        free = c.execute("PRAGMA freelist_count").fetchone()[0]
        ver = c.execute("PRAGMA user_version").fetchone()[0]
        jm = c.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        c.close()
    walp = db.DB_PATH + "-wal"
    return {"tables": tables, "bytes": page * pages, "freeBytes": page * free, "schema": ver, "journal": jm,
            "walBytes": os.path.getsize(walp) if os.path.exists(walp) else 0,
            "file": os.path.basename(db.DB_PATH), "ts": int(time.time())}


def _cell(v, col):
    if v is None:
        return None
    if isinstance(v, bytes):
        return "<%d bytes>" % len(v)
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, int) and TS_COL.search(col) and 946684800 <= v <= 4102444800:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(v))
    if isinstance(v, str) and len(v) > 160:
        return v[:160] + "…"
    return v


def rows(name, limit=25, offset=0):
    limit = max(1, min(LIMIT_MAX, int(limit))); offset = max(0, min(OFFSET_MAX, int(offset)))
    c = _ro()
    try:
        if name not in _names(c):
            return {"error": "جدول غير معروف"}
        cols = _cols(c, name); cn = [x["name"] for x in cols]
        total = c.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
        try:
            cur = c.execute('SELECT * FROM "%s" ORDER BY rowid DESC LIMIT ? OFFSET ?' % name, (limit, offset))
        except sqlite3.OperationalError:  # WITHOUT ROWID tables have no rowid
            cur = c.execute('SELECT * FROM "%s" LIMIT ? OFFSET ?' % name, (limit, offset))
        data = [[_cell(v, cn[i] if i < len(cn) else "") for i, v in enumerate(r)] for r in cur.fetchall()]
    finally:
        c.close()
    return {"table": name, "cols": cols, "rows": data, "total": total, "limit": limit, "offset": offset}
