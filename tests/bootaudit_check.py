"""فحص الإقلاع (bootaudit، البند 11 من المجلس 19/11): تحليل ناتج PowerShell وحدوده، عدّ المخالفات (المهمة المعطّلة لا تُعدّ)،
التخطّي بلا صلاحية مسؤول، والتنبيه الحرج مع الحالة؛ ثم دوال PowerShell المشتركة على ملفات حقيقية: ملف يملكه المستخدم مخالف،
ومكتبة النظام لا؛ وstartup_mgr يبني على الدوال نفسها (لا نسخة ثانية).

Run: python -X utf8 -B tests/bootaudit_check.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import bootaudit as ba

PWSH = shutil.which("pwsh") or shutil.which("powershell")


def _reset():
    ba._STATE.update(state="pending", ts=None, ms=None, checked=0, tasks=[], procs=[], files=[], error=None)


class ParseAndCount(unittest.TestCase):
    def test_single_objects_and_nulls_become_lists(self):
        raw = json.dumps({"tasks": {"name": "T", "path": "\\", "state": "Ready", "bad": "C:\\u\\x.ps1"},
                          "procs": None, "files": "C:\\p\\a.dll", "checked": 7})
        tasks, procs, files, checked = ba.parse(raw)
        self.assertEqual(tasks, [{"name": "T", "path": "\\", "state": "Ready", "bad": ["C:\\u\\x.ps1"]}])
        self.assertEqual((procs, files, checked), ([], ["C:\\p\\a.dll"], 7))

    def test_lists_are_capped(self):
        raw = json.dumps({"files": [f"f{i}" for i in range(500)], "tasks": [], "procs": [
            {"pid": 4, "name": "x", "unreadable": False, "bad": [f"m{i}" for i in range(500)]}]})
        _, procs, files, _ = ba.parse(raw)
        self.assertEqual((len(files), len(procs[0]["bad"])), (ba.MAX_LIST, ba.MAX_LIST))

    def test_non_object_is_rejected(self):
        for raw in ("null", "[]", "5"):
            with self.assertRaises(TypeError):
                ba.parse(raw)
        with self.assertRaises(ValueError):
            ba.parse("not json")

    def test_disabled_task_and_unreadable_process_are_not_violations(self):
        st = {"tasks": [{"name": "A", "path": "\\", "state": "Disabled", "bad": ["x", "y"]},
                        {"name": "B", "path": "\\", "state": "Ready", "bad": ["z"]}],
              "procs": [{"pid": 1, "name": "p", "unreadable": True, "bad": []}, {"pid": 2, "name": "q", "unreadable": False, "bad": ["m"]}],
              "files": ["f"]}
        self.assertEqual(ba.violations(st), 3)
        self.assertNotIn("\\A", ba._summary(st))
        self.assertIn("\\B: z", ba._summary(st))


class RunOnce(unittest.TestCase):
    def setUp(self):
        _reset()
        self.events = []
        self.mods = {"secure": types.SimpleNamespace(is_admin=lambda: True, SECURE_DIR="C:\\S", BIN="C:\\S\\bin"),
                     "paths": types.SimpleNamespace(CNW=0, PWSH="pwsh", ROOT="C:\\S\\app"),
                     "infra": types.SimpleNamespace(log=types.SimpleNamespace(info=lambda *a: None, warning=lambda *a: None)),
                     "db": types.SimpleNamespace(add_event=lambda *a: self.events.append(a))}

    def _run(self, stdout=b"", exc=None, admin=True):
        self.mods["secure"].is_admin = lambda: admin

        def fake(*a, **k):
            if exc:
                raise exc
            return types.SimpleNamespace(stdout=stdout, returncode=0)
        with patch.dict(sys.modules, self.mods), patch.object(ba.subprocess, "run", fake):
            return ba.run_once()

    def test_not_admin_is_skipped_without_scanning(self):
        st = self._run(exc=AssertionError("must not run"), admin=False)
        self.assertEqual((st["state"], self.events), ("skipped", []))

    def test_clean_scan_is_ok_without_alert(self):
        st = self._run(json.dumps({"tasks": [], "procs": [], "files": [], "checked": 40}).encode())
        self.assertEqual((st["state"], st["checked"], self.events), ("ok", 40, []))
        self.assertEqual(ba.payload()["violations"], 0)

    def test_violation_raises_one_critical_audit_alert(self):
        out = {"tasks": [{"name": "Bk", "path": "\\", "state": "Ready", "bad": ["C:\\Users\\u\\b.ps1"]}], "procs": [], "files": [], "checked": 3}
        st = self._run(json.dumps(out).encode())
        self.assertEqual(st["state"], "bad")
        self.assertEqual(len(self.events), 1)
        _, source, sev, title, detail = self.events[0]
        self.assertEqual((source, sev), ("audit", "crit"))
        self.assertIn("(1)", title)
        self.assertIn("b.ps1", detail)

    def test_failures_are_reported_not_raised(self):
        for exc, out in ((subprocess.TimeoutExpired("pwsh", 1), b""), (None, b"garbage"), (None, b"")):
            st = self._run(out, exc)
            self.assertEqual(st["state"], "error", out)
        self.assertEqual(self.events, [])


@unittest.skipUnless(os.name == "nt" and PWSH, "Windows PowerShell checks")
class PowerShellFunctions(unittest.TestCase):
    def ps(self, body, **env):
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", ba.WRITABLE_PS + body],
                           env=dict(os.environ, **env), capture_output=True, timeout=60, check=False)
        return r.stdout.decode("utf-8", "replace").strip()

    def test_user_owned_file_is_unsafe_system_dll_is_not(self):
        d = tempfile.mkdtemp(prefix="mw_audit_")
        f = os.path.join(d, "x.dll")
        with open(f, "wb"):
            pass
        sysdll = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "kernel32.dll")
        out = self.ps("[string](Test-FileUnsafe $env:F) + ',' + [string](Test-FileUnsafe $env:S) + ',' + [string](Test-FileUnsafe $env:M)",
                      F=f, S=sysdll, M=os.path.join(d, "missing.dll"))
        self.assertEqual(out, "True,False,True")   # ما لا يُقرأ يُعدّ غير آمن

    def test_task_with_user_file_lists_it(self):
        d = tempfile.mkdtemp(prefix="mw_audit_")
        f = os.path.join(d, "job.ps1")
        with open(f, "w"):
            pass
        body = ("$t = [pscustomobject]@{ Actions = @([pscustomobject]@{ Execute = 'cmd.exe'; Arguments = ('/c \"' + $env:F + '\"') },"
                " [pscustomobject]@{ Execute = 'no-such-tool.exe'; Arguments = '' }) }; (@(Get-UnsafeFiles $t) -join '|') + '#' + (Get-Writable $t)")
        self.assertEqual(self.ps(body, F=f), f + "|?no-such-tool.exe#True")


class SharedWithStartup(unittest.TestCase):
    def test_startup_mgr_uses_the_shared_functions(self):
        with open(os.path.join(ROOT, "startup_mgr.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('TASK_PS = bootaudit.WRITABLE_PS + r"""', src)
        self.assertEqual(src.count("function Test-UserWritable"), 0)
        self.assertIn("Get-Writable $_", src)


if __name__ == "__main__":
    unittest.main(verbosity=1)
