"""Windows.old removal goes through the fixed elevated task only: no takeown/icacls/rmdir from the server process."""
import ast
import os
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]


def extracted():
    src = (ROOT / "space.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    names = {"_winold_task_ready", "_winold_delete", "winold_status"}
    body = [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("_wtask", "_wo", "WINOLD", "WINOLD_DAYS") for t in n.targets)]
    body += [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"os": os, "time": __import__("time"), "json": __import__("json"), "threading": __import__("threading"),
          "errlog": types.SimpleNamespace(swallowed=lambda: None), "db": types.SimpleNamespace(_lock=__import__("threading").RLock()),
          "_is_reparse": lambda p: False, "_c": lambda: None, "scan": lambda *a: None}
    exec(compile(ast.Module(body=body, type_ignores=[]), "isolated-space", "exec"), ns)
    ns["_wtask"]["t"] = 0.0
    return ns


class WinOldTask(unittest.TestCase):
    def test_server_source_never_calls_takeown_or_rmdir(self):
        src = (ROOT / "space.py").read_text(encoding="utf-8-sig")
        for word in ('"takeown"', '"icacls"', '"rmdir"'):
            self.assertNotIn(word, src)

    def test_missing_protected_install_refuses_without_running_anything(self):
        ns = extracted()
        calls = []
        ns["secure"] = types.SimpleNamespace(installed=lambda: False, WINOLD_TASKS={"normal": "T", "early": "TE"}, WINOLD_RESULT="r.json")
        ns["subprocess"] = types.SimpleNamespace(run=lambda *a, **k: calls.append(a), TimeoutExpired=Exception)
        ok, why = ns["_winold_task_ready"]()
        self.assertFalse(ok); self.assertIn("التثبيت المحمي", why); self.assertEqual(calls, [])

    def test_missing_task_is_reported_after_one_query(self):
        ns = extracted()
        calls = []
        ns["secure"] = types.SimpleNamespace(installed=lambda: True, WINOLD_TASKS={"normal": "MonitorWidget-WinOld", "early": "TE"}, WINOLD_RESULT="r.json")
        ns["subprocess"] = types.SimpleNamespace(run=lambda a, **k: (calls.append(a) or types.SimpleNamespace(returncode=1)), TimeoutExpired=Exception)
        ok, why = ns["_winold_task_ready"]()
        self.assertFalse(ok); self.assertIn("MonitorWidget-WinOld", why)
        self.assertEqual(calls, [["schtasks", "/query", "/tn", "MonitorWidget-WinOld"]])

    def test_early_override_uses_the_early_task(self):
        ns = extracted()
        calls = []
        ns["secure"] = types.SimpleNamespace(installed=lambda: True, WINOLD_TASKS={"normal": "N", "early": "E"}, WINOLD_RESULT=str(ROOT / "tests" / "no_such_result.json"))
        ns["subprocess"] = types.SimpleNamespace(run=lambda a, **k: (calls.append(a) or types.SimpleNamespace(returncode=1)), TimeoutExpired=Exception)
        ns["winold_status"] = lambda: {"present": True, "eligible": False}
        ns["_winold_delete"]("manual-early", early=True)
        self.assertEqual(calls, [["schtasks", "/run", "/tn", "E"]])
        self.assertEqual(ns["_wo"]["job"]["state"], "error")

    def test_ineligible_without_early_is_refused_before_any_task(self):
        ns = extracted()
        calls = []
        ns["secure"] = types.SimpleNamespace(installed=lambda: True, WINOLD_TASKS={"normal": "N", "early": "E"}, WINOLD_RESULT="r.json")
        ns["subprocess"] = types.SimpleNamespace(run=lambda a, **k: calls.append(a), TimeoutExpired=Exception)
        ns["winold_status"] = lambda: {"present": True, "eligible": False}
        ns["_winold_delete"]("manual")
        self.assertEqual(calls, []); self.assertEqual(ns["_wo"]["job"]["state"], "refused")


if __name__ == "__main__":
    unittest.main()
