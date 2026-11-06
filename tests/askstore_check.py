"""ذاكرة «اسأل المرصد» ومقارنة الفترات، على SQLite في الذاكرة.

Run: python -X utf8 -B tests/askstore_check.py
"""
import os
import sqlite3
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import askstore
import db


class Store(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:", check_same_thread=False)
        self.c.execute("CREATE TABLE actions(ts INT,kind TEXT,src TEXT,ok INT,detail TEXT)")
        p = patch.object(db, "conn", lambda: self.c)
        p.start()
        self.addCleanup(p.stop)
        askstore._ready = None

    def post(self, route, body):
        return askstore.post(route, body)

    def saved(self):
        return askstore.save("سؤال", {"text": "جواب", "model": "m", "ms": 900})

    def test_turns_come_back_in_order(self):
        rid = self.saved()
        turns = askstore.payload()["turns"]
        self.assertEqual([(t["role"], t["content"]) for t in turns], [("user", "سؤال"), ("assistant", "جواب")])
        self.assertEqual((turns[1]["id"], turns[1]["model"], turns[1]["ms"]), (rid, "m", 900))

    def test_expired_turns_are_hidden_before_prune(self):
        self.saved()
        self.c.execute("UPDATE ask_turns SET ts=?", (int(time.time()) - (askstore.KEEP_DAYS + 1) * 86400,))
        self.assertEqual(askstore.payload()["turns"], [])

    def test_retention_matches_page_promise(self):
        self.assertEqual(db.RETENTION["ask_turns"], ("ts", askstore.KEEP_DAYS, None))

    def test_rating_toggles_and_counts(self):
        rid = self.saved()
        self.assertEqual(self.post("/api/assistant-rate", {"id": rid, "value": -1})[1]["ratings"], {"good": 0, "bad": 1})
        self.assertEqual(self.post("/api/assistant-rate", {"id": rid, "value": 0})[1]["ratings"], {"good": 0, "bad": 0})

    def test_rating_only_on_replies_with_valid_value(self):
        rid = self.saved()
        self.assertEqual(self.post("/api/assistant-rate", {"id": rid - 1, "value": 1})[0], 404)
        for body in ({"id": rid, "value": 5}, {"id": "x", "value": 1}, {"id": rid, "value": True}, {}):
            self.assertEqual(self.post("/api/assistant-rate", body)[0], 400)

    def test_clear_removes_turns_keeps_facts_and_audits(self):
        self.saved()
        self.post("/api/assistant-fact", {"text": "لا تقترح تفريغ الكرت"})
        self.assertEqual(self.post("/api/assistant-clear", {})[0], 200)
        p = askstore.payload()
        self.assertEqual((p["turns"], len(p["facts"])), ([], 1))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM actions WHERE kind='ask_clear'").fetchone()[0], 1)

    def test_fact_add_duplicate_and_remove(self):
        self.post("/api/assistant-fact", {"text": " أ "})
        facts = self.post("/api/assistant-fact", {"text": "أ"})[1]["facts"]
        self.assertEqual([f["text"] for f in facts], ["أ"])
        self.assertEqual(askstore.fact_texts(), ["أ"])
        self.assertEqual(self.post("/api/assistant-fact", {"remove": facts[0]["id"]})[1]["facts"], [])

    def test_fact_limits(self):
        for body in ({"text": ""}, {"text": 5}, {"text": "س" * (askstore.MAX_FACT + 1)}):
            self.assertEqual(self.post("/api/assistant-fact", body)[0], 400)
        for i in range(askstore.MAX_FACTS):
            self.post("/api/assistant-fact", {"text": str(i)})
        self.assertEqual(self.post("/api/assistant-fact", {"text": "زيادة"})[0], 400)

    def test_foreign_route_is_not_ours(self):
        self.assertIsNone(askstore.post("/api/ask", {}))

    def test_save_failure_does_not_raise(self):
        with patch.object(askstore, "_db", side_effect=sqlite3.Error("x")), patch.object(askstore.errlog, "swallowed", lambda: None):
            self.assertIsNone(self.saved())


class Compare(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:", check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript("CREATE TABLE samples(ts INT,source TEXT,commit_pct REAL,ram_pct REAL,avail_gb REAL,disk_free_gb REAL);"
                             "CREATE TABLE events(ts INT);CREATE TABLE trims(ts INT);"
                             "CREATE TABLE cleanup_runs(ts INT,dry INT);")
        self.now = int(time.time())

    def sample(self, ago, commit, ram, avail, disk):
        self.c.execute("INSERT INTO samples VALUES(?,?,?,?,?,?)", (self.now - ago, "live", commit, ram, avail, disk))

    def test_splits_two_equal_windows(self):
        self.sample(3600, 70, 60, 9, 120)
        self.sample(30 * 3600, 50, 40, 12, 130)
        self.sample(60 * 3600, 99, 99, 1, 1)         # خارج الفترتين
        self.c.execute("INSERT INTO events VALUES(?)", (self.now - 7200,))
        r = db.compare(24, self.c)
        self.assertEqual(r["hours"], 24)
        self.assertEqual((r["now"]["samples"], r["now"]["commitAvg"], r["now"]["diskFreeGB"], r["now"]["events"]), (1, 70, 120, 1))
        self.assertEqual((r["before"]["samples"], r["before"]["ramAvg"], r["before"]["availMin"], r["before"]["events"]), (1, 40, 12, 0))

    def test_empty_history(self):
        r = db.compare(24, self.c)
        self.assertEqual((r["now"]["samples"], r["before"]["diskFreeGB"]), (0, None))


if __name__ == "__main__":
    unittest.main(verbosity=1)
