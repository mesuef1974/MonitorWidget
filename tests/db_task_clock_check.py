"""حارس قفزة الساعة في أحداث المهام (db.note_tasks): مهمة شُغّلت والساعة مقدَّمة تبقى في مجدول ويندوز بوقت تشغيل مستقبلي
حتى تشغيلها التالي (حادثة 14/11/2026: 26 مهمة بتاريخ 31/12/2026). وقت التشغيل المستقبلي لا يُسجَّل حدثًا، ورؤيته تحذف
أحداث المهام المستقبلية التي كُتبت قبل الحارس، ولا يُمسّ الماضي ولا أحداث المصادر الأخرى. معزول: قاعدة مؤقتة.

Run: python -X utf8 -B tests/db_task_clock_check.py
"""
import atexit
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="mwdb_tc_")
atexit.register(shutil.rmtree, TMP, True)
import db

db.DB_PATH = os.path.join(TMP, "monitor.db")
db._conn = None

NOW = 1794613440            # 14/11/2026 02:44
JUMP = 1798750795           # 31/12/2026 23:59:55


def stamp(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(epoch))


def task(label, epoch, result=0, group=None):
    return {"name": label, "label": label, "last": stamp(epoch), "result": result, "group": group}


def rows(source="task"):
    return [(r[0], r[1], r[2]) for r in db.conn().execute("SELECT ts,title,severity FROM events WHERE source=? ORDER BY ts,title", (source,))]


class TaskClockGuard(unittest.TestCase):
    def setUp(self):
        db.conn().execute("DELETE FROM events")
        db.conn().commit()

    def test_future_last_run_is_not_recorded(self):
        self.assertEqual(db.note_tasks([task("تنظيف شهري", JUMP, 1), task("تنظيف يومي", NOW - 3600)], now=NOW), 0)
        self.assertEqual(rows(), [(NOW - 3600, "مهمة: تنظيف يومي", "ok")])

    def test_future_events_written_before_the_guard_are_dropped(self):
        db.add_event(JUMP, "task", "bad", "مهمة: تنظيف شهري")
        db.add_event(JUMP, "task", "ok", "مهمة: تنبيه الصحة")
        db.add_event(NOW - 60, "task", "ok", "مهمة: تنبيه الصحة")
        db.add_event(JUMP, "cleanup", "bad", "فشل في تنظيف monthly")       # مصدر آخر: لا يُمسّ
        self.assertEqual(db.note_tasks([task("تنظيف شهري", JUMP, 1)], now=NOW), 2)
        self.assertEqual(rows(), [(NOW - 60, "مهمة: تنبيه الصحة", "ok")])
        self.assertEqual(len(rows("cleanup")), 1)
        self.assertEqual(db.note_tasks([task("تنظيف شهري", JUMP, 1)], now=NOW), 0)   # لا شيء يُحذف مرتين

    def test_nothing_is_dropped_without_a_future_task(self):
        db.add_event(JUMP, "task", "ok", "مهمة: قديمة")
        self.assertEqual(db.note_tasks([task("تنظيف يومي", NOW - 10)], now=NOW), 0)
        self.assertEqual(len(rows()), 2)

    def test_small_clock_difference_is_within_slack(self):
        edge = NOW + db.TASK_FUTURE_SLACK
        self.assertEqual(db.note_tasks([task("على الحد", edge), task("بعد الحد", edge + 60)], now=NOW), 0)
        self.assertEqual(rows(), [(edge, "مهمة: على الحد", "ok")])

    def test_severity_and_default_clock_unchanged(self):
        past = int(time.time()) - 120
        db.note_tasks([task("تنظيف أسبوعي", past, 3, "clean"), task("فاشلة", past, 1), {"name": "x", "missing": True, "last": stamp(past)}])
        self.assertEqual(rows(), [(past, "مهمة: تنظيف أسبوعي", "info"), (past, "مهمة: فاشلة", "bad")])


if __name__ == "__main__":
    unittest.main(verbosity=1)
