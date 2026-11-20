"""مراقب الويدجت (widgetwatch): قراءة widget.log، ومتى يُعاد تشغيل الويدجت ومتى لا، والحدّ الأقصى، وحمولة صفحة الصحة.
معزول: لا ويدجت حقيقي ولا مهمة مجدولة ولا قاعدة حية (التشغيل وحالة العملية والسجل محقونة).

Run: python -X utf8 -B tests/widgetwatch_check.py
"""
import os
import sqlite3
import sys
import threading
import time
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
sys.modules["widgetctl"] = types.SimpleNamespace(widget_running=lambda: False, widget_start=lambda: None)
import widgetwatch as ww

NOW = time.mktime(time.strptime("2026-11-12T18:00:00", "%Y-%m-%dT%H:%M:%S"))
START = "2026-11-12T17:00:00 start: pid 100, shell ready after 2.1 s, work area {X=0,Y=0,Width=2560,Height=1540}"
FATAL = '2026-11-12T17:10:00 fatal: Exception calling "Run" with "1" argument(s): "Cannot find an overload for "op_Subtraction""'


class Parse(unittest.TestCase):
    def test_counts_and_thousands_separator_in_stall(self):
        st = ww.parse([START, "2026-11-12T17:05:00 layout: wide 726 (work 1,232, natural 1,230, dpi 120)",
                       "2026-11-12T17:06:00 ui stall: 5.3 s between ticks", "2026-11-12T17:07:00 ui stall: 2,852.2 s between ticks",
                       "2026-11-12T17:08:00 trim: pressed deep", "+     [void]$app.Run($win)"], NOW)
        self.assertEqual((st["starts"], st["stalls"], st["stallMaxS"], st["stallSumS"], st["fatals"]), (1, 2, 2852.2, 2857.5, 0))
        self.assertEqual((st["state"], st["pid"]), ("running-or-killed", 100))
        self.assertIn("wide 726", st["lastLayout"]["text"])

    def test_states(self):
        self.assertEqual(ww.parse([], NOW)["state"], "never")
        self.assertEqual(ww.parse([START, FATAL, "2026-11-12T17:10:00 exit"], NOW)["state"], "crashed")
        self.assertEqual(ww.parse([START, "2026-11-12T17:20:00 exit: user"], NOW)["state"], "closed")
        self.assertEqual(ww.parse([START, "2026-11-12T17:20:00 exit"], NOW)["state"], "exited")
        # بدء جديد يمحو حالة ما قبله
        self.assertEqual(ww.parse([START, FATAL, "2026-11-12T17:10:00 exit", START.replace("17:00", "17:30")], NOW)["state"], "running-or-killed")

    def test_old_lines_are_outside_the_24h_counts_but_still_set_state(self):
        old = [l.replace("2026-11-12", "2026-11-10") for l in (START, "2026-11-12T17:06:00 ui stall: 9.0 s between ticks", FATAL, "2026-11-12T17:10:00 exit")]
        st = ww.parse(old, NOW)
        self.assertEqual((st["starts"], st["stalls"], st["fatals"], st["state"]), (0, 0, 0, "crashed"))


