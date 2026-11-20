"""صحة المرصد وميزانيته (البندان 21 و22 من المجلس 19/11): الدوال النقية، والتعرّف الحي على عملية ابن، وعقد الواجهات.
Run: python -X utf8 -B tests/selfwatch_check.py"""
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import selfwatch as sw

NOW = 1_800_000_000


class Collectors(unittest.TestCase):
    def job(self, hb=10, **kw):
        j = {"name": "j", "interval_s": 60, "timeout_s": 600, "last_start": NOW - 30, "last_end": NOW - 29, "last_status": "ok", "next_run": NOW + 30}
        j.update(kw)
        rows = sw.collectors([j], NOW, hb)
        return rows[0] if rows else None

    def test_fresh_is_ok(self):
        self.assertEqual(self.job()["level"], "ok")

    def test_postponed_by_agent_restart_is_ok(self):
        # الوكيل أعيد تشغيله فأجّل الجامع: آخر تشغيل قبل 4 ساعات وموعده التالي في المستقبل (quarantine_purge 20/11)
        self.assertEqual(self.job(interval_s=3600, last_start=NOW - 15500, last_end=NOW - 15500, next_run=NOW + 1400)["level"], "ok")

    def test_overdue_by_slack_then_bad(self):
        self.assertEqual(self.job(next_run=NOW - 700)["level"], "ok")    # ضمن 2×60+600
        self.assertEqual(self.job(next_run=NOW - 800)["level"], "warn")
        r = self.job(next_run=NOW - 9000)
        self.assertEqual(r["level"], "bad")
        self.assertIn("ساعة", r["why"])

    def test_orphan_row_skipped_only_while_agent_alive(self):
        # trimwatch أُزيل من الوكيل وبقي صفّه: موعده متأخر 11 يومًا
        self.assertIsNone(self.job(next_run=NOW - 11 * 86400))
        self.assertEqual(self.job(hb=None, next_run=NOW - 11 * 86400)["level"], "bad")    # لا نبضة: كل تأخر يُعدّ
        self.assertEqual(self.job(hb=3600, next_run=NOW - 2 * 86400)["level"], "bad")    # وكيل متوقف

    def test_stuck_and_error(self):
        r = self.job(last_start=NOW - 1000, last_end=NOW - 2000, next_run=NOW - 2 * 86400)   # العالق لا يُتجاوز يتيمًا
        self.assertEqual((r["level"], r["why"][:4]), ("bad", "عالق"))
        self.assertEqual(self.job(last_status="error")["level"], "warn")

    def test_never_ran(self):
        self.assertEqual(self.job(last_start=None, last_end=None, next_run=NOW + 10)["level"], "ok")    # لم يحن موعده
        self.assertEqual(self.job(last_start=None, last_end=None, next_run=NOW - 3600)["level"], "warn")


class Swallowed(unittest.TestCase):
    LINES = ("2026-11-19 02:00:49,765 WARNING swallowed exception:", "2026-11-19 02:00:50,001 INFO agent start",
             "2026-11-20 01:18:39,473 WARNING swallowed exception:", "2026-11-20 01:18:40,000 swallowed exception:",   # errlog بلا مستوى
             "Traceback (most recent call last):", "  swallowed exception: داخل نص الاستثناء لا يُعدّ")

    def test_counts_only_heads_of_the_day(self):
        self.assertEqual(sw.count_swallowed(self.LINES, "2026-11-19"), 1)
        self.assertEqual(sw.count_swallowed(self.LINES, "2026-11-20"), 2)
        self.assertEqual(sw.count_swallowed(self.LINES, "2026-11-21"), 0)

    def test_log_lines_reads_rotation_and_caches(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "server.log.1"), "w", encoding="utf-8") as f:
                f.write(self.LINES[0] + "\nTraceback\n")
            with open(os.path.join(d, "server.log"), "w", encoding="utf-8") as f:
                f.write("\n".join(self.LINES[1:]) + "\n")
            lines = sw._log_lines(d)
            self.assertEqual(len(lines), 3)
            self.assertEqual(sw.count_swallowed(sw._log_lines(d), "2026-11-20"), 2)


