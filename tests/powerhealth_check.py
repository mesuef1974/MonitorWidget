"""صحة الطاقة (powerhealth): تحليل powercfg بالمعرّفات لا بالتسميات، ومتى لا ينفع إجراء البطارية الحرجة،
وسبب كل إيقاف مفاجئ من قراءتَي البطارية قبل الانطفاء وبعد الإقلاع، وقراءة shutdowns من قاعدة في الذاكرة.
معزول: لا powercfg حقيقي ولا سجل ولا قاعدة حية.

Run: python -X utf8 -B tests/powerhealth_check.py
"""
import os
import sqlite3
import sys
import threading
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
CONN = sqlite3.connect(":memory:", check_same_thread=False)
sys.modules["db"] = types.SimpleNamespace(_lock=threading.RLock(), conn=lambda: CONN, add_event=lambda *a: None)
import bootwatch
import powerhealth as ph


def block(guid, label, ac, dc, levels=False):
    body = ("      Minimum Possible Setting: 0x00000000\n      Maximum Possible Setting: 0x00000064\n      Possible Settings increment: 0x00000001\n"
            if levels else "      Possible Setting Index: 000\n      Possible Setting Friendly Name: Do nothing\n      Possible Setting Index: 003\n")
    return "    %s: %s  (x)\n%s    %s AC: 0x%08x\n    %s DC: 0x%08x\n\n" % (label, guid, body, label, ac, label, dc)


def text(action=(2, 2), level=(5, 5), low=(10, 10), label="Power Setting GUID"):
    return ("Power Scheme GUID: f970844b-acea-4df9-87ec-4434d3132420  (High performance)\n  Subgroup GUID: e73a048d-bf27-4f12-9731-8b2076e8891f  (Battery)\n"
            + block("5dbb7c9f-38e9-40d2-9749-4f8a0e9f640f", label, 1, 1)
            + block(ph.GUID_CRIT_ACTION, label, *action)
            + block(ph.GUID_LOW_LEVEL, label, *low, levels=True)
            + block(ph.GUID_CRIT_LEVEL.upper(), label, *level, levels=True))


class Policy(unittest.TestCase):
    def test_current_values_are_the_last_two_hex_of_each_setting_block(self):
        pol = ph.parse_policy(text(action=(3, 2), level=(7, 5), low=(12, 10)))
        self.assertEqual(pol, {"action": {"ac": 3, "dc": 2}, "level": {"ac": 7, "dc": 5}, "lowLevel": {"ac": 12, "dc": 10}})

    def test_labels_do_not_matter_only_guids_and_hex(self):
        self.assertEqual(ph.parse_policy(text(label="معرّف إعداد الطاقة"))["action"], {"ac": 2, "dc": 2})

    def test_missing_setting_is_none_and_empty_text_is_safe(self):
        self.assertEqual(ph.parse_policy(""), {"action": None, "level": None, "lowLevel": None})
        self.assertIsNone(ph.parse_policy("x " + ph.GUID_CRIT_ACTION + " only one 0x00000002")["action"])

    def test_risk_matrix(self):
        r = lambda act, hib: ph.risk({"action": {"ac": 3, "dc": act}}, hib)
        self.assertEqual(r(0, True), "none")
        self.assertEqual(r(2, False), "hibernate_off")
        self.assertIsNone(r(2, True))
        self.assertIsNone(r(2, None))           # حالة السبات غير مقروءة: لا نتّهم
        self.assertEqual(r(1, True), "sleep")
        self.assertIsNone(r(3, False))
        self.assertIsNone(ph.risk({"action": None}, False))

    def test_policy_payload_and_failure(self):
        with patch.object(ph, "_query_policy", lambda: text(action=(3, 2))), patch.object(ph, "_hibernate_enabled", lambda: False):
            p = ph.policy()
        self.assertEqual((p["ok"], p["action"], p["actionName"], p["actionAc"], p["level"], p["lowLevel"], p["hibernate"], p["risk"]),
                         (True, 2, "سبات", 3, 5, 10, False, "hibernate_off"))
        self.assertIn("السبات معطّل", p["riskText"])
        with patch.object(ph, "_query_policy", lambda: ""):
            self.assertEqual(ph.policy(), {"ok": False})


