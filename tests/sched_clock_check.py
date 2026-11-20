"""حارس قفزة الساعة في المجدول (sched.heal_clock): مهمة موعدها في 2027 بعد أن قُدّمت الساعة ثم أُعيدت تُسحب إلى الآن،
والمهمة المؤجَّلة شرعيًا (فترة واحدة، أو دفعة trim اليدوية 600 ثانية) لا تُمسّ، وأوقات البدء والانتهاء المستقبلية تُصحَّح.
معزول: قاعدة SQLite في الذاكرة بدل monitor.db.

Run: python -X utf8 -B tests/sched_clock_check.py
"""
import os
import sqlite3
import sys
import threading
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
CON = sqlite3.connect(":memory:", check_same_thread=False)
CON.row_factory = sqlite3.Row
WARN = []
RUNS = []
sys.modules["db"] = types.SimpleNamespace(conn=lambda: CON, _lock=threading.RLock())
sys.modules["infra"] = types.SimpleNamespace(log=types.SimpleNamespace(warning=lambda *a: WARN.append(a), error=lambda *a: None, exception=lambda *a: None),
                                             record_job=lambda *a: RUNS.append(a[1:]))
import mwsched as sched

NOW = 1794613440            # 14/11/2026 02:44
JUMP = 1798750795           # 31/12/2026 23:59:55


def job(name, interval, next_run, last_start=None, last_end=None, enabled=1):
    CON.execute("INSERT INTO jobs(name,interval_s,mode,next_run,last_start,last_end,enabled,timeout_s) VALUES(?,?,?,?,?,?,?,600)",
                (name, interval, "fixed", next_run, last_start, last_end, enabled))


def nxt(name):
    return CON.execute("SELECT next_run FROM jobs WHERE name=?", (name,)).fetchone()[0]


class ClockGuard(unittest.TestCase):
    def setUp(self):
        CON.execute("DROP TABLE IF EXISTS jobs")
        CON.execute("CREATE TABLE jobs(name TEXT PRIMARY KEY, interval_s INTEGER NOT NULL, mode TEXT, next_run INTEGER, last_start INTEGER, last_end INTEGER,"
                    " last_ms INTEGER, last_status TEXT, last_error TEXT, runs INTEGER DEFAULT 0, fails INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1, timeout_s INTEGER)")
        CON.execute("DROP TABLE IF EXISTS meta")
        CON.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        WARN.clear()

    def test_job_scheduled_after_clock_jump_is_pulled_back(self):
        job("heartbeat", 15, JUMP + 15, last_start=JUMP, last_end=JUMP)
        job("quarantine_purge", 3600, JUMP + 3600, last_start=JUMP, last_end=NOW - 60)
        self.assertEqual(sorted(sched.heal_clock(NOW)), ["heartbeat", "quarantine_purge"])
        self.assertEqual(nxt("heartbeat"), NOW)
        self.assertEqual(nxt("quarantine_purge"), NOW)
        r = CON.execute("SELECT last_start, last_end FROM jobs WHERE name='heartbeat'").fetchone()
        self.assertEqual((r[0], r[1]), (NOW, NOW))
        self.assertEqual(CON.execute("SELECT last_end FROM jobs WHERE name='quarantine_purge'").fetchone()[0], NOW - 60)   # الماضي لا يُمسّ
        self.assertEqual(len(WARN), 1)

    def test_legitimate_schedules_are_left_alone(self):
        job("sample", 60, NOW + 60, last_start=NOW, last_end=NOW)
        job("scan_big", 86400, NOW + 86400)
        job("trim", 3600, NOW + 3600 + 600)        # دفعة يدوية بعد تنظيف ذاكرة
        job("heartbeat", 15, NOW + 600)             # ضمن الهامش: 2×15 + 600
        self.assertEqual(sched.heal_clock(NOW), [])
        self.assertEqual([nxt(n) for n in ("sample", "scan_big", "trim", "heartbeat")], [NOW + 60, NOW + 86400, NOW + 4200, NOW + 600])
        self.assertEqual(WARN, [])

    def test_healed_job_becomes_due(self):
        job("cpu", 30, JUMP + 30)
        s = sched.Scheduler()
        self.assertEqual(s._due(), [])              # قبل الحارس: غير مستحقة حتى 2027
        sched.heal_clock()
        self.assertEqual(s._due(), ["cpu"])

    def test_disabled_job_is_healed_too(self):
        job("shots", 5, JUMP, enabled=0)
        self.assertEqual(sched.heal_clock(NOW), ["shots"])
        self.assertEqual(nxt("shots"), NOW)

    def test_future_meta_stamps_are_pulled_back(self):
        """مراجعة 19/11/2026: ختم last_prune أو last_backup كُتب أثناء القفزة كان يوقف عمله اليومي حتى 2027."""
        for k, v in (("last_prune", JUMP), ("last_vacuum", JUMP), ("last_backup", JUMP), ("last_integrity", NOW - 86400),
                     ("backup_extra_last_attempt", NOW + 600), ("agent_hb", JUMP), ("pageuse.since", "2026-11-14")):
            CON.execute("INSERT INTO meta VALUES(?,?)", (k, str(v)))
        self.assertEqual(sched.heal_clock(NOW), [])
        meta = dict(CON.execute("SELECT key, value FROM meta").fetchall())
        self.assertEqual((meta["last_prune"], meta["last_vacuum"], meta["last_backup"]), (str(NOW),) * 3)
        self.assertEqual(meta["last_integrity"], str(NOW - 86400))            # الماضي لا يُمسّ
        self.assertEqual(meta["backup_extra_last_attempt"], str(NOW + 600))   # ضمن الهامش
        self.assertEqual((meta["agent_hb"], meta["pageuse.since"]), (str(JUMP), "2026-11-14"))   # مفاتيح خارج القائمة لا تُمسّ
        self.assertEqual(len(WARN), 1)
        self.assertEqual(sched.heal_clock(NOW), [])
        self.assertEqual(len(WARN), 1)                                         # لا شيء يُصحَّح مرتين

    def test_slack_formula(self):
        self.assertEqual(sched.future_slack(15), 630)
        self.assertEqual(sched.future_slack(3600), 7800)



