"""إنذار فراغ البطارية (battwatch): متى ينذر وبأي مستوى ونص، وحدث واحد لكل مستوى في نوبة التفريغ. معزول: لا قراءة طاقة ولا قاعدة.

Run: python -X utf8 -B tests/battwatch_check.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import battwatch as bw

CRIT = {"ok": True, "actionName": "إيقاف تشغيل", "level": 5, "lowLevel": 10}


def ps(pct, ac=False, sec=None, **kw):
    return {"ac": ac, "pct": pct, "charging": False, "noBattery": False, "secLeft": sec, "saver": False, **kw}


class Evaluate(unittest.TestCase):
    def test_silent_on_charger_without_battery_or_when_unknown(self):
        for p in (ps(3, ac=True), ps(3, ac=None), ps(3, noBattery=True), ps(None), None, "x", ps(16), ps(80, sec=7200)):
            self.assertIsNone(bw.evaluate(p, CRIT), p)

    def test_levels_from_percent_follow_the_windows_thresholds(self):
        self.assertEqual(bw.thresholds(CRIT), (15, 10, 5))
        self.assertEqual(bw.evaluate(ps(15), CRIT)["level"], "warn")
        self.assertEqual(bw.evaluate(ps(11), CRIT)["level"], "warn")
        self.assertEqual(bw.evaluate(ps(10), CRIT)["level"], "bad")
        self.assertEqual(bw.thresholds({"ok": True, "level": 12, "lowLevel": 20}), (25, 17, 12))
        self.assertEqual(bw.evaluate(ps(24), {"ok": True, "level": 12, "lowLevel": 20})["level"], "warn")
        self.assertEqual(bw.thresholds(None), (15, 10, 5))
        self.assertEqual(bw.thresholds({"level": "x", "lowLevel": 0}), (15, 10, 5))

    def test_time_left_can_raise_the_level_but_unknown_or_zero_time_cannot(self):
        self.assertEqual(bw.evaluate(ps(40, sec=19 * 60), CRIT)["level"], "warn")
        self.assertEqual(bw.evaluate(ps(14, sec=372), CRIT)["level"], "bad")
        self.assertIsNone(bw.evaluate(ps(40, sec=0), CRIT))
        self.assertIsNone(bw.evaluate(ps(40, sec=21 * 60), CRIT))

    def test_text_names_percent_time_and_the_critical_action(self):
        a = bw.evaluate(ps(14, sec=372), CRIT)
        self.assertEqual(a, {"level": "bad", "pct": 14, "secLeft": 372, "critLevel": 5,
                             "text": "البطارية 14% ويبقى نحو 6 دقائق؛ وصّل الشاحن، فعند 5% ينفّذ الجهاز «إيقاف تشغيل»."})
        self.assertEqual(bw.evaluate(ps(12), {"ok": False})["text"], "البطارية 12%؛ وصّل الشاحن.")
        self.assertEqual([bw._mins(s) for s in (20, 60, 120, 600, 660)], ["دقيقة", "دقيقة", "دقيقتين", "10 دقائق", "11 دقيقة"])


class Guard(unittest.TestCase):
    def setUp(self):
        self.ep, self.events, self.now = None, [], 1794500000

    def run_guard(self, alert, dt=60):
        self.now += dt
        return bw.guard(now=self.now, current=lambda: alert, load=lambda: self.ep, save=lambda v: setattr(self, "ep", v), add_event=lambda *a: self.events.append(a))

    def test_one_event_per_level_per_episode(self):
        warn, bad = bw.evaluate(ps(14), CRIT), bw.evaluate(ps(9), CRIT)
        self.assertEqual(self.run_guard(None), "ok")
        self.assertEqual(self.run_guard(warn), "alerted: warn")
        self.assertEqual(self.run_guard(warn), "alert: warn")
        self.assertEqual(self.run_guard(bad), "alerted: bad")
        self.assertEqual(self.run_guard(bad), "alert: bad")
        self.assertEqual(self.run_guard(warn), "alert: warn")   # لا حدث عند النزول
        self.assertEqual([(e[1], e[2], e[3]) for e in self.events], [("health", "warn", "البطارية منخفضة"), ("health", "error", "البطارية توشك على الفراغ")])
        self.assertIn("14%", self.events[0][4])

    def test_flapping_around_the_threshold_does_not_repeat_within_an_hour_but_a_new_episode_alerts_again(self):
        warn = bw.evaluate(ps(15), CRIT)
        self.run_guard(warn); self.run_guard(None); self.run_guard(warn); self.run_guard(None)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.run_guard(bw.evaluate(ps(9), CRIT)), "alerted: bad")   # التصعيد يُبلَّغ ولو في النوبة نفسها
        self.run_guard(None)
        self.assertEqual(self.run_guard(warn, dt=3600), "alerted: warn")
        self.assertEqual(len(self.events), 3)

    def test_mini_never_raises(self):
        orig = bw.current
        try:
            bw.current = lambda: 1 / 0
            self.assertIsNone(bw.mini())
        finally:
            bw.current = orig


if __name__ == "__main__":
    unittest.main(verbosity=1)
