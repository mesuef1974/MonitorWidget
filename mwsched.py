# -*- coding: utf-8 -*-
"""Persistent job scheduler (state lives in the `jobs` table, so it survives restarts and can be nudged by other processes).
mode "fixed": next run = start + interval (missed runs are skipped, never stacked)
mode "after_finish": next run = end + interval (the countdown starts when the work is done)
A job never overlaps itself; an overdue job at startup runs immediately (catch-up).
timeout_s is a cooperative deadline, not permission to kill a Python thread.
IO callers use remaining_timeout(); loops use check_cancelled(). Uncooperative
work remains marked running/stuck and is never started again concurrently.
Wall-clock guard: next_run is wall time, so a clock set forward and back (31/12/2026 for 18 s on 14/11/2026)
left 17 jobs scheduled for 2027. heal_clock() pulls any job scheduled past FUTURE_SLACK back to now."""
import threading, time, traceback

import db, infra

log = infra.log
HEAL_EVERY = 60     # seconds between wall-clock guard passes in the loop
# كل تشغيل يطول HEAVY_MS فأكثر يُسجَّل في job_runs (لا الإخفاق وحده) بوقت نهايته ومدته: مراقب الويدجت يطابق به
# توقفات الواجهة مع ما كان يعمل في نافذتها (البند 36 من المجلس 19/11). القصيرة لا تُسجَّل فلا يتضخّم الجدول.
HEAVY_MS = 5000
# أختام «آخر تشغيل» بالثواني في جدول meta تُقارن بـ time.time(): ختم كُتب أثناء قفزة الساعة للأمام يوقف عمله حتى يلحقه الزمن
# (prune يوميًا وVACUUM أسبوعيًا في db.py، والنسخ والسلامة والنسخ الخارجي في infra.py). heal_clock يعيدها إلى الآن (مراجعة 19/11/2026).
META_STAMPS = ("last_prune", "last_vacuum", "last_backup", "last_integrity", "backup_extra_last_attempt", "backup_extra_last_success")


def future_slack(interval_s):
    """How far past now a legitimate next_run can be: one interval (or a manual push such as trim's 600 s), with margin."""
    return 2 * int(interval_s) + 600


def heal_clock(now=None):
    """Pull jobs scheduled impossibly far ahead (the clock went forward, then back) to now, and unstick their
    future last_start/last_end. Returns the healed job names."""
    now = int(time.time() if now is None else now)
    with db._lock:
        c = db.conn()
        names = [r[0] for r in c.execute("SELECT name FROM jobs WHERE next_run > ? + 2*interval_s + 600", (now,))]
        if names:
            c.execute("UPDATE jobs SET next_run=? WHERE next_run > ? + 2*interval_s + 600", (now, now))
        stamped = c.execute("UPDATE jobs SET last_start=? WHERE last_start > ? + 600", (now, now)).rowcount
        stamped += c.execute("UPDATE jobs SET last_end=? WHERE last_end > ? + 600", (now, now)).rowcount
        marks = c.execute("UPDATE meta SET value=? WHERE key IN (%s) AND CAST(value AS INTEGER) > ? + 600" % ",".join("?" * len(META_STAMPS)),
                          (str(now),) + META_STAMPS + (now,)).rowcount
        if names or stamped or marks:
            c.commit()
    if names:
        log.warning("clock guard: %d job(s) were scheduled in the far future, reset to now: %s", len(names), ", ".join(names))
    if marks:
        log.warning("clock guard: %d meta stamp(s) were in the far future, reset to now", marks)
    return names
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
            # لا check_cancelled() هنا: عمل اكتمل بعد مهلته نجاحٌ بطيء (slow أدناه) لا خطأ (مراجعة 19/11/2026)
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
        elif (t1 - t0) * 1000 >= HEAVY_MS:
            infra.record_job(db.conn(), name, True, (t1 - t0) * 1000, str(detail) if detail else status)

    def loop(self):
        healed = None
        while True:
            try:
                if healed is None or time.monotonic() - healed >= HEAL_EVERY:
                    healed = time.monotonic()
                    heal_clock()
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
        r = db.conn().execute("SELECT next_run, interval_s, last_start, last_end, last_status, timeout_s FROM jobs WHERE name=?", (name,)).fetchone()
    return dict(r) if r else None


def running_now(r, now=None):
    """صفّ next_run() لمهمة بدأت ولم تنتهِ وما زالت ضمن مهلتها. «شغّل الآن» أثناءها يضيع: _execute يكتب next_run
    عند الانتهاء فوق الموعد المطلوب. ما تجاوز مهلته (عالقة، أو علامة باقية من وكيل انهار) لا يُعدّ جاريًا هنا."""
    now = int(time.time() if now is None else now)
    start, end = r.get("last_start"), r.get("last_end")
    return bool(start and (not end or end < start) and now - start <= (r.get("timeout_s") or 600))


def listing():
    now = int(time.time())
    with db._lock:
        rows = [dict(r) for r in db.conn().execute("SELECT * FROM jobs ORDER BY name")]
    for r in rows:
        r["timeout_kind"] = "cooperative_deadline"
        r["in_s"] = r["next_run"] - now if r["next_run"] else None
        r["stuck"] = bool(r["last_start"] and (not r["last_end"] or r["last_end"] < r["last_start"]) and now - r["last_start"] > (r["timeout_s"] or 600))
    return rows
