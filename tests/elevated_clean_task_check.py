"""wintemp / swdist / DISM run only through their fixed elevated tasks: the server process never deletes in system folders
nor runs dism /Cleanup-Image itself, the owner's switch refuses before any process is started, and the right task is triggered."""
import ast
import os
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]


def extracted(setting=None):
    src = (ROOT / "space.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    names = {"_task_ready", "get_elevated", "set_elevated", "elevated_state", "elevated_gate", "run_fixed_task", "elevated_clean", "_winold_task_ready"}
    body = [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") in ("ELEVATED", "_etask", "_wtask") for t in n.targets)]
    body += [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    store = {"space.elevated": setting}
    ns = {"os": os, "time": __import__("time"), "json": __import__("json"),
          "_setting": lambda k, d=None: store.get(k, d), "_setting_set": lambda k, v: store.__setitem__(k, v)}
    exec(compile(ast.Module(body=body, type_ignores=[]), "isolated-space", "exec"), ns)
    ns["_store"] = store
    return ns


SECURE = types.SimpleNamespace(installed=lambda: True, WINOLD_TASKS={"normal": "W", "early": "WE"},
                               CLEAN_TASKS={"wintemp": "MonitorWidget-Clean-WinTemp", "swdist": "MonitorWidget-Clean-SwDist", "dism": "MonitorWidget-Clean-Dism"},
                               clean_result=lambda k: str(ROOT / "tests" / ("no_such_%s.json" % k)))


class ElevatedCleanTasks(unittest.TestCase):
    def test_server_source_never_runs_the_elevated_work_itself(self):
        for f in ("space.py", "extras.py"):
            src = (ROOT / f).read_text(encoding="utf-8-sig")
            self.assertNotIn("/StartComponentCleanup", src, f)
            self.assertNotIn("_purge_dir(SWDIST)", src, f)
        ps = (ROOT / "elevated_clean.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("ValidateSet('wintemp', 'swdist', 'dism')", ps)
        self.assertNotIn("ResetBase", ps.replace("لا /ResetBase", ""))
        self.assertIn("ReparsePoint", ps)

    def test_installer_registers_one_fixed_task_per_kind(self):
        inst = (ROOT / "install_secure_2026-10-24.ps1").read_text(encoding="utf-8-sig")
        for t in ("MonitorWidget-Clean-WinTemp", "MonitorWidget-Clean-SwDist", "MonitorWidget-Clean-Dism"):
            self.assertIn(t, inst)
        self.assertIn("elevated_clean.ps1' = '", inst)
        self.assertIn("-Kind $($k[0]) -Result", inst)

    def test_default_switches_are_on_and_persist(self):
        ns = extracted()
        self.assertEqual(ns["get_elevated"](), {"wintemp": True, "swdist": True, "dism": True, "winold": True, "dwm": True})
        self.assertTrue(ns["set_elevated"]("dism", False))
        self.assertFalse(ns["set_elevated"]("edgecache", False))
        self.assertFalse(ns["get_elevated"]()["dism"])
        self.assertTrue(ns["get_elevated"]()["wintemp"])

    def test_switched_off_kind_is_refused_before_any_process(self):
        ns = extracted({"wintemp": False})
        calls = []
        ns["secure"] = SECURE
        ns["subprocess"] = types.SimpleNamespace(run=lambda a, **k: calls.append(a), TimeoutExpired=Exception)
        r = ns["elevated_clean"]("wintemp")
        self.assertFalse(r["ok"])
        self.assertTrue(r.get("disabled"))
        self.assertEqual(calls, [])

    def test_missing_task_is_reported_without_running(self):
        ns = extracted()
        calls = []
        ns["secure"] = SECURE
        ns["subprocess"] = types.SimpleNamespace(run=lambda a, **k: (calls.append(a) or types.SimpleNamespace(returncode=1)), TimeoutExpired=Exception)
        r = ns["elevated_clean"]("swdist")
        self.assertFalse(r["ok"])
        self.assertIn("MonitorWidget-Clean-SwDist", r["error"])
        self.assertEqual(calls, [["schtasks", "/query", "/tn", "MonitorWidget-Clean-SwDist"]])

    def test_ready_kind_triggers_exactly_its_task(self):
        ns = extracted()
        calls = []
        ns["secure"] = SECURE
        ns["subprocess"] = types.SimpleNamespace(run=lambda a, **k: (calls.append(a) or types.SimpleNamespace(returncode=0)), TimeoutExpired=Exception)
        r = ns["elevated_clean"]("wintemp", timeout_s=0)
        self.assertFalse(r["ok"])
        self.assertIn("مهلة", r["error"])
        self.assertEqual(calls, [["schtasks", "/query", "/tn", "MonitorWidget-Clean-WinTemp"], ["schtasks", "/run", "/tn", "MonitorWidget-Clean-WinTemp"]])

    def test_extras_routes_admin_kinds_through_space(self):
        src = (ROOT / "extras.py").read_text(encoding="utf-8-sig")
        self.assertIn("space.elevated_clean(kind)", src)
        self.assertIn('space.elevated_gate("dism")', src)
        self.assertIn('CLEAN_TASKS["dism"]', src)
        self.assertNotIn("not space._is_admin():\n        return {\"ok\": False, \"error\": \"يتطلب تشغيل الخادم بصلاحية المسؤول\"}\n    if blk", src)


if __name__ == "__main__":
    unittest.main()