class Guard(unittest.TestCase):
    def setUp(self):
        EVENTS.clear()
        CONN.execute("DELETE FROM meta")
        self.launched = []

    def guard(self, lines, running=False, now=NOW):
        return ww.guard(now=now, running=lambda: running, lines=lambda: lines, launch=lambda: self.launched.append(1) or "task")

    def test_running_widget_is_left_alone(self):
        self.assertEqual(self.guard([START, FATAL], running=True), "up")
        self.assertEqual(self.launched, [])

    def test_relaunches_after_crash_kill_and_unrequested_exit_with_an_event(self):
        for lines, word in (([START, FATAL, "2026-11-12T17:10:00 exit"], "سقوطه"), ([START], "توقف عمليته"), ([START, "2026-11-12T17:20:00 exit"], "لم يطلبه")):
            self.setUp()
            self.assertTrue(self.guard(lines).startswith("relaunched via task"), lines)
            self.assertEqual(len(self.launched), 1)
            self.assertIn(word, EVENTS[0][3])

    def test_never_relaunches_what_the_owner_closed_or_hid_or_never_started(self):
        self.assertEqual(self.guard([START, "2026-11-12T17:20:00 exit: user"]), "down: closed cleanly")
        self.assertEqual(self.guard([]), "down: never started")
        ww.set_wanted(False)
        self.assertEqual(self.guard([START, FATAL]), "down: hidden from the app")
        ww.set_wanted(True)
        self.assertTrue(self.guard([START, FATAL]).startswith("relaunched"))
        self.assertEqual(len(self.launched), 1)

    def test_a_start_after_hiding_means_the_owner_brought_it_back(self):
        hidden_at = NOW - 1800                                  # أُخفي 17:30، والبدء في السجل 17:00: ما زال مخفيًّا
        ww.set_wanted(False, now=hidden_at)
        self.assertEqual(self.guard([START, FATAL]), "down: hidden from the app")
        self.assertFalse(ww.payload(now=NOW, running=lambda: False, lines=lambda: [START])["wanted"])
        later = START.replace("17:00:00", "17:40:00")           # المالك شغّله 17:40 من اختصاره ثم سقط
        self.assertTrue(ww.payload(now=NOW, running=lambda: True, lines=lambda: [later])["wanted"])
        self.assertTrue(self.guard([later, FATAL.replace("17:10", "17:45")]).startswith("relaunched"))

    def test_legacy_hidden_flag_is_cleared_once_the_widget_is_seen_running(self):
        ww._meta_set("widget_wanted", False)
        self.assertEqual(self.guard([START, FATAL]), "down: hidden from the app")
        self.assertEqual(self.guard([START], running=True), "up")
        self.assertTrue(self.guard([START, FATAL]).startswith("relaunched"))

    def test_waits_while_a_restart_is_in_flight(self):
        self.assertEqual(self.guard([START.replace("17:00:00", "17:59:50")]), "down: settling")
        self.assertEqual(self.launched, [])

    def test_stops_after_three_relaunches_in_an_hour_then_recovers(self):
        lines = [START, FATAL, "2026-11-12T17:10:00 exit"]
        results = [self.guard(lines, now=NOW + 60 * i) for i in range(5)]
        self.assertEqual([r.split(" ")[0] for r in results], ["relaunched"] * 3 + ["down:", "down:"])
        self.assertEqual(results[3], "down: relaunch limit")
        self.assertEqual(len(self.launched), 3)
        self.assertTrue(self.guard(lines, now=NOW + 3700).startswith("relaunched"))


class Payload(unittest.TestCase):
    def setUp(self):
        CONN.execute("DELETE FROM meta")

    def test_payload_for_health_page(self):
        lines = [START, "2026-11-12T17:06:00 ui stall: 9.0 s between ticks", FATAL, "2026-11-12T17:10:00 exit"]
        p = ww.payload(now=NOW, running=lambda: False, lines=lambda: lines)
        self.assertEqual((p["running"], p["state"], p["fatals"], p["stalls"], p["relaunches"], p["wanted"]), (False, "crashed", 1, 1, 0, True))
        self.assertIn("op_Subtraction", p["lastFatal"]["text"])
        p = ww.payload(now=NOW, running=lambda: True, lines=lambda: lines)
        self.assertEqual((p["running"], p["state"]), (True, "running"))
        p = ww.payload(now=NOW, running=lambda: False, lines=lambda: [START])
        self.assertEqual(p["state"], "killed")

    def test_read_tail_missing_file_is_empty(self):
        self.assertEqual(ww.read_tail(os.path.join(ROOT, "no-such-widget.log")), [])


