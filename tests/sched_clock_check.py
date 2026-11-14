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
sys.modules["db"] = types.SimpleNamespace(conn=lambda: CON, _lock=threading.RLock())
sys.modules["infra"] = types.SimpleNamespace(log=types.SimpleNamespace(warning=lambda *a: WARN.append(a), error=lambda *a: None, exception=lambda *a: None),
                                             record_job=lambda *a: None)
import sched

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

    def test_slack_formula(self):
        self.assertEqual(sched.future_slack(15), 630)
        self.assertEqual(sched.future_slack(3600), 7800)


if __name__ == "__main__":
    unittest.main(verbosity=1)
