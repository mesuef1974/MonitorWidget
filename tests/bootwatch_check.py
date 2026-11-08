"""رصد الإقلاع غير النظيف (bootwatch): تحليل XML حقيقي الشكل، وأولوية الأنواع (عطل ثم سكون ثم زر ثم انقطاع)، وتحويل الوقت،
والحدث لكل حادثة بوقتها (فريد فتكراره آمن)، ومهمة job بـ wevtutil وdb وهميين.
معزول: لا قراءة سجل حقيقية ولا قاعدة بيانات.

Run: python -X utf8 -B tests/bootwatch_check.py
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
import bootwatch as bw


def ev_xml(when, bug=0, sleep=0, button=0):
    return ("<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='Microsoft-Windows-Kernel-Power'/>"
            "<EventID>41</EventID><TimeCreated SystemTime='%s.123456700Z'/></System><EventData>"
            "<Data Name='BugcheckCode'>%d</Data><Data Name='SleepInProgress'>%d</Data>"
            "<Data Name='PowerButtonTimestamp'>%d</Data></EventData></Event>") % (when, bug, sleep, button)


class Parse(unittest.TestCase):
    def test_fields_and_time(self):
        evs = bw.parse(ev_xml("2026-11-06T02:25:10") + ev_xml("2026-10-25T10:32:00", bug=239))
        self.assertEqual(len(evs), 2)
        self.assertEqual(evs[0]["ts"], calendar.timegm((2026, 11, 6, 2, 25, 10)))
        self.assertEqual(evs[1]["bugcheck"], 239)

    def test_empty_and_garbage(self):
        self.assertEqual(bw.parse(""), [])
        self.assertEqual(bw.parse(None), [])
        self.assertEqual(bw.parse("<Event>بلا وقت</Event>"), [])

    def test_hex_value(self):
        self.assertEqual(bw.parse(ev_xml("2026-11-06T02:25:10").replace(">0</Data><Data Name='SleepInProgress'", ">0xef</Data><Data Name='SleepInProgress'"))[0]["bugcheck"], 239)


class Classify(unittest.TestCase):
    def test_priority(self):
        self.assertEqual(bw.classify({"bugcheck": 239, "sleep": 4, "button": 9})[0], "عطل")
        self.assertEqual(bw.classify({"bugcheck": 0, "sleep": 4, "button": 9})[0], "سكون")
        self.assertEqual(bw.classify({"bugcheck": 0, "sleep": 0, "button": 9})[0], "زر")
        self.assertEqual(bw.classify({"bugcheck": 0, "sleep": 0, "button": 0})[0], "انقطاع")

    def test_bugcheck_code_in_hex_and_severity(self):
        kind, sev, desc = bw.classify({"bugcheck": 239, "sleep": 0, "button": 0})
        self.assertEqual(sev, "error")
        self.assertIn("0xEF", desc)
        self.assertEqual(bw.classify({"bugcheck": 0, "sleep": 4, "button": 0})[1], "warn")
        self.assertEqual(bw.classify({"bugcheck": 0, "sleep": 0, "button": 0})[1], "error")


class Emit(unittest.TestCase):
    def setUp(self):
        EVENTS.clear()

    def test_event_per_incident_at_its_own_time(self):
        evs = bw.parse(ev_xml("2026-11-06T02:25:10") + ev_xml("2026-11-04T11:18:00", sleep=4, button=7))
        n = bw.emit(evs, lambda *a: EVENTS.append(a))
        self.assertEqual(n, 2)
        self.assertEqual([e[0] for e in EVENTS], [calendar.timegm((2026, 11, 6, 2, 25, 10)), calendar.timegm((2026, 11, 4, 11, 18, 0))])
        self.assertEqual(EVENTS[0][1], "boot")
        self.assertEqual(EVENTS[0][3], "إقلاع غير نظيف: انقطاع")
        self.assertEqual(EVENTS[1][3], "إقلاع غير نظيف: سكون")

    def test_same_input_same_keys(self):
        x = ev_xml("2026-11-06T02:25:10")
        bw.emit(bw.parse(x), lambda *a: EVENTS.append(a))
        bw.emit(bw.parse(x), lambda *a: EVENTS.append(a))
        self.assertEqual(len({(e[0], e[1], e[3]) for e in EVENTS}), 1)

    def test_none_no_events(self):
        self.assertEqual(bw.emit([], lambda *a: EVENTS.append(a)), 0)
        self.assertEqual(EVENTS, [])


class Job(unittest.TestCase):
    def test_job_uses_query_and_db(self):
        EVENTS.clear()
        seen = []
        orig = bw._query
        bw._query = lambda since: (seen.append(since), ev_xml("2026-11-06T02:25:10"))[1]
        try:
            self.assertEqual(bw.job(), "1 حادثة")
        finally:
            bw._query = orig
        self.assertEqual(len(EVENTS), 1)
        self.assertRegex(seen[0], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.000Z$")


if __name__ == "__main__":
    unittest.main()
