# -*- coding: utf-8 -*-
"""Persistent job scheduler (state lives in the `jobs` table, so it survives restarts and can be nudged by other processes).
mode "fixed": next run = start + interval (missed runs are skipped, never stacked)
mode "after_finish": next run = end + interval (the countdown starts when the work is done)
A job never overlaps itself; an overdue job at startup runs immediately (catch-up).
timeout_s is a cooperative deadline, not permission to kill a Python thread.
IO callers use remaining_timeout(); loops use check_cancelled(). Uncooperative
work remains marked running/stuck and is never started again concurrently."""
import threading, time, traceback

import db, infra

log = infra.log
_context = threading.local()


def check_cancelled():
    deadline = getattr(_context, "deadline", None)
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("job execution deadline exceeded")


def remaining_timeout(default=None):
    """Bound blocking IO to the current job budget (seconds). Outside a job use default."""
    check_cancelled()
    deadline = getattr(_context, "deadline", None)
    if deadline is None:
        return default
    left = max(0.001, deadline - time.monotonic())
    return min(default, left) if default is not None else left



class Scheduler:
    def __init__(self):
        self.fns = {}
        self.running = set()
        self._l = threading.Lock()
        self._retry_after = {}

    def register(self, name, fn, interval_s, mode="fixed", timeout_s=600, delay=0):
        if interval_s <= 0 or timeout_s <= 0:
            raise ValueError("job interval and deadline must be positive")
        self.fns[name] = fn
        now = int(time.time())
        with db._lock:
            c = db.conn()
            r = c.execute("SELECT name FROM jobs WHERE name=?", (name,)).fetchone()
            if r is None:
                c.execute("INSERT INTO jobs(name,interval_s,mode,next_run,timeout_s) VALUES(?,?,?,?,?)", (name, interval_s, mode, now + delay, timeout_s))
            else:
                c.execute("UPDATE jobs SET interval_s=?, mode=?, timeout_s=? WHERE name=?", (interval_s, mode, timeout_s, name))
                if delay > 0:  # boot grace: an overdue job must not fire the moment the agent starts
                    c.execute("UPDATE jobs SET next_run=? WHERE name=? AND next_run<?", (now + delay, name, now + delay))
            c.commit()

    def _due(self):
        with db._lock:
            return [r[0] for r in db.conn().execute("SELECT name FROM jobs WHERE enabled=1 AND next_run<=? ORDER BY next_run", (int(time.time()),))]

    def _run(self, name):
        try:
            with db._lock:
                row = db.conn().execute("SELECT timeout_s FROM jobs WHERE name=?", (name,)).fetchone()
            _context.deadline = time.monotonic() + (row[0] if row else 600)
            self._execute(name)
        except Exception:
            # Even DB bookkeeping can fail. Never strand the running marker or
            # hot-loop a failing DB once a second.
            log.exception("job %s bookkeeping failed", name)
            self._retry_after[name] = time.monotonic() + 30
        finally:
            if hasattr(_context, "deadline"):
                del _context.deadline
            with self._l:
                self.running.discard(name)

    def _execute(self, name):
        fn = self.fns[name]
        t0 = time.time()
        with db._lock:
            c = db.conn(); c.execute("UPDATE jobs SET last_start=? WHERE name=?", (int(t0), name)); c.commit()
        status, err, detail = "ok", None, None
        try:
            check_cancelled()
            detail = fn()
            check_cancelled()
        except Exception:
            status, err = "error", traceback.format_exc(limit=3)[-400:]
            log.error("job %s failed: %s", name, err)
        t1 = time.time()
        with db._lock:
            c = db.conn()
            row = c.execute("SELECT interval_s, mode, timeout_s, next_run FROM jobs WHERE name=?", (name,)).fetchone()
            if row:
                iv, mode, to = row["interval_s"], row["mode"], row["timeout_s"]
                if status == "ok" and (t1 - t0) > to:
                    status = "slow"
                nxt = int(t1 + iv) if mode == "after_finish" else int(max(t0 + iv, t1 + 1))
                if row["next_run"] > time.time() + 5 and row["next_run"] > nxt:   # someone pushed it further out while it ran (e.g. a manual trim)
                    nxt = row["next_run"]
                c.execute("UPDATE jobs SET last_end=?, last_ms=?, last_status=?, last_error=?, runs=runs+1, fails=fails+?, next_run=? WHERE name=?",
                          (int(t1), int((t1 - t0) * 1000), status, err or (str(detail)[:200] if detail else None), 1 if status == "error" else 0, nxt, name))
                c.commit()
        if status == "error":
            infra.record_job(db.conn(), name, False, (t1 - t0) * 1000, err)

    def loop(self):
        while True:
            try:
                for n in self._due():
                    if n in self.fns:
                        with self._l:
                            if n in self.running or time.monotonic() < self._retry_after.get(n, 0):
                                continue
                            self.running.add(n)
                        try:
                            threading.Thread(target=self._run, args=(n,), daemon=True, name="job-" + n).start()
                        except Exception:
                            with self._l:
                                self.running.discard(n)
                            raise
            except Exception:
                log.exception("scheduler tick")
            time.sleep(1)


def set_next(name, ts):
    """Move a job's next run (used by the server after a manual trim, so the countdown restarts after ANY trim)."""
    with db._lock:
        c = db.conn(); c.execute("UPDATE jobs SET next_run=? WHERE name=?", (int(ts), name)); c.commit()


def next_run(name):
    with db._lock:
        r = db.conn().execute("SELECT next_run, interval_s, last_start, last_end, last_status FROM jobs WHERE name=?", (name,)).fetchone()
    return dict(r) if r else None


def listing():
    now = int(time.time())
    with db._lock:
        rows = [dict(r) for r in db.conn().execute("SELECT * FROM jobs ORDER BY name")]
    for r in rows:
        r["timeout_kind"] = "cooperative_deadline"
        r["in_s"] = r["next_run"] - now if r["next_run"] else None
        r["stuck"] = bool(r["last_start"] and (not r["last_end"] or r["last_end"] < r["last_start"]) and now - r["last_start"] > (r["timeout_s"] or 600))
    return rows
