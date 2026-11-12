"""حارس المعالج (cpuprio): أي العمليات تُخفض، وما لا يُمسّ، وما يُسجَّل، وحالة «مخفّضة» للويدجت.
معزول: لا تغيير أولوية حقيقي ولا قاعدة حية (القياس والتغيير محقونان)، عدا اختبار واحد يخفض عملية ابن يُنشئها هو.

Run: python -X utf8 -B tests/cpuprio_check.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
CONN = sqlite3.connect(":memory:", check_same_thread=False)
CONN.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
EVENTS = []


def _meta_get(k, d=None):
    r = CONN.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
    return r[0] if r else d


sys.modules["db"] = types.SimpleNamespace(_lock=threading.RLock(), conn=lambda: CONN, add_event=lambda *a: EVENTS.append(a), _meta_get=_meta_get)
import cpuprio

S = 10_000_000   # ثانية معالج بوحدات FILETIME


class Busy(unittest.TestCase):
    def test_picks_only_busy_processes_of_that_name(self):
        a = {10: ("python.exe", 111, 0), 11: ("python.exe", 222, 0), 12: ("Python.EXE", 333, 0), 13: ("node.exe", 444, 0), -14: ("python.exe", 0, 0)}
        b = {10: ("python.exe", 111, 12 * S), 11: ("python.exe", 222, int(0.2 * S)), 12: ("Python.EXE", 333, 1 * S), 13: ("node.exe", 444, 9 * S), -14: ("python.exe", 0, 0)}
        self.assertEqual(cpuprio.busy_pids("python.exe", a, b, 1.0), [(10, 111, 12.0), (12, 333, 1.0)])

    def test_reused_pid_and_new_process_are_ignored(self):
        a = {10: ("python.exe", 111, 0)}
        b = {10: ("python.exe", 999, 5 * S), 20: ("python.exe", 555, 5 * S)}   # 10 أُعيد استعماله، 20 بلا لقطة سابقة
        self.assertEqual(cpuprio.busy_pids("python.exe", a, b, 1.0), [])


class Lower(unittest.TestCase):
    def setUp(self):
        EVENTS.clear()
        CONN.execute("DELETE FROM meta")

    def run_lower(self, name, busy, results):
        calls = []

        def setter(pid):
            calls.append(pid)
            return results[pid]
        code, reply = cpuprio.lower(name, measure=lambda n: busy, setter=setter, now=1000)
        return code, reply, calls

    def test_protected_and_bad_names_are_refused_without_touching_anything(self):
        for name in ("explorer.exe", "PYTHONW.EXE", "pwsh.exe", "csrss.exe", "", None, "x" * 300):
            code, reply, calls = self.run_lower(name, [(1, 1, 5.0)], {1: "lowered"})
            self.assertEqual(code, 400, name)
            self.assertEqual(calls, [])
        self.assertEqual(EVENTS, [])

    def test_lowers_busy_processes_records_state_and_one_event(self):
        code, reply, calls = self.run_lower("python.exe", [(10, 111, 12.0), (12, 333, 1.0)], {10: "lowered", 12: "already"})
        self.assertEqual((code, calls), (200, [10, 12]))
        self.assertEqual((reply["lowered"], reply["already"], reply["ok"]), (1, 1, True))
        self.assertIn("خُفضت أولوية python.exe", reply["text"])
        self.assertEqual(len(EVENTS), 1)
        self.assertEqual(json.loads(_meta_get("cpu_lowered"))["pids"], [[10, 111], [12, 333]])

    def test_nothing_busy_is_ok_with_no_event(self):
        code, reply, calls = self.run_lower("python.exe", [], {})
        self.assertEqual((code, reply["lowered"], calls, EVENTS), (200, 0, [], []))
        self.assertIn("لا عملية", reply["text"])

    def test_denied_and_skipped_are_reported_and_not_recorded(self):
        code, reply, _ = self.run_lower("ocr.exe", [(5, 1, 3.0)], {5: "denied"})
        self.assertIn("صلاحية أعلى", reply["text"])
        code, reply, _ = self.run_lower("ocr.exe", [(5, 1, 3.0)], {5: "skipped"})
        self.assertIn("يدويًا", reply["text"])
        self.assertIsNone(_meta_get("cpu_lowered"))
        self.assertEqual(EVENTS, [])

    def test_never_lowers_own_process(self):
        code, reply, calls = self.run_lower("python.exe", [(os.getpid(), 1, 9.0)], {})
        self.assertEqual(calls, [])

    def test_lowered_state_follows_name_and_liveness(self):
        self.run_lower("python.exe", [(10, 111, 12.0)], {10: "lowered"})
        self.assertTrue(cpuprio.lowered_state("Python.exe", alive=lambda p, c: (p, c) == (10, 111)))
        self.assertFalse(cpuprio.lowered_state("python.exe", alive=lambda p, c: False))   # انتهت العملية
        self.assertFalse(cpuprio.lowered_state("node.exe", alive=lambda p, c: True))
        self.assertFalse(cpuprio.lowered_state("", alive=lambda p, c: True))

    def test_post_routes_only_its_own_path(self):
        self.assertIsNone(cpuprio.post("/api/other", {}))
        self.assertEqual(cpuprio.post("/api/cpu-lower", {"name": "explorer.exe"})[0], 400)


class RealChild(unittest.TestCase):
    """تغيير حقيقي على عملية ابن يملكها الاختبار وحده: NORMAL ← BELOW_NORMAL، ثم «already»، وحالة الحياة قبل الإنهاء وبعده."""

    def test_set_below_and_alive_on_own_child(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], creationflags=0x08000000 | 0x20)   # NORMAL_PRIORITY_CLASS صريحة: مشغّل CI نفسه بأولوية منخفضة فيرثها الابن
        try:
            created = cpuprio.cpuwatch._snap()[child.pid][1]
            self.assertEqual(cpuprio._set_below(child.pid), "lowered")
            self.assertEqual(cpuprio._set_below(child.pid), "already")
            self.assertTrue(cpuprio._alive(child.pid, created))
            self.assertFalse(cpuprio._alive(child.pid, created + 1))
        finally:
            child.kill()
            child.wait()
        self.assertFalse(cpuprio._alive(child.pid, created))


if __name__ == "__main__":
    unittest.main(verbosity=1)