class StallCauses(unittest.TestCase):
    """البند 36 من المجلس 19/11: كل توقف واجهة يُربط بما قيس في نافذته، وإلا فهو داخلي."""
    def setUp(self):
        c = self.c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE job_runs(id INTEGER PRIMARY KEY, ts INTEGER, name TEXT, ok INTEGER, ms INTEGER, detail TEXT)")
        c.execute("CREATE TABLE io_min(ts INTEGER, dev TEXT, busy_max REAL, lat_r_max REAL, lat_w_max REAL)")
        c.execute("CREATE TABLE io_stalls(id INTEGER PRIMARY KEY, ts_start INTEGER, ts_end INTEGER)")
        c.execute("CREATE TABLE cpu_top(ts INTEGER, name TEXT, cores REAL, n INTEGER)")
        c.execute("CREATE TABLE samples(ts INTEGER, source TEXT, commit_pct REAL)")
        self.end = int(NOW) - 600

    def kinds(self, sec=10):
        return [x["kind"] for x in ww.stall_causes([(self.end, sec)], self.c)[0]["causes"]]

    def test_nothing_measured_means_internal(self):
        self.c.execute("INSERT INTO io_min VALUES(?, '_total', 40, 3, 5)", (self.end - 60,))
        self.c.execute("INSERT INTO cpu_top VALUES(?, '__total__', 7.3, 20)", (self.end,))
        self.c.execute("INSERT INTO samples VALUES(?, 'live', 60)", (self.end,))
        self.assertEqual(self.kinds(), ["internal"])

    def test_overlapping_heavy_job_disk_cpu_and_memory(self):
        self.c.execute("INSERT INTO job_runs(ts,name,ok,ms) VALUES(?, 'scan_big', 1, 339000)", (self.end + 200,))   # بدأ قبل التوقف وانتهى بعده
        self.c.execute("INSERT INTO job_runs(ts,name,ok,ms) VALUES(?, 'live', 1, 6000)", (self.end - 3600,))         # قبله بساعة: لا
        self.c.execute("INSERT INTO io_min VALUES(?, '_total', 97, 2, 250)", (self.end - 60,))
        self.c.execute("INSERT INTO cpu_top VALUES(?, '__total__', 19, 20)", (self.end - 5,))
        self.c.execute("INSERT INTO cpu_top VALUES(?, 'pytest.exe', 15, 20)", (self.end - 5,))
        self.c.execute("INSERT INTO samples VALUES(?, 'live', 95)", (self.end - 3,))
        got = ww.stall_causes([(self.end, 10)], self.c)[0]["causes"]
        self.assertEqual([x["kind"] for x in got], ["job", "disk", "cpu", "memory"])
        self.assertIn("scan_big", got[0]["text"]); self.assertIn("pytest.exe", got[2]["text"])

    def test_disk_stall_record_counts_when_minutes_look_quiet(self):
        self.c.execute("INSERT INTO io_stalls(ts_start, ts_end) VALUES(?, ?)", (self.end - 8, self.end - 2))
        self.assertEqual(self.kinds(), ["disk"])

    def test_missing_tables_are_not_an_error_and_sleep_gaps_are_skipped(self):
        bare = sqlite3.connect(":memory:")
        self.assertEqual([x["kind"] for x in ww.stall_causes([(self.end, 9)], bare)[0]["causes"]], ["internal"])
        self.assertEqual(ww.stall_causes([(self.end, 5074.1)], bare), [])

    def test_newest_first_and_capped(self):
        st = [(self.end + i, 6.0) for i in range(12)]
        out = ww.stall_causes(st, self.c)
        self.assertEqual(len(out), ww.STALL_CAUSES_MAX); self.assertEqual(out[0]["ts"], self.end + 11)

    def test_parse_lists_recent_stalls_and_payload_carries_causes(self):
        lines = [START, "2026-11-12T17:06:00 ui stall: 5.3 s between ticks"]
        self.assertEqual(ww.parse(lines, NOW)["stallList"], [(ww._ts("2026-11-12T17:06:00"), 5.3)])
        p = ww.payload(now=NOW, running=lambda: True, lines=lambda: lines, causes=lambda st: [{"ts": st[0][0], "s": st[0][1], "causes": []}])
        self.assertEqual(p["stallCauses"][0]["s"], 5.3)


if __name__ == "__main__":
    unittest.main(verbosity=1)
