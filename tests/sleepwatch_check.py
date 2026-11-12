"""تشخيص السكون (sleepwatch): تحليل XML بشكل سجل ويندوز الحقيقي، ربط سبب النوم باستيقاظه، الاستيقاظ الفوري والبطيء،
مصدر الاستيقاظ، والملاحظات. معزول: لا wevtutil (الاستعلام محقون).

Run: python -X utf8 -B tests/sleepwatch_check.py
"""
import calendar
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sleepwatch as sw

HEAD = "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System><Provider Name='%s' Guid='{x}'/><EventID>%d</EventID><Version>3</Version><TimeCreated SystemTime='%s'/><Channel>System</Channel></System><EventData>%s</EventData></Event>"


def data(**kw):
    return "".join("<Data Name='%s'>%s</Data>" % kv for kv in kw.items())


def wake(sleep, woke, wake_ms=187, driver_ms=181, src=1, text="", owner="", state=4):
    return HEAD % ("Microsoft-Windows-Power-Troubleshooter", 1, woke.replace("Z", ".7073091Z"), data(
        SleepTime=sleep.replace("Z", ".4222095Z"), WakeTime=woke.replace("Z", ".6877735Z"), SleepDuration=978, WakeDuration=wake_ms, DriverInitDuration=driver_ms,
        BiosInitDuration=1503, TargetState=state, EffectiveState=state, WakeSourceType=src, WakeSourceText=text, WakeTimerOwner=owner))


def enter(at, reason=0):
    return HEAD % ("Microsoft-Windows-Kernel-Power", 42, at.replace("Z", ".3962297Z"), data(TargetState=4, EffectiveState=4, Reason=reason, Flags=4))


def ts(iso):
    return calendar.timegm(tuple(int(x) for x in (iso[0:4], iso[5:7], iso[8:10], iso[11:13], iso[14:16], iso[17:19])) + (0, 0, 0))


XML = (wake("2026-11-12T07:56:03Z", "2026-11-12T08:35:29Z") + enter("2026-11-12T07:56:04Z", 7)
       + wake("2026-11-12T07:42:48Z", "2026-11-12T07:43:17Z", wake_ms=15997, driver_ms=15960) + enter("2026-11-12T07:42:50Z", 0)
       + wake("2026-11-11T20:00:00Z", "2026-11-11T20:01:00Z", src=5, text="USB Root Hub") + enter("2026-11-11T19:59:58Z", 4)
       + wake("2026-11-10T01:00:00Z", "2026-11-10T06:00:00Z", src=0, owner="\\Device\\HarddiskVolume3\\Windows\\System32\\svchost.exe", state=5)
       + HEAD % ("Microsoft-Windows-Kernel-Power", 107, "2026-11-12T07:56:05.1Z", data(TargetState=4))
       + wake("2026-11-09T10:00:00Z", "2026-11-09T09:00:00Z"))   # استيقاظ قبل النوم: يُهمل


class Parse(unittest.TestCase):
    def test_reads_wakes_and_enters_and_drops_other_and_broken_events(self):
        wakes, enters = sw.parse(XML)
        self.assertEqual((len(wakes), len(enters)), (4, 3))
        w = wakes[0]
        self.assertEqual((w["sleepTs"], w["wakeTs"], w["wakeMs"], w["driverMs"], w["biosMs"], w["enterMs"], w["srcType"], w["state"]),
                         (ts("2026-11-12T07:56:03Z"), ts("2026-11-12T08:35:29Z"), 187, 181, 1503, 978, 1, 4))
        self.assertEqual(enters[0], {"ts": ts("2026-11-12T07:56:04Z"), "reason": 7})
        self.assertEqual(sw.parse(""), ([], []))
        self.assertEqual(sw.parse(None), ([], []))


class Sessions(unittest.TestCase):
    def setUp(self):
        self.items = sw.sessions(*sw.parse(XML))

    def test_newest_first_with_reason_source_and_flags(self):
        a, b, c, d = self.items
        self.assertEqual((a["asleepS"], a["wakeS"], a["reason"], a["source"], a["instant"], a["slow"]), (2366, 0.2, "خمول الجهاز", "زر الطاقة", False, False))
        self.assertEqual((b["asleepS"], b["wakeS"], b["driverS"], b["reason"], b["instant"], b["slow"]), (29, 16.0, 16.0, "زر الطاقة أو الغطاء", True, True))
        self.assertEqual((c["reason"], c["source"], c["instant"]), ("طلب من تطبيق", "جهاز: USB Root Hub", True))
        self.assertEqual((d["reason"], d["hibernate"], d["asleepS"]), (None, True, 18000))
        self.assertTrue(d["source"].startswith("مؤقّت: \\Device\\"))

    def test_unknown_codes_are_shown_by_number_not_guessed(self):
        items = sw.sessions(*sw.parse(wake("2026-11-12T07:56:03Z", "2026-11-12T08:35:29Z", src=9) + enter("2026-11-12T07:56:03Z", 33)))
        self.assertEqual((items[0]["reason"], items[0]["source"]), ("سبب 33", "نوع 9"))

    def test_summary_counts(self):
        s = sw.summary(self.items)
        self.assertEqual((s["days"], s["n"], s["instant"], s["slow"], s["maxWakeS"], s["avgWakeS"]), (14, 4, 2, 1, 16.0, 4.2))
        self.assertEqual(s["slowest"], {"ts": ts("2026-11-12T07:43:17Z"), "wakeS": 16.0, "driverS": 16.0, "biosS": 1.5})
        self.assertEqual({x["source"]: x["n"] for x in s["instantSources"]}, {"زر الطاقة": 1, "جهاز: USB Root Hub": 1})
        self.assertEqual(sw.summary([])["n"], 0)
        self.assertIsNone(sw.summary([])["slowest"])


class Notes(unittest.TestCase):
    def test_notes_for_instant_wakes_and_slow_resume(self):
        notes = sw.notes(sw.summary(sw.sessions(*sw.parse(XML))))
        self.assertEqual([(n[0], n[1]) for n in notes], [("warn", "الجهاز يستيقظ بُعيد نومه"), ("info", "الخروج من السكون بطيء أحيانًا")])
        self.assertIn("2 من 4", notes[0][2])
        self.assertIn("16.0 ث", notes[1][2]); self.assertIn("تهيئة التعريفات", notes[1][2])

    def test_power_button_only_is_info_and_quiet_cases_say_nothing(self):
        xml = wake("2026-11-12T07:00:00Z", "2026-11-12T07:00:30Z") + wake("2026-11-12T08:00:00Z", "2026-11-12T08:00:40Z")
        notes = sw.notes(sw.summary(sw.sessions(*sw.parse(xml))))
        self.assertEqual([(n[0], n[1]) for n in notes], [("info", "الجهاز يستيقظ بُعيد نومه")])
        calm = sw.summary(sw.sessions(*sw.parse(wake("2026-11-12T01:00:00Z", "2026-11-12T08:00:00Z"))))
        self.assertEqual(sw.notes(calm), [])
        self.assertEqual(sw.notes(None), [])
        self.assertEqual(sw.notes(sw.summary([])), [])


class Payload(unittest.TestCase):
    def test_payload_queries_the_last_14_days(self):
        asked = []
        p = sw.payload(now=ts("2026-11-12T12:00:00Z"), query=lambda since: asked.append(since) or XML)
        self.assertEqual(asked, ["2026-10-29T12:00:00.000Z"])
        self.assertEqual((p["n"], len(p["items"])), (4, 4))
        self.assertEqual(sw.payload(now=ts("2026-11-12T12:00:00Z"), query=lambda since: "")["n"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
