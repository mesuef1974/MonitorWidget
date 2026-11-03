"""التزام الذاكرة (commitinfo): الترتيب والتقريب بصفوف وهمية، والقياس الحيّ عبر GetPerformanceInfo متسق مع GlobalMemoryStatusEx،
وعقد GET /api/commit في جدول المسارات والواجهة وثابت الفحص المرئي. معزول: لا خادم ولا قاعدة بيانات ولا استيراد للتطبيق.

Run: python -X utf8 -B tests/commit_check.py
"""
import ctypes
import json
import os
import sys
import unittest
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import commitinfo   # noqa: E402

FAKE = [{"pid": 10, "name": "a.exe", "commitMB": 100.4, "wsMB": 50.6},
        {"pid": 11, "name": "B.exe", "commitMB": 900.6, "wsMB": 10},
        {"pid": 12, "name": "c.exe", "commitMB": 900.6, "wsMB": 20},
        {"pid": 13, "name": "d.exe", "commitMB": 0, "wsMB": 0},
        {"pid": 14, "name": "e.exe", "commitMB": None, "wsMB": None}]
ST = {"usedGB": 10.0, "limitGB": 20.0, "peakGB": 12.0, "headroomGB": 10.0, "pct": 50.0}
KEYS = {"ok", "now", "self", "usedGB", "limitGB", "peakGB", "headroomGB", "pct", "top", "procs", "skipped", "procsGB"}


class RankTests(unittest.TestCase):
    def test_order_tiebreak_rounding(self):
        top = commitinfo.rank(FAKE, 3)
        self.assertEqual([t["pid"] for t in top], [11, 12, 10])   # تنازليًا؛ التعادل بالاسم بلا حساسية لحالة الأحرف
        self.assertEqual(top[0], {"pid": 11, "name": "B.exe", "commitMB": 901, "wsMB": 10})
        self.assertEqual(top[2], {"pid": 10, "name": "a.exe", "commitMB": 100, "wsMB": 51})

    def test_n_clamped_and_missing_values(self):
        self.assertEqual(len(commitinfo.rank(FAKE, 0)), 1)
        self.assertEqual(len(commitinfo.rank(FAKE, 999)), 5)
        self.assertEqual(commitinfo.rank(FAKE, 5)[-1], {"pid": 14, "name": "e.exe", "commitMB": 0, "wsMB": 0})   # None تُعامل صفرًا
        self.assertEqual(commitinfo.rank([], 10), [])

    def test_payload_injected(self):
        p = commitinfo.payload(2, rows=FAKE, st=ST, now=123)
        self.assertEqual(set(p), KEYS)
        self.assertEqual((p["ok"], p["now"], p["procs"], p["skipped"], p["pct"], len(p["top"])), (True, 123, 5, 0, 50.0, 2))
        self.assertEqual(p["procsGB"], round((100.4 + 900.6 + 900.6) / 1024.0, 2))
        self.assertEqual(p["self"], os.getpid())

    def test_payload_without_status(self):
        p = commitinfo.payload(1, rows=FAKE, st={}, now=1)
        self.assertEqual(set(p), KEYS)
        self.assertTrue(all(p[k] is None for k in ("usedGB", "limitGB", "peakGB", "headroomGB", "pct")))


class LiveTests(unittest.TestCase):
    """القياس الحيّ على Windows بلا صلاحيات مسؤول (يعمل على عدّاء CI)."""

    def test_status_matches_global_memory_status(self):
        st = commitinfo.status()
        self.assertIsNotNone(st)
        self.assertGreater(st["usedGB"], 0)
        self.assertLessEqual(st["usedGB"], st["limitGB"])
        self.assertGreaterEqual(st["peakGB"], st["usedGB"])
        self.assertTrue(0 < st["pct"] <= 100)
        self.assertAlmostEqual(st["headroomGB"], st["limitGB"] - st["usedGB"], delta=0.02)

        class MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("x", ctypes.c_ulonglong)]
        m = MS()
        m.l = ctypes.sizeof(MS)
        self.assertTrue(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)))
        self.assertAlmostEqual((m.tpf - m.apf) / commitinfo.GB, st["usedGB"], delta=0.5)   # الرقم نفسه الذي تعرضه snapshot وTask Manager
        self.assertAlmostEqual(m.tpf / commitinfo.GB, st["limitGB"], delta=0.05)

    def test_scan_reads_self_without_admin(self):
        rows, skipped = commitinfo.scan()
        me = [r for r in rows if r["pid"] == os.getpid()]
        self.assertEqual(len(me), 1)
        self.assertTrue(me[0]["name"].lower().startswith("python"))
        self.assertGreater(me[0]["commitMB"], 1)
        self.assertGreater(me[0]["wsMB"], 1)
        self.assertGreaterEqual(skipped, 0)
        pids = [r["pid"] for r in rows]
        self.assertEqual(len(pids), len(set(pids)))
        self.assertTrue(all(p > 0 for p in pids))

    def test_payload_live(self):
        p = commitinfo.payload(3)
        self.assertEqual(set(p), KEYS)
        self.assertEqual(len(p["top"]), 3)
        self.assertGreaterEqual(p["procs"], 3)
        self.assertLessEqual(p["procsGB"], p["usedGB"] + 2.0)   # التزام العمليات المقروءة جزء من الالتزام الكلي (سماحية للتغيّر بين القياسين)
        self.assertGreaterEqual(p["top"][0]["commitMB"], p["top"][-1]["commitMB"])


class ContractTests(unittest.TestCase):
    def test_route_table(self):
        src = (ROOT / "routes_read.py").read_text(encoding="utf-8-sig")
        self.assertIn("import commitinfo", src)
        self.assertIn('"/api/commit": (lambda q: commitinfo.payload(_ival(q, "n", 10, 1, 50))', src)

    def test_ui_and_fixture(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('api("/api/commit")', html)
        self.assertIn('panel("الالتزام"', html)
        self.assertIn('panel("ما الذي يخفّض الالتزام؟"', html)
        self.assertIn("لا الالتزام", html)   # لوحة الإعدادات تقول صراحة إن التنظيف لا يخفّض الالتزام
        fx = json.loads((ROOT / "tests" / "fixtures" / "api" / "commit.json").read_text(encoding="utf-8"))
        self.assertEqual(set(fx), KEYS)
        self.assertTrue(1 <= len(fx["top"]) <= 10)
        for r in fx["top"]:
            self.assertEqual(set(r), {"pid", "name", "commitMB", "wsMB"})


if __name__ == "__main__":
    unittest.main()
