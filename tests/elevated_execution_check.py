"""الكانس من خادم مرتفع: لا يُشغَّل سكربت مجلد المستخدم أبدًا بصلاحيته؛ تُطلق مهمة ثابتة محدودة الصلاحية فقط."""
import ast
import ctypes
import os
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

TASKS = {"dry": "MonitorWidget-Reaper-Dry", "apply": "MonitorWidget-Reaper-Apply"}


def _load(run, lines_after_run, pin="pinned-ok"):
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "insight.py").read_text(encoding="utf-8-sig"))
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("reaper_run", "_reaper_lines", "_reaper_wait")]
    fake_os = types.SimpleNamespace(path=types.SimpleNamespace(join=os.path.join, exists=lambda _: True, isfile=lambda _: True,
                                                               getsize=lambda _: 0))
    log = []

    def fake_run(args, **k):
        run.append(args)
        log.extend(lines_after_run)
        return types.SimpleNamespace(returncode=0 if args[0] == "schtasks" and args[-1] in TASKS.values() else 1)
    db = types.SimpleNamespace(_lock=threading.Lock(), conn=lambda: types.SimpleNamespace(execute=lambda *a: None, commit=lambda: None))
    ns = {"os": fake_os, "re": __import__("re"), "time": types.SimpleNamespace(time=lambda: 0, sleep=lambda _: None), "db": db,
          "secure": types.SimpleNamespace(REAPER_TASKS=TASKS, reaper_pin_state=lambda _: (pin, "")),
          "_run_lock": threading.Lock(), "CNW": 0, "errlog": types.SimpleNamespace(swallowed=lambda: None),
          "subprocess": types.SimpleNamespace(run=fake_run)}
    code = compile(ast.Module(body=fns, type_ignores=[]), "isolated-reaper", "exec")
    exec(code, ns)  # noqa: S102 — يعزل الدوال عن واردات insight الثقيلة؛ المصدر ملف المستودع نفسه
    ns["_reaper_lines"] = lambda logp, pos: list(log)
    ns["_reaper_wait"] = lambda logp, pos, t: list(log)
    return ns


class ElevatedExecution(unittest.TestCase):
    def test_elevated_triggers_fixed_limited_task_never_the_script(self):
        for dry, task in ((True, TASKS["dry"]), (False, TASKS["apply"])):
            run = []
            ns = _load(run, ["[Reaper] INFO done: mode=X candidates=2 killed=1"])
            with patch.object(ctypes.windll.shell32, "IsUserAnAdmin", return_value=1):
                r = ns["reaper_run"](dry, "user-writable-folder")
            self.assertEqual(run, [["schtasks", "/run", "/tn", task]])
            self.assertTrue(r["ok"])
            self.assertEqual((r["candidates"], r["killed"]), (2, 1))
            self.assertFalse(ns["_run_lock"].locked())

    def test_pin_mismatch_blocks_before_any_execution(self):
        run = []
        ns = _load(run, [], pin="pinned-mismatch")
        with patch.object(ctypes.windll.shell32, "IsUserAnAdmin", return_value=1):
            r = ns["reaper_run"](True, "user-writable-folder")
        self.assertFalse(r["ok"])
        self.assertEqual(run, [])

    def test_no_done_line_is_failure(self):
        ns = _load([], ["[Reaper] INFO WOULD-KILL x"])
        with patch.object(ctypes.windll.shell32, "IsUserAnAdmin", return_value=1):
            r = ns["reaper_run"](True, "user-writable-folder")
        self.assertFalse(r["ok"])
        self.assertIn("90", r["error"])

    def test_installer_registers_both_tasks_limited(self):
        src = (Path(__file__).resolve().parents[1] / "build_secure_install.py").read_text(encoding="utf-8-sig")
        block = src[src.index("# 4e."):src.index("# 5.")]
        self.assertIn("-RunLevel Limited", block)
        self.assertNotIn("Highest", block)
        for t in TASKS.values():
            self.assertIn(t, block)
        self.assertIn("--headless", block)


if __name__ == "__main__":
    unittest.main()
