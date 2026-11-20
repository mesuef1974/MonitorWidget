"""مهام التحديث (updwatch، البند 23 من المجلس 19/11): أنماط المهام، التقييم (مفقودة/معطّلة كلها = warn، بعضها = partial،
البرنامج غير المثبَّت لا بند له)، قراءة Settings/Enabled من XML المهمة، التخطّي بلا صلاحية، التنبيه عند تغيّر المجموعة
وحده، ورفض startup_mgr تعطيل مهمة تحديث مع السماح بتفعيلها.

Run: python -X utf8 -B tests/updwatch_check.py
"""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import updwatch as uw

EDGE = r"\MicrosoftEdgeUpdateTaskMachineCore"
EDGE_UA = r"\MicrosoftEdgeUpdateTaskMachineUA"
TVSU = r"\TVT\TVSUUpdateTask"
TVSU_LOGON = r"\TVT\TVSUUpdateTask_UserLogOn"
VANT = r"\Lenovo\Vantage\Lenovo.Vantage.ServiceMaintainance"


def _installed(*keys):
    paths = {p for u in uw.UPDATERS if u["key"] in keys for p in u["product"]}
    return lambda p: p in paths


class Patterns(unittest.TestCase):
    def test_known_update_tasks_match(self):
        for p in (EDGE, EDGE_UA, TVSU, TVSU_LOGON, VANT, r"\Lenovo\Vantage\Schedule\LenovoSystemUpdateAddin_WeeklyTask",
                  r"\GoogleUpdateTaskMachineCore{1}", r"\Microsoft\Office\Office Automatic Updates 2.0"):
            self.assertTrue(uw.is_updater(p), p)

    def test_unrelated_tasks_do_not_match(self):
        for p in (r"\Lenovo\Lenovo Service Bridge\S-1-5-21", r"\Lenovo\Vantage\Schedule\DailyTelemetryTransmission",
                  r"\MicrosoftEdgeUpdateBrowserReplacementTask", r"\Maestro-Digest-Loop", "", None):
            self.assertFalse(uw.is_updater(p), p)


class Evaluate(unittest.TestCase):
    def test_missing_and_all_disabled_warn_partial_and_ok(self):
        state = {EDGE: False, EDGE_UA: False, TVSU: True, TVSU_LOGON: False, VANT: True}
        items = {i["key"]: i for i in uw.evaluate(list(state), exists=_installed("edge", "lenovo-su", "vantage", "office"),
                                                   enabled=state.get)}
        self.assertEqual(items["edge"]["level"], "warn")
        self.assertEqual(items["lenovo-su"]["level"], "partial")
        self.assertIn("TVSUUpdateTask_UserLogOn", items["lenovo-su"]["text"])
        self.assertEqual(items["vantage"]["level"], "ok")
        self.assertEqual(items["office"]["level"], "warn")      # مثبَّت بلا مهمة
        self.assertEqual(items["office"]["tasks"], [])
        self.assertNotIn("chrome", items)                       # غير مثبَّت: لا بند

    def test_unreadable_task_is_not_counted_as_disabled(self):
        items = uw.evaluate([EDGE], exists=_installed("edge"), enabled=lambda p: None)
        self.assertEqual(items[0]["level"], "ok")


class TaskXml(unittest.TestCase):
    def _write(self, d, name, body):
        with open(os.path.join(d, name), "w", encoding="utf-16") as f:
            f.write('<?xml version="1.0" encoding="UTF-16"?><Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
                    f"<Settings>{body}</Settings></Task>")

    def test_enabled_element_and_default(self):
        with tempfile.TemporaryDirectory() as d, patch.object(uw, "TASKS_DIR", d):
            self._write(d, "off", "<Enabled>false</Enabled>")
            self._write(d, "on", "<Enabled>true</Enabled>")
            self._write(d, "dflt", "<Hidden>true</Hidden>")
            self.assertEqual((uw.task_enabled(r"\off"), uw.task_enabled(r"\on"), uw.task_enabled(r"\dflt")), (False, True, True))
            self.assertIsNone(uw.task_enabled(r"\missing"))


class RunOnce(unittest.TestCase):
    def setUp(self):
        uw._LAST_BAD["v"] = None
        self.events = []
        sys.modules["db"] = types.SimpleNamespace(add_event=lambda *a: self.events.append(a))

    def tearDown(self):
        sys.modules.pop("db", None)

    def test_no_admin_is_skipped_without_alert(self):
        with patch.object(uw, "task_paths", side_effect=PermissionError):
            st = uw.run_once()
        self.assertEqual((st["state"], st["items"], self.events), ("skipped", [], []))

    def test_alert_only_when_set_changes(self):
        bad = [{"key": "edge", "label": "E", "level": "warn", "text": "t", "tasks": []}]
        with patch.object(uw, "task_paths", return_value=[]), patch.object(uw, "evaluate", return_value=bad):
            self.assertEqual(uw.run_once()["state"], "warn")
            uw.run_once()
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][1:3], ("updates", "warn"))
        with patch.object(uw, "task_paths", return_value=[]), patch.object(uw, "evaluate", return_value=[]):
            self.assertEqual(uw.run_once()["state"], "ok")
        with patch.object(uw, "task_paths", return_value=[]), patch.object(uw, "evaluate", return_value=bad):
            uw.run_once()
        self.assertEqual(len(self.events), 2)     # عاد التعطيل بعد إصلاحه: تنبيه جديد


class StartupGuard(unittest.TestCase):
    def test_disabling_update_task_is_refused_enabling_allowed(self):
        import startup_mgr as sm
        item = {"id": "task|" + TVSU_LOGON, "source": "task", "name": "TVSUUpdateTask_UserLogOn", "path": "\\TVT\\",
                "enabled": False, "protected": False, "writable": True}
        with patch.object(sm, "list_items", return_value={"items": [item]}), \
             patch.object(sm.subprocess, "run") as run:
            self.assertEqual(sm.toggle({"id": item["id"], "enabled": False}), (400, {"error": "update task"}))
            run.assert_not_called()
            run.return_value = types.SimpleNamespace(returncode=0)
            with patch.object(sm, "_elevated_unsafe", return_value=False), patch.object(sm.db, "conn"), \
                 patch.object(sm.db, "_lock"):
                code, _ = sm.toggle({"id": item["id"], "enabled": True})
            self.assertEqual(code, 200)
            self.assertIn("Enable-ScheduledTask", run.call_args[0][0][-1])


if __name__ == "__main__":
    unittest.main(verbosity=1)