class SlowSuccess(unittest.TestCase):
    """مراجعة 19/11/2026: عمل اكتمل بعد مهلته كان يُسجَّل error لأن check_cancelled() بعد fn() يرفع TimeoutError."""

    def setUp(self):
        ClockGuard.setUp(self)

    def test_job_finishing_past_its_deadline_is_slow_not_error(self):
        import time as _t
        job("slowpoke", 3600, NOW)
        CON.execute("UPDATE jobs SET timeout_s=0 WHERE name='slowpoke'")   # أي مدة تتجاوز المهلة
        s = sched.Scheduler()
        s.fns["slowpoke"] = lambda: _t.sleep(0.05) or "done"
        sched._context.deadline = _t.monotonic() + 0.01                   # المهلة تنقضي أثناء fn()
        try:
            s._execute("slowpoke")
        finally:
            del sched._context.deadline
        r = CON.execute("SELECT last_status, fails, runs, last_error FROM jobs WHERE name='slowpoke'").fetchone()
        self.assertEqual((r[0], r[1], r[2], r[3]), ("slow", 0, 1, "done"))

    def test_deadline_already_passed_before_start_is_still_an_error(self):
        import time as _t
        job("late", 3600, NOW)
        ran = []
        s = sched.Scheduler()
        s.fns["late"] = lambda: ran.append(1)
        sched._context.deadline = _t.monotonic() - 1
        try:
            s._execute("late")
        finally:
            del sched._context.deadline
        self.assertEqual(ran, [])
        self.assertEqual(CON.execute("SELECT last_status, fails FROM jobs WHERE name='late'").fetchone()[:], ("error", 1))


    def test_heavy_runs_are_recorded_for_stall_matching_and_short_ones_are_not(self):
        """البند 36 من المجلس 19/11: تشغيل يطول HEAVY_MS فأكثر يُسجَّل ناجحًا بمدته وتفاصيله؛ القصير لا."""
        RUNS.clear()
        for name in ("heavy", "light"):
            job(name, 3600, NOW)
        s = sched.Scheduler()
        s.fns["light"] = lambda: "quick"
        s.fns["heavy"] = lambda: "big scan"
        clock = iter([100.0, 100.0 + sched.HEAVY_MS / 1000.0 + 1])
        real = sched.time.time
        sched.time.time = lambda: next(clock, real())
        try:
            s._execute("heavy")
        finally:
            sched.time.time = real
        s._execute("light")
        self.assertEqual(len(RUNS), 1)
        name, ok, ms, detail = RUNS[0]
        self.assertEqual((name, ok, detail), ("heavy", True, "big scan")); self.assertGreaterEqual(ms, sched.HEAVY_MS)

class RunningNow(unittest.TestCase):
    """مراجعة 19/11/2026: «شغّل الآن» لمهمة جارية كان يضيع بصمت؛ running_now يميّزها لمسار /api/jobs/run."""

    def test_running_within_timeout(self):
        self.assertTrue(sched.running_now({"last_start": NOW - 10, "last_end": NOW - 100, "timeout_s": 600}, NOW))
        self.assertTrue(sched.running_now({"last_start": NOW - 10, "last_end": None, "timeout_s": 600}, NOW))

    def test_finished_never_started_or_stuck_is_not_running(self):
        self.assertFalse(sched.running_now({"last_start": NOW - 10, "last_end": NOW - 5, "timeout_s": 600}, NOW))
        self.assertFalse(sched.running_now({"last_start": NOW - 10, "last_end": NOW - 10, "timeout_s": 600}, NOW))   # الثانية نفسها: انتهت
        self.assertFalse(sched.running_now({"last_start": None, "last_end": None, "timeout_s": 600}, NOW))
        self.assertFalse(sched.running_now({"last_start": NOW - 700, "last_end": NOW - 9000, "timeout_s": 600}, NOW))   # عالقة أو علامة وكيل انهار


if __name__ == "__main__":
    unittest.main(verbosity=1)