class Cause(unittest.TestCase):
    def test_crash_wins_over_battery(self):
        self.assertEqual(ph.cause("عطل", (1, 0, 2), (2, 1, 1))[0], "crash")

    def test_battery_by_reading_after_boot(self):
        self.assertEqual(ph.cause("انقطاع", (1, 1, 80), (2, 1, 3)), ("battery", "فرغت البطارية"))
        self.assertEqual(ph.cause("انقطاع", None, (2, 1, 1))[0], "battery")

    def test_battery_by_last_reading_on_battery(self):
        self.assertEqual(ph.cause("انقطاع", (1, 0, 7), (2, 1, 9))[0], "battery")      # حالة حقيقية: 7% ثم 9% بعد دقائق شحن
        self.assertEqual(ph.cause("انقطاع", (1, 0, 10), None)[0], "battery")
        self.assertEqual(ph.cause("انقطاع", (1, 0, 11), None)[0], "unknown")
        self.assertEqual(ph.cause("انقطاع", (1, 1, 7), (2, 1, 40))[0], "unknown")     # كان على الشاحن: القراءة المنخفضة ليست دليلًا
        self.assertEqual(ph.cause("انقطاع", (1, 0, 8), (2, 1, 60))[0], "unknown")     # شُحنت وهو مطفأ: لا دليل على الفراغ

    def test_sleep_and_button_and_battery_during_sleep(self):
        self.assertEqual(ph.cause("سكون", (1, 0, 54), (2, 0, 49))[0], "sleep")
        self.assertEqual(ph.cause("سكون", (1, 0, 5), (2, 1, 1)), ("battery", "فرغت البطارية أثناء السكون"))
        self.assertEqual(ph.cause("زر", (1, 1, 90), (2, 1, 90))[0], "button")
        self.assertEqual(ph.cause("انقطاع", None, None)[0], "unknown")


class Join(unittest.TestCase):
    def test_newest_first_with_readings_and_counts(self):
        before = {100: (90, 0, 4), 200: (150, 1, 70)}
        after = {100: (101, 1, 1), 200: (205, 0, 66)}
        out = ph.join([(100, "انقطاع"), (200, "سكون")], before.get, after.get)
        self.assertEqual([i["ts"] for i in out["items"]], [200, 100])
        self.assertEqual(out["counts"], {"sleep": 1, "battery": 1})
        self.assertEqual(out["items"][1], {"ts": 100, "kind": "انقطاع", "cause": "battery", "text": "فرغت البطارية", "lastSeen": 90,
                                          "battBefore": 4, "acBefore": False, "battAfter": 1})
        self.assertEqual((out["items"][0]["acBefore"], out["days"]), (True, ph.DAYS))

    def test_no_events(self):
        self.assertEqual(ph.join([], lambda t: None, lambda t: None), {"days": ph.DAYS, "items": [], "counts": {}})


class Shutdowns(unittest.TestCase):
    NOW = 1_794_000_000

    def setUp(self):
        CONN.executescript("DROP TABLE IF EXISTS events; DROP TABLE IF EXISTS power_samples;"
                           "CREATE TABLE events(ts INTEGER, source TEXT, severity TEXT, title TEXT, detail TEXT);")

    def ev(self, ts, title, source="boot"):
        CONN.execute("INSERT INTO events VALUES(?,?,?,?,?)", (ts, source, "warn", title, ""))

    def test_reads_only_unclean_boot_events_in_window_and_joins_samples(self):
        n = self.NOW
        CONN.execute("CREATE TABLE power_samples(ts INTEGER PRIMARY KEY, ac INTEGER, batt INTEGER, mhz INTEGER, mhz_max INTEGER, mhz_limit INTEGER)")
        self.ev(n - 3600, bootwatch.TITLE % "انقطاع")
        self.ev(n - 7200, "إقلاع الجهاز")                               # حدث الإقلاع العادي من الفحص: ليس حادثة
        self.ev(n - 9000, bootwatch.TITLE % "سكون", source="cpu")        # مصدر آخر
        self.ev(n - (ph.DAYS + 1) * 86400, bootwatch.TITLE % "عطل")      # خارج النافذة
        CONN.executemany("INSERT INTO power_samples VALUES(?,?,?,0,0,0)", [
            (n - 3600 - 900, 0, 6), (n - 3600 - 600, 0, None),          # قراءة بلا نسبة تُتخطّى
            (n - 3600 + 120, 1, 2), (n - 3600 + 180, 1, 3)])
        out = ph.shutdowns(now=n)
        self.assertEqual(len(out["items"]), 1)
        it = out["items"][0]
        self.assertEqual((it["ts"], it["kind"], it["cause"], it["battBefore"], it["acBefore"], it["lastSeen"], it["battAfter"]),
                         (n - 3600, "انقطاع", "battery", 6, False, n - 3600 - 900, 2))

    def test_samples_outside_their_windows_are_ignored(self):
        n = self.NOW
        CONN.execute("CREATE TABLE power_samples(ts INTEGER PRIMARY KEY, ac INTEGER, batt INTEGER, mhz INTEGER, mhz_max INTEGER, mhz_limit INTEGER)")
        self.ev(n - 3600, bootwatch.TITLE % "انقطاع")
        CONN.executemany("INSERT INTO power_samples VALUES(?,?,?,0,0,0)", [
            (n - 3600 - ph.BEFORE_WINDOW - 60, 0, 2), (n - 3600 + ph.AFTER_WINDOW + 60, 1, 1)])
        it = ph.shutdowns(now=n)["items"][0]
        self.assertEqual((it["cause"], it["battBefore"], it["battAfter"]), ("unknown", None, None))

    def test_missing_samples_table_still_lists_events(self):
        self.ev(self.NOW - 60, bootwatch.TITLE % "سكون")
        out = ph.shutdowns(now=self.NOW)
        self.assertEqual((out["counts"], out["items"][0]["battBefore"]), ({"sleep": 1}, None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
