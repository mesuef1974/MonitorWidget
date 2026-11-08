"""رصد استنفاد المنافذ (portwatch): تحليل أوقات الحوادث، وعدّ الاتصالات لكل عملية، ووصف أكبر العمليات، والحادثة الحديثة تُنسب
لها لقطة والقديمة لا، والحادثة لا تتكرر، ومهمة job بـ wevtutil وnetstat وdb وهميين.
معزول: لا قراءة سجل ولا netstat حقيقيان.

Run: python -X utf8 -B tests/portwatch_check.py
"""
import calendar
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EVENTS = []
sys.modules["db"] = types.SimpleNamespace(add_event=lambda *a: EVENTS.append(a))
sys.modules["commitinfo"] = types.SimpleNamespace(scan=lambda with_cmd=False: ([{"pid": 7, "name": "docker.exe"}], 0))
import portwatch as pw

NETSTAT = """  Proto  Local Address  Foreign Address  State  PID
  TCP 1.1.1.1:1 2.2.2.2:2 TIME_WAIT 7
  TCP 1.1.1.1:3 2.2.2.2:2 TIME_WAIT 7
  TCP 1.1.1.1:4 2.2.2.2:2 ESTABLISHED 7
  TCP 1.1.1.1:5 2.2.2.2:2 TIME_WAIT 9
  UDP 0.0.0.0:53 *:* 11
"""


def ev(when):
    return "<Event xmlns='x'><System><EventID>4231</EventID><TimeCreated SystemTime='%s.1Z'/></System></Event>" % when


T = calendar.timegm((2026, 11, 8, 10, 0, 0))


class Parse(unittest.TestCase):
    def test_times_sorted(self):
        self.assertEqual(pw.parse_events(ev("2026-11-08T10:00:00") + ev("2026-11-08T09:00:00")), [T - 3600, T])

    def test_empty(self):
        self.assertEqual(pw.parse_events(""), [])
        self.assertEqual(pw.parse_events(None), [])


class Count(unittest.TestCase):
    def test_by_pid_tcp_only(self):
        by, st = pw.count_by_pid(NETSTAT)
        self.assertEqual(by, {7: 3, 9: 1})
        self.assertEqual(st["TIME_WAIT"], 3)

    def test_describe_ranks_and_names(self):
        by, st = pw.count_by_pid(NETSTAT)
        s = pw.describe(by, st, {7: "docker.exe"})
        self.assertTrue(s.index("docker.exe") < s.index("غير معروف"))
        self.assertIn("TIME_WAIT الكلي: 3", s)

    def test_pid_zero_never_ranked(self):
        s = pw.describe({0: 400, 7: 3}, {"TIME_WAIT": 400}, {7: "docker.exe"})
        self.assertNotIn("pid 0", s)
        self.assertIn("docker.exe", s)
        self.assertIn("منها 400 بلا مالك", s)

    def test_describe_no_connections(self):
        self.assertIn("لا اتصالات", pw.describe({}, {}, {}))


class Emit(unittest.TestCase):
    def setUp(self):
        EVENTS.clear()

    def test_fresh_gets_snapshot_old_does_not(self):
        n = pw.emit([T - 3000, T - 60], T, lambda: "لقطة", lambda *a: EVENTS.append(a), set())
        self.assertEqual(n, 2)
        self.assertIn("لا لقطة", EVENTS[0][4])
        self.assertEqual(EVENTS[1][4], "لقطة")
        self.assertEqual(EVENTS[1][1], "net")

    def test_not_repeated(self):
        seen = set()
        pw.emit([T], T, lambda: "x", lambda *a: EVENTS.append(a), seen)
        self.assertEqual(pw.emit([T], T + 5, lambda: "x", lambda *a: EVENTS.append(a), seen), 0)
        self.assertEqual(len(EVENTS), 1)


class Job(unittest.TestCase):
    def test_job(self):
        EVENTS.clear()
        pw._seen.clear()
        calls = []

        def fake(args, timeout=30):
            calls.append(args[0])
            return ev("2099-01-01T00:00:00") if args[0] == "wevtutil" else NETSTAT
        orig = pw._run
        pw._run = fake
        try:
            self.assertEqual(pw.job(), "1 حادثة جديدة")
        finally:
            pw._run = orig
        self.assertEqual(calls, ["wevtutil", "netstat"])
        self.assertIn("docker.exe", EVENTS[0][4])


if __name__ == "__main__":
    unittest.main()