class Budget(unittest.TestCase):
    def test_within_budget(self):
        b = sw.budget({"server": [{"pid": 1, "mb": 30, "handles": 300, "cpu100ns": 0}]})
        self.assertEqual((b["level"], b["over"], b["totalMB"]), ("ok", [], 30))
        self.assertIsNone(b["roles"][0]["cores"])

    def test_memory_children_handles_and_cpu(self):
        widget = [{"pid": 10, "mb": 380, "handles": 6000, "cpu100ns": 2 * 10**7}] + [{"pid": 100 + i, "mb": 2, "handles": 50, "cpu100ns": 0} for i in range(26)]
        b = sw.budget({"widget": widget}, prev={10: 0}, dt=10.0)
        r = b["roles"][0]
        self.assertEqual((r["level"], r["children"], r["cores"]), ("warn", 26, 0.2))
        for word in ("ذاكرته", "مقبضًا", "فرعية", "معالجه"):
            self.assertIn(word, r["why"])
        self.assertTrue(b["over"][0].startswith("الويدجت: "))

    def test_double_budget_is_bad_and_total_cap(self):
        b = sw.budget({"widget": [{"pid": 1, "mb": 900, "handles": 1, "cpu100ns": 0}], "shell": [{"pid": 2, "mb": 600, "handles": 1, "cpu100ns": 0}]})
        self.assertEqual(b["level"], "bad")
        self.assertTrue(any("مجموع" in o for o in b["over"]))

    def test_new_pid_since_prev_counts_no_cpu(self):
        b = sw.budget({"agent": [{"pid": 7, "mb": 10, "handles": 1, "cpu100ns": 5 * 10**7}]}, prev={}, dt=60.0)
        self.assertEqual(b["roles"][0]["cores"], 0.0)


class Summary(unittest.TestCase):
    def test_ok_and_worst_first(self):
        ok = [{"level": "ok", "text": "a"}]
        self.assertEqual(sw.summarize(ok, None), ("ok", "المرصد سليم"))
        items = ok + [{"level": "warn", "text": "تحذير"}, {"level": "bad", "text": "خلل"}]
        lvl, text = sw.summarize(items, {"level": "warn", "over": ["الويدجت: ذاكرته"]})
        self.assertEqual(lvl, "bad")
        self.assertTrue(text.startswith("المرصد: خلل؛ تحذير"))

    def test_budget_only(self):
        self.assertEqual(sw.summarize([], {"level": "warn", "over": ["الوكيل: x"]}), ("warn", "المرصد: الوكيل: x"))


class Note(unittest.TestCase):
    def test_event_after_streak_once_per_window(self):
        events = []
        sys.modules["db"] = type(sys)("db")
        sys.modules["db"].add_event = lambda *a: events.append(a)
        try:
            sw._state.update(streak=0, lastEvent=0.0)
            over = {"over": ["الويدجت: ذاكرته 500 MB"]}
            for i in range(sw.OVER_STREAK + 2):
                sw._note(over, NOW + i)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0][1:3], ("self", "warn"))
            sw._note({"over": []}, NOW + 10)
            self.assertEqual(sw._state["streak"], 0)
        finally:
            del sys.modules["db"]


class LiveRoles(unittest.TestCase):
    """عملية ابن سطر أوامرها يحوي <مجلد>\\agent.py تُعرف وكيلًا، وابنها يُعدّ من نسله."""

    def test_child_detected_with_descendant(self):
        with tempfile.TemporaryDirectory() as d:
            agent = os.path.join(d, "agent.py")
            with open(agent, "w", encoding="utf-8") as f:
                f.write("import subprocess, sys, time\np = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\ntime.sleep(30)\n")
            p = subprocess.Popen([sys.executable, agent], creationflags=0x08000000)
            try:
                deadline = time.time() + 10
                roles = {}
                while time.time() < deadline:
                    roles = sw._roles(app_dir=d, me=-1)
                    if len(roles.get("agent", [])) >= 2:
                        break
                    time.sleep(0.3)
                pids = {r["pid"] for r in roles.get("agent", [])}
                self.assertIn(p.pid, pids)
                self.assertGreaterEqual(len(pids), 2)
                self.assertTrue(all(r["mb"] > 0 and r["handles"] > 0 for r in roles["agent"]))
                self.assertNotIn("widget", roles)
            finally:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, check=False, creationflags=0x08000000)
                p.wait(5)


class Contract(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(ROOT, name), encoding="utf-8-sig") as f:
            return f.read()

    def test_wired(self):
        self.assertIn('"self": selfwatch.mini()', self.read("snapshot.py"))
        self.assertIn('out["self"] = selfwatch.view()', self.read("routes_read.py"))
        w = self.read("widget.ps1")
        self.assertIn("$sh = $d.self", w)
        self.assertIn("$UI.selfAlert", w.split("foreach($n in @($hdr", 1)[1].split("\n", 1)[0])
        self.assertIn('panel("صحة المرصد"', self.read(os.path.join("web", "index.html")))
        self.assertIn("selfwatch_check.py", self.read(os.path.join("tests", "run_all.py")))


if __name__ == "__main__":
    unittest.main()
