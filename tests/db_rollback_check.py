"""الاتصال المشترك يلغي المعاملة حين تفشل كتابة (حادثة 14/11/2026: «database is locked» دائم حتى إعادة التشغيل).
كتابة تفشل لأن اتصالًا آخر يمسك قفل الكتابة يجب ألا تترك معاملة مفتوحة؛ وبعد زوال القفل وكتابة الآخرين
تنجح الكتابة التالية فورًا والقراءة ترى الجديد. معزول: قاعدة مؤقتة.

Run: python -X utf8 -B tests/db_rollback_check.py
"""
import atexit
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="mwdb_rb_")
atexit.register(shutil.rmtree, TMP, True)
import db

db.DB_PATH = os.path.join(TMP, "monitor.db")
db._conn = None


def other():
    c = sqlite3.connect(db.DB_PATH, timeout=0.05)
    c.isolation_level = None
    return c


class RollbackOnFailedWrite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = db.conn()
        cls.c.execute("PRAGMA busy_timeout=50")

    def test_failed_write_leaves_no_open_transaction_and_recovers(self):
        o = other()
        o.execute("BEGIN IMMEDIATE")
        o.execute("INSERT INTO meta(key,value) VALUES('held','1')")
        with self.assertRaises(sqlite3.OperationalError):
            db.add_event(1000, "t", "info", "blocked")
        self.assertFalse(self.c.in_transaction)            # كان يبقى True إلى الأبد
        o.execute("COMMIT")                                # كتابة من اتصال آخر بعد الفشل: شرط اللقطة القديمة
        self.c.execute("SELECT COUNT(*) FROM events").fetchone()
        t0 = time.monotonic()
        db.add_event(1001, "t", "info", "after")           # كانت تفشل فورًا بـ SQLITE_BUSY_SNAPSHOT
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual(self.c.execute("SELECT title FROM events WHERE ts=1001").fetchone()[0], "after")
        self.assertEqual(self.c.execute("SELECT value FROM meta WHERE key='held'").fetchone()[0], "1")   # القراءة ترى ما كتبه الآخر
        o.close()

    def test_failed_statement_mid_transaction_rolls_back_the_whole_transaction(self):
        self.c.execute("INSERT INTO meta(key,value) VALUES('half','x')")
        self.assertTrue(self.c.in_transaction)
        with self.assertRaises(sqlite3.Error):
            self.c.execute("INSERT INTO no_such_table VALUES(1)")
        self.assertFalse(self.c.in_transaction)
        self.assertIsNone(self.c.execute("SELECT value FROM meta WHERE key='half'").fetchone())

    def test_successful_writes_still_commit(self):
        db.set_json("k", {"a": 1})
        self.assertFalse(self.c.in_transaction)
        o = other()
        self.assertEqual(o.execute("SELECT value FROM meta WHERE key='k'").fetchone()[0], '{"a": 1}')
        o.close()

    def test_shared_connection_uses_the_guarded_class(self):
        self.assertIsInstance(self.c, db._Conn)


if __name__ == "__main__":
    unittest.main(verbosity=1)
