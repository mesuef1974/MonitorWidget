"""إغلاق عملية بعينها (procclose): السياسة بلقطة وهمية، وإغلاق حيّ لعملية ابن بلا نافذة، وعقد POST /api/proc-close في المسارات
الهدّامة والواجهة. معزول: لا خادم ولا قاعدة بيانات ولا استيراد للتطبيق.

Run: python -X utf8 -B tests/procclose_check.py
"""
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import procclose   # noqa: E402

ROWS = {4: "System", 100: "llama-server.exe", 200: "explorer.exe", 300: "pythonw.exe", 400: "pwsh.exe"}
CREATE_NO_WINDOW = 0x08000000


class PolicyTests(unittest.TestCase):
    def test_bad_params(self):
        for pid, name in (("100", "a.exe"), (True, "a.exe"), (0, "a.exe"), (4, "System"), (100, ""), (100, "   "), (100, None), (100, "x" * 261)):
            self.assertEqual(procclose.check(pid, name, ROWS), (False, "bad params"), (pid, name))

    def test_protected(self):
        self.assertEqual(procclose.check(200, "explorer.exe", ROWS), (False, "protected"))
        self.assertEqual(procclose.check(300, "pythonw.exe", ROWS), (False, "protected"))      # خادم المرصد ووكيله
        self.assertEqual(procclose.check(300, "PythonW.EXE", ROWS), (False, "protected"))      # بلا حساسية لحالة الأحرف
        self.assertEqual(procclose.check(os.getpid(), "python.exe", {os.getpid(): "python.exe"}), (False, "protected"))   # العملية نفسها
        self.assertTrue(all(n == n.lower() for n in procclose.DENY_NAMES))

    def test_not_found_and_mismatch(self):
        self.assertEqual(procclose.check(999, "x.exe", ROWS), (False, "not_found"))
        self.assertEqual(procclose.check(100, "claude.exe", ROWS), (False, "name_mismatch"))   # المعرّف أُعيد استعماله
        self.assertEqual(procclose.check(100, "LLAMA-SERVER.EXE", ROWS), (True, "ok"))
        self.assertEqual(procclose.check(400, " pwsh.exe ", ROWS), (True, "ok"))             # الشلات مسموحة بطلب صريح

    def test_close_refuses_before_touching_anything(self):
        self.assertEqual(procclose.close(200, "explorer.exe", rows=ROWS), {"ok": False, "error": "protected", "pid": 200, "name": "explorer.exe"})
        self.assertEqual(procclose.close(999, "x.exe", rows=ROWS)["error"], "not_found")
        self.assertEqual(procclose.close(os.getpid(), "python.exe")["error"], "protected")


class LiveTests(unittest.TestCase):
    """إغلاق حيّ لعملية ابن بلا نافذة بصلاحية عادية (يعمل على عدّاء CI)."""

    def test_names_by_pid_has_self(self):
        rows = procclose.names_by_pid()
        self.assertTrue(rows.get(os.getpid(), "").lower().startswith("python"))

    def test_close_child_without_window(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], creationflags=CREATE_NO_WINDOW)
        try:
            time.sleep(0.5)
            name = procclose.names_by_pid().get(p.pid)
            self.assertIsNotNone(name)
            r = procclose.close(p.pid, name)
            self.assertEqual((r["ok"], r["exited"], r["method"], r["windows"]), (True, True, "terminate", 0), r)
            self.assertIsInstance(r["commitMB"], int)
            self.assertIsNotNone(p.wait(5))
            self.assertIn(procclose.check(p.pid, name)[1], ("not_found", "name_mismatch"))   # لم تعد موجودة (أو أُعيد استعمال المعرّف)
        finally:
            if p.poll() is None:
                p.kill()


class ContractTests(unittest.TestCase):
    def test_route_in_destructive_module(self):
        src = (ROOT / "routes_destructive.py").read_text(encoding="utf-8-sig")
        self.assertIn('"/api/proc-close"', src.split("ROUTES = ", 1)[1].split("\n", 1)[0])   # ضمن قائمة المسارات الهدّامة (تأكيد 428)
        self.assertIn("import procclose", src)
        self.assertIn("procclose.check(", src)
        self.assertIn("procclose.close(", src)
        self.assertIn('"procclose"', src)   # يُسجَّل في جدول actions

    def test_ui(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("data-procclose=", html)
        self.assertIn('"/api/proc-close"', html)
        for key in ("protected", "not_found", "name_mismatch", "access_denied", '"bad params"'):
            self.assertIn(key, html)
        self.assertIn("force: true", html)   # الإنهاء القسري بتأكيد ثانٍ فقط


if __name__ == "__main__":
    unittest.main()
