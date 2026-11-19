"""إغلاق عملية بعينها (procclose): السياسة بلقطة وهمية وهوية محقونة، وإغلاق حيّ لعملية ابن بلا نافذة، وعقد POST /api/proc-close في
المسارات الهدّامة والواجهة. معزول: لا خادم ولا قاعدة بيانات ولا استيراد للتطبيق. لا يُنهي إلا عمليات ينشئها هو.

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
import commitinfo  # noqa: E402
import procclose   # noqa: E402

ROWS = {4: "System", 100: "llama-server.exe", 200: "explorer.exe", 300: "pythonw.exe", 400: "pwsh.exe", 500: "nvcontainer.exe",
        600: "elevated-tool.exe", 700: "other-user.exe"}
OWNER = "S-1-5-21-111-222-333-1001"
ME = (OWNER, 1)
IDENTS = {100: ("133000000000000100", OWNER, 1), 400: ("133000000000000400", OWNER, 1),
          500: ("133000000000000500", "S-1-5-18", 0),          # خدمة SYSTEM ليست في قائمة الأسماء
          600: ("133000000000000600", OWNER, 1),               # عملية المالك المرتفعة: له إغلاقها
          700: ("133000000000000700", "S-1-5-21-111-222-333-1002", 2)}
CREATED = {pid: v[0] for pid, v in IDENTS.items()}
CREATE_NO_WINDOW = 0x08000000


def chk(pid, name, created=None, rows=ROWS, idents=IDENTS, mine=ME):
    return procclose.check(pid, name, CREATED.get(pid, "1") if created is None else created, rows, idents.get, mine)


class PolicyTests(unittest.TestCase):
    def test_bad_params(self):
        for pid, name in (("100", "a.exe"), (True, "a.exe"), (0, "a.exe"), (4, "System"), (100, ""), (100, "   "), (100, None), (100, "x" * 261)):
            self.assertEqual(chk(pid, name), (False, "bad params"), (pid, name))
        for created in (None, 133000000000000100, "", "0", "abc", "-5", "1" * 21, "١٢٣", " 133000000000000100"):
            self.assertEqual(procclose.check(100, "llama-server.exe", created, ROWS, IDENTS.get, ME), (False, "bad params"), created)

    def test_protected(self):
        self.assertEqual(chk(200, "explorer.exe"), (False, "protected"))
        self.assertEqual(chk(300, "pythonw.exe"), (False, "protected"))      # خادم المرصد ووكيله
        self.assertEqual(chk(300, "PythonW.EXE"), (False, "protected"))      # بلا حساسية لحالة الأحرف
        self.assertEqual(chk(os.getpid(), "python.exe", rows={os.getpid(): "python.exe"}), (False, "protected"))   # العملية نفسها
        self.assertTrue(all(n == n.lower() for n in procclose.DENY_NAMES))

    def test_not_found_and_mismatch(self):
        self.assertEqual(chk(999, "x.exe"), (False, "not_found"))
        self.assertEqual(chk(100, "claude.exe"), (False, "name_mismatch"))   # المعرّف أُعيد استعماله باسم آخر
        self.assertEqual(chk(100, "LLAMA-SERVER.EXE"), (True, "ok"))
        self.assertEqual(chk(400, " pwsh.exe "), (True, "ok"))             # الشلات مسموحة بطلب صريح

    def test_created_mismatch_when_pid_reused_with_same_name(self):
        self.assertEqual(chk(100, "llama-server.exe", "133000000000000099"), (False, "created_mismatch"))
        self.assertEqual(chk(100, "llama-server.exe", idents={}), (False, "access_denied"))   # لا تُفتح حتى للاستعلام
        self.assertEqual(chk(100, "llama-server.exe", idents={100: (None, OWNER, 1)}), (False, "access_denied"))

    def test_only_interactive_owner_processes(self):
        self.assertEqual(chk(500, "nvcontainer.exe"), (False, "not_owner"))   # SYSTEM في جلسة الخدمات
        self.assertEqual(chk(700, "other-user.exe"), (False, "not_owner"))    # مستخدم آخر
        self.assertEqual(chk(600, "elevated-tool.exe"), (True, "ok"))         # المالك نفسه ولو مرتفعة
        self.assertEqual(chk(100, "llama-server.exe", mine=("S-1-5-18", 1)), (False, "not_owner"))   # خادم بحساب خدمة: لا مستخدم تفاعلي
        self.assertEqual(chk(100, "llama-server.exe", mine=(None, 1)), (False, "not_owner"))

    def test_owner_reason_pure(self):
        r = procclose.owner_reason
        self.assertIsNone(r(OWNER, 1, ME))
        for sid, session in ((OWNER, 0), (OWNER, 2), ("S-1-5-19", 1), ("S-1-5-20", 1), ("S-1-5-90-0-1", 1), (None, 1), (OWNER, None)):
            self.assertEqual(r(sid, session, ME), "not_owner", (sid, session))
        self.assertEqual(r(OWNER, 0, (OWNER, 0)), "not_owner")   # جلسة الخدمات 0 مرفوضة دائمًا

    def test_close_refuses_before_touching_anything(self):
        self.assertEqual(procclose.close(200, "explorer.exe", "1", rows=ROWS), {"ok": False, "error": "protected", "pid": 200, "name": "explorer.exe"})
        self.assertEqual(procclose.close(999, "x.exe", "1", rows=ROWS)["error"], "not_found")
        self.assertEqual(procclose.close(500, "nvcontainer.exe", CREATED[500], rows=ROWS, probe=IDENTS.get, mine=ME)["error"], "not_owner")
        self.assertEqual(procclose.close(os.getpid(), "python.exe", "1")["error"], "protected")


class LiveTests(unittest.TestCase):
    """إغلاق حيّ لعملية ابن بلا نافذة ينشئها الاختبار نفسه (يعمل على عدّاء CI)."""

    def test_names_by_pid_has_self(self):
        rows = procclose.names_by_pid()
        self.assertTrue(rows.get(os.getpid(), "").lower().startswith("python"))

    def test_identity_of_self_is_the_interactive_owner(self):
        sid, session = procclose.me()
        self.assertTrue(sid and sid.startswith("S-1-5-"), sid)
        self.assertIsNotNone(session)
        ident = procclose.identity(os.getpid())
        self.assertIsNotNone(ident)
        self.assertTrue(ident[0].isdigit())
        self.assertEqual((ident[1], ident[2]), (sid, session))
        self.assertIsNone(procclose.owner_reason(ident[1], ident[2], (sid, session)))

    def test_system_process_is_refused_when_readable(self):
        """عملية SYSTEM حقيقية (winlogon/lsass غير مسموحة بالاسم أصلًا؛ نبحث عن أي عملية مالكها SYSTEM ونفحص السياسة فقط، بلا إنهاء)."""
        mine = procclose.me()
        for pid, name in procclose.names_by_pid().items():
            if pid <= 4 or name.lower() in procclose.DENY_NAMES:
                continue
            ident = procclose.identity(pid)
            if ident and ident[1] == "S-1-5-18" and ident[0]:
                self.assertEqual(procclose.check(pid, name, ident[0], mine=mine), (False, "not_owner"), (pid, name))
                return
        self.skipTest("لا عملية SYSTEM مقروءة بهذه الصلاحية")

    def test_commit_rows_carry_created(self):
        rows, _ = commitinfo.scan()
        own = next(r for r in rows if r["pid"] == os.getpid())
        self.assertEqual(own["created"], procclose.identity(os.getpid())[0])
        top = commitinfo.rank([own], 1)[0]
        self.assertEqual(top["created"], own["created"])

    def test_close_child_without_window(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], creationflags=CREATE_NO_WINDOW)
        try:
            time.sleep(0.5)
            name = procclose.names_by_pid().get(p.pid)
            self.assertIsNotNone(name)
            created = procclose.identity(p.pid)[0]
            stale = str(int(created) - 1)
            r = procclose.close(p.pid, name, stale)   # وقت إنشاء غير مطابق: كأن المعرّف أُعيد استعماله
            self.assertEqual(r["error"], "created_mismatch", r)
            self.assertIsNone(p.poll())               # لم تُمسّ
            r = procclose.close(p.pid, name, created)
            self.assertEqual((r["ok"], r["exited"], r["method"], r["windows"]), (True, True, "terminate", 0), r)
            self.assertIsInstance(r["commitMB"], int)
            self.assertIsNotNone(p.wait(5))
            self.assertIn(procclose.check(p.pid, name, created)[1], ("not_found", "name_mismatch", "created_mismatch", "access_denied"))
        finally:
            if p.poll() is None:
                p.kill()


class ContractTests(unittest.TestCase):
    def test_route_in_destructive_module(self):
        src = (ROOT / "routes_destructive.py").read_text(encoding="utf-8-sig")
        self.assertIn('"/api/proc-close"', src.split("ROUTES = ", 1)[1].split("\n", 1)[0])   # ضمن قائمة المسارات الهدّامة (تأكيد 428)
        self.assertIn("import procclose", src)
        self.assertIn("procclose.check(pid, name, created)", src)
        self.assertIn("procclose.close(pid, name, created, force=force)", src)
        self.assertIn('"procclose"', src)   # يُسجَّل في جدول actions

    def test_ui(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("data-procclose=", html)
        self.assertIn('data-proccreated="${esc(p.created || "")}"', html)
        self.assertIn('"/api/proc-close"', html)
        self.assertIn("{ pid, name, created, force: false }", html)
        self.assertIn("force: true", html)   # الإنهاء القسري بتأكيد ثانٍ فقط
        self.assertIn("{ pid, name, created, force: true }", html)
        for key in ("protected", "not_found", "name_mismatch", "created_mismatch", "not_owner", "access_denied", '"bad params"'):
            self.assertIn(key, html)


if __name__ == "__main__":
    unittest.main()
