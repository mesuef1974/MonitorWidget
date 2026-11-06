"""حارس النوافذ: أصناف المهام، والإجراء الصامت، والسياسة، وعدّ فتحات Docker، ورصد النوافذ الجديدة.

Run: python -X utf8 -B tests/winguard_check.py
"""
import calendar
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import winguard   # noqa: E402

PYW = r"C:\Py\pythonw.exe"


def task(**kw):
    return {"name": "T", "path": "\\", "actions": 1, "exe": PYW, "args": "x.py", "logon": "Interactive", **kw}


class Classify(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(winguard.classify(task()), ("children", True))
        self.assertEqual(winguard.classify(task(exe="powershell.exe", args="-WindowStyle Hidden -File a.ps1")), ("flash", True))
        self.assertEqual(winguard.classify(task(exe="cmd.exe")), ("visible", True))

    def test_silent(self):
        self.assertEqual(winguard.classify(task(exe=winguard.CONHOST, args='--headless "p.exe"')), ("silent", False))
        self.assertEqual(winguard.classify(task(logon="S4U")), ("silent", False))
        self.assertEqual(winguard.classify(task(name="MonitorWidget-Agent")), ("silent", False))
        self.assertEqual(winguard.classify(task(name="Cleanup_v2_Daily", exe="pwsh.exe")), ("silent", False))
        self.assertEqual(winguard.classify(task(exe="wscript.exe")), ("silent", False))

    def test_many_actions_not_fixable(self):
        self.assertEqual(winguard.classify(task(actions=2)), ("children", False))


class Wrap(unittest.TestCase):
    def test_pythonw_becomes_python(self):
        exe, args = winguard.wrap(PYW, '"D:\\a b\\x.py" --go')
        self.assertEqual(exe, winguard.CONHOST)
        self.assertEqual(args, '--headless "C:\\Py\\python.exe" "D:\\a b\\x.py" --go')

    def test_keeps_other_exe_and_empty_args(self):
        self.assertEqual(winguard.wrap("pwsh.exe", ""), (winguard.CONHOST, '--headless "pwsh.exe"'))


class Policy(unittest.TestCase):
    ROWS = ({"name": "A", "fixable": True}, {"name": "B", "fixable": False})

    def test_check(self):
        self.assertEqual(winguard.check("A", self.ROWS), (True, "ok"))
        self.assertEqual(winguard.check("B", self.ROWS), (False, "not_fixable"))
        self.assertEqual(winguard.check("C", self.ROWS), (False, "not_found"))
        for bad in (None, "", " ", 5, "x" * 201):
            self.assertEqual(winguard.check(bad, self.ROWS), (False, "bad params"))

    def test_script_stops_on_failure_and_handles_empty_workdir(self):
        self.assertIn("$ErrorActionPreference = 'Stop'", winguard.SILENCE_PS)
        self.assertIn("if ($wd) { $p.WorkingDirectory = $wd }", winguard.SILENCE_PS)
        self.assertIn("verify failed", winguard.SILENCE_PS)

    def test_silence_passes_values_by_env_and_reports_denied(self):
        rows = [{"name": "A", "path": "\\", "exe": PYW, "args": "x.py", "fixable": True}]
        seen = {}

        class P:
            returncode, stdout, stderr = 1, "", "Access is denied."

        def fake(script, env=None, timeout=60):
            seen.update(env)
            return P

        with patch.object(winguard, "_run_ps", fake):
            res = winguard.silence("A", rows)
        self.assertEqual(res["error"], "needs_admin")
        self.assertEqual(seen["WG_NAME"], "A")
        self.assertEqual(seen["WG_EXE"], winguard.CONHOST)
        self.assertIn("python.exe", seen["WG_ARGS"])

    def test_silence_refuses_before_running(self):
        with patch.object(winguard, "_run_ps", side_effect=AssertionError("must not run")):
            self.assertEqual(winguard.silence("B", self.ROWS)["error"], "not_fixable")


class Docker(unittest.TestCase):
    def test_counts_last_day_only(self):
        now = calendar.timegm((2026, 11, 6, 12, 0, 0))
        mark = "[com.docker.backend.exe.electron] show-dashboard flag detected; opening dashboard\n"
        body = ("[2026-11-06T03:42:00.440871700Z]" + mark + "[2026-11-04T03:42:00.440871700Z]" + mark
                + "[2026-11-06T03:43:00.000000000Z][x] other\n")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.log")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(body)
            r = winguard.docker_opens(p, now)
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["last"], calendar.timegm((2026, 11, 6, 3, 42, 0)))

    def test_no_log(self):
        self.assertIsNone(winguard.docker_opens(os.path.join(tempfile.gettempdir(), "no_such_log_x.log")))


class Watch(unittest.TestCase):
    def test_only_new_windows_recorded(self):
        winguard.WINDOWS.clear()
        seen = winguard.note_new({1}, {1: ("c", "old"), 2: ("c", "git.exe")}, time.time())
        self.assertEqual(seen, {1, 2})
        self.assertEqual([w["title"] for w in winguard.WINDOWS], ["git.exe"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
