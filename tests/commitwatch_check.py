"""رصد قفزات الالتزام (commitwatch): قفزة 52 إلى 700 MB تُسجَّل، والثابت لا، والعملية الجديدة لا تُعدّ قفزة في أول قياس،
والنافذة تنسى القديم، والعملية المختفية تُحذف، وحدّ حدث واحد لكل اسم، والمشروع في التفاصيل، ومهمة job بمسح وdb وهميين.
معزول: commitinfo وdb بدائل وهمية؛ لا مسح حقيقي ولا قاعدة بيانات.

Run: python -X utf8 -B tests/commitwatch_check.py
"""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SCANS = []
EVENTS = []
sys.modules["commitinfo"] = types.SimpleNamespace(scan=lambda with_cmd=False: (SCANS.pop(0) if SCANS else [], 0))
sys.modules["db"] = types.SimpleNamespace(add_event=lambda *a: EVENTS.append(a))
import commitwatch as cw


def row(pid, name, mb):
    return {"pid": pid, "ppid": 0, "name": name, "commitMB": mb, "wsMB": 10}


def feed(hist, series, start=1000.0, step=30):
    """series: قائمة قياسات لعملية واحدة (pid 8011)."""
    for i, mb in enumerate(series):
        cw.update(hist, [row(8011, "python.exe", mb)], start + i * step)
    return start + (len(series) - 1) * step


class Spikes(unittest.TestCase):
    def test_8011_swing_is_a_spike(self):
        h = {}
        feed(h, [52, 55, 700])
        sp = cw.find_spikes(h)
        self.assertEqual(len(sp), 1)
        self.assertEqual((sp[0]["pid"], sp[0]["from"], sp[0]["to"]), (8011, 52, 700))
        self.assertEqual(round(sp[0]["delta"]), 648)

    def test_steady_and_small_growth_are_not(self):
        h = {}
        feed(h, [500, 505, 498, 510])
        self.assertEqual(cw.find_spikes(h), [])
        h = {}
        feed(h, [100, 250, 399])    # +299 دون العتبة
        self.assertEqual(cw.find_spikes(h), [])

    def test_drop_after_spike_is_not_a_spike(self):
        h = {}
        feed(h, [52, 700, 60])
        self.assertEqual(cw.find_spikes(h), [])

    def test_new_process_has_no_baseline(self):
        h = {}
        cw.update(h, [row(1, "big.exe", 5000)], 1000.0)
        self.assertEqual(cw.find_spikes(h), [])

    def test_window_forgets_old_low_reading(self):
        h = {}
        cw.update(h, [row(1, "a.exe", 50)], 1000.0)
        cw.update(h, [row(1, "a.exe", 800)], 1000.0 + cw.WINDOW_S + 1)   # القياس الأول خرج من النافذة
        self.assertEqual(cw.find_spikes(h), [])

    def test_vanished_process_dropped_and_pid_reuse_is_separate(self):
        h = {}
        cw.update(h, [row(1, "a.exe", 50), row(2, "b.exe", 60)], 1000.0)
        cw.update(h, [row(2, "b.exe", 60)], 1030.0)
        self.assertNotIn((1, "a.exe"), h)
        cw.update(h, [row(2, "c.exe", 900)], 1060.0)    # نفس pid باسم آخر = عملية أخرى بلا قياس سابق
        self.assertEqual(cw.find_spikes(h), [])

    def test_sorted_by_size(self):
        h = {}
        cw.update(h, [row(1, "a.exe", 50), row(2, "b.exe", 100)], 1000.0)
        cw.update(h, [row(1, "a.exe", 400), row(2, "b.exe", 900)], 1030.0)
        self.assertEqual([s["name"] for s in cw.find_spikes(h)], ["b.exe", "a.exe"])


class Emit(unittest.TestCase):
    def setUp(self):
        EVENTS.clear()

    def test_event_text_and_project(self):
        h, last = {}, {}
        now = feed(h, [52, 700])
        n = cw.emit(cw.find_spikes(h), now, last, lambda *a: EVENTS.append(a), lambda pid: "المشروع ب" if pid == 8011 else None)
        self.assertEqual(n, 1)
        ts, src, sev, title, detail = EVENTS[0]
        self.assertEqual((src, sev), ("commit", "warn"))
        self.assertIn("python.exe", title)
        self.assertIn("648 MB", title)
        self.assertIn("من 52 إلى 700 MB", detail)
        self.assertIn("المشروع ب", detail)
        self.assertIn("pid 8011", detail)

    def test_unknown_project_label(self):
        h, last = {}, {}
        now = feed(h, [52, 700])
        cw.emit(cw.find_spikes(h), now, last, lambda *a: EVENTS.append(a), lambda pid: None)
        self.assertIn("غير مصنّف", EVENTS[0][4])

    def test_cooldown_per_name(self):
        h, last = {}, {}
        now = feed(h, [52, 700])
        add = lambda *a: EVENTS.append(a)   # noqa: E731
        self.assertEqual(cw.emit(cw.find_spikes(h), now, last, add, lambda p: None), 1)
        self.assertEqual(cw.emit(cw.find_spikes(h), now + 60, last, add, lambda p: None), 0)
        self.assertEqual(cw.emit(cw.find_spikes(h), now + cw.COOLDOWN_S + 1, last, add, lambda p: None), 1)
        self.assertEqual(len(EVENTS), 2)

    def test_western_digits_only(self):
        h, last = {}, {}
        now = feed(h, [52, 700])
        cw.emit(cw.find_spikes(h), now, last, lambda *a: EVENTS.append(a), lambda p: None)
        text = EVENTS[0][3] + EVENTS[0][4]
        self.assertFalse(any("٠" <= c <= "٩" for c in text))


class Job(unittest.TestCase):
    def test_job_end_to_end_with_fakes(self):
        EVENTS.clear()
        cw._hist.clear()
        cw._last.clear()
        SCANS[:] = [[row(8011, "python.exe", 52)], [row(8011, "python.exe", 700)]]
        t = [2000.0]
        real = cw.time.time
        cw.time.time = lambda: t[0]
        try:
            self.assertNotIn("حدث", cw.job())
            t[0] += 30
            msg = cw.job()
        finally:
            cw.time.time = real
        self.assertIn("حدث", msg)
        self.assertEqual(len(EVENTS), 1)
        self.assertEqual(EVENTS[0][1], "commit")


if __name__ == "__main__":
    unittest.main(verbosity=1)
