"""النشر المحمي (deploy_protected.ps1، مجلس 19/11/2026): عقود السكربت التي تمنع الارتقاء من مستخدم عادي إلى مسؤول،
وتبديل سياسة التنظيف إلى نسختها المحمية، وتعديل XML المهمة دون مسّ مشغّلاتها.
معزول: لا يشغّل المثبّت ولا يلمس المهام ولا ProgramData؛ دالة XML تُستخرج وتُشغَّل في pwsh على تعريف اصطناعي.

Run: python -X utf8 -B tests/protected_deploy_check.py
"""
import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
with open(os.path.join(ROOT, "deploy_protected.ps1"), encoding="utf-8-sig") as fh:
    SCRIPT = fh.read()


def _function(name):
    m = re.search(r"^function %s[ ({].*?^\}" % re.escape(name), SCRIPT, re.S | re.M)
    assert m, name
    return m.group(0)


class Contracts(unittest.TestCase):
    def test_only_merged_code_from_the_commit(self):
        self.assertIn("'merge-base', '--is-ancestor', $commit, 'origin/main'", SCRIPT)
        self.assertIn("'archive', '--format=zip'", SCRIPT)   # من الالتزام لا من الشجرة التي يكتبها المستخدم

    def test_isolated_signed_interpreter(self):
        body = _function("Install-Python")
        self.assertIn("Python Software Foundation", body)
        self.assertIn("'Lib\\site-packages'", body)
        self.assertIn("Set-PythonPath $tmp", body)   # يُتحقق من sys.path فعليًا قبل التبديل
        pth = _function("Set-PythonPath")
        # ._pth يعطّل site وPYTHON* ويحصر sys.path؛ app أولًا لأن مجلد السكربت لا يُضاف فتحجب Lib وحدة sched.py
        self.assertIn("@('..\\app', 'Lib', 'DLLs')", pth)
        self.assertIn("المفسّر المحمي يبحث خارج المجلد المحمي", pth)
        self.assertIn("أول sys.path في المفسّر المحمي ليس app", pth)
        self.assertIn("Set-PythonPath $py", SCRIPT)   # كل تشغيل، ولو تُخطّي نسخ المفسّر

    def test_full_token_is_locked_before_it_holds_a_value(self):
        body = _function("Write-Secret")
        empty = body.index("WriteAllText($Path, '')")
        lock = body.index("Set-AdminOnly $Path")
        value = body.index("WriteAllText($Path, $value")
        self.assertLess(empty, lock)
        self.assertLess(lock, value)
        acl = _function("Set-AdminOnly")
        self.assertIn("SetAccessRuleProtection($true, $false)", acl)   # بلا وراثة من مجلد يقرؤه المستخدمون
        self.assertNotIn("S-1-5-32-545", acl)
        self.assertIn("Write-Secret $full -AdminOnly", _function("Set-Tokens"))
        self.assertIn("Write-Secret $read |", _function("Set-Tokens"))   # رمز القراءة وحده يرث قراءة المستخدمين

    def test_old_tokens_and_data_leave_the_user_folder(self):
        self.assertIn("foreach ($n in '.token', '.token.old')", _function("Set-Tokens"))
        move = _function("Move-Data")
        self.assertIn("check-ignore", move)   # يُنقل المتجاهَل وحده؛ ملفات المالك غير المتتبَّعة تُترك
        self.assertIn("Reset-Tree $app", move)
        self.assertIn("/setowner '*S-1-5-32-544'", _function("Reset-Tree"))   # المالك يغيّر صلاحيات ملفه، فلا يبقى مالكًا

    def test_policy_and_tasks(self):
        self.assertIn("-PolicyPath \"' + $protPolicy", _function("Set-PolicyTasks"))
        tasks = _function("Set-BackendTasks")
        self.assertIn("Disable-ScheduledTask -TaskName 'MonitorWidget-Collector'", tasks)
        self.assertIn("'.protected-launch'", tasks)
        self.assertIn("Remove-Item -LiteralPath (Join-Path $app '.protected-launch')", _function("Install-Code"))   # لا حلقة في app

    def test_verification_rejects_read_token_on_post(self):
        body = _function("Start-AndVerify")
        self.assertIn("StatusCode -eq 401", body)
        self.assertIn("الخادم المستمع ليس من المسار المحمي", body)

    def test_result_is_written_in_the_protected_folder(self):
        self.assertIn("$resultFile = Join-Path $state 'last_result.txt'", SCRIPT)
        self.assertIn("function Invoke-Rollback", SCRIPT)


class QuarantinePaths(unittest.TestCase):
    def test_stored_paths_follow_the_moved_quarantine(self):
        # المخزن يرفض qpath خارج جذره؛ بعد النقل يجب أن يتبع كل مسار مخزَّن الجذر الجديد (بلا حساسية لحالة الأحرف)
        self.assertIn("Update-QuarantinePaths (Join-Path $app 'monitor.db')", _function("Move-Data"))
        self.assertIn("Update-QuarantinePaths (Join-Path $src 'monitor.db')", _function("Invoke-Rollback"))
        code = re.search(r"\$code = @'\n(.*?)\n'@", SCRIPT.replace("\r\n", "\n"), re.S).group(1)
        import sqlite3
        d = tempfile.mkdtemp(prefix="mw_deploy_q_")
        try:
            fix, db = os.path.join(d, "fix.py"), os.path.join(d, "m.db")
            with open(fix, "w", encoding="utf-8") as fh:
                fh.write(code)
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE quarantine(id INTEGER PRIMARY KEY, qpath TEXT)")
            c.executemany("INSERT INTO quarantine(qpath) VALUES(?)", [(r"C:\Src\MW\quarantine\op1\a.txt",),
                          (r"c:\src\mw\QUARANTINE\op2\b",), (r"C:\Src\MW\quarantine-other\x",), (r"D:\else\y",)])
            c.commit()
            c.close()
            out = subprocess.run([sys.executable, fix, db, "C:\\Src\\MW\\quarantine", "C:\\PD\\app\\quarantine\\"],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip(), "2")
            rows = [r[0] for r in sqlite3.connect(db).execute("SELECT qpath FROM quarantine ORDER BY id")]
            self.assertEqual(rows, [r"C:\PD\app\quarantine\op1\a.txt", r"C:\PD\app\quarantine\op2\b",
                                    r"C:\Src\MW\quarantine-other\x", r"D:\else\y"])   # بادئ مشابه لا يُمسّ
        finally:
            shutil.rmtree(d, ignore_errors=True)


class PolicyPath(unittest.TestCase):
    def tearDown(self):
        import paths
        importlib.reload(paths)

    def test_protected_deployment_reads_the_protected_policy(self):
        import paths
        import secure
        with patch.object(secure, "protected_app_ready", lambda: True):
            importlib.reload(paths)
            self.assertTrue(paths.PROTECTED)
            self.assertEqual(paths.POLICY, paths.PROT_POLICY)
            self.assertTrue(paths.POLICY.lower().startswith(secure.SECURE_DIR.lower()))
        with patch.object(secure, "protected_app_ready", lambda: False):
            importlib.reload(paths)
            self.assertEqual(paths.POLICY, paths.USER_POLICY)


@unittest.skipUnless(shutil.which("pwsh"), "pwsh غير متاح")
class TaskXml(unittest.TestCase):
    XML = ('<?xml version="1.0" encoding="UTF-16"?><Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
           '<Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>'
           '<Principals><Principal id="Author"><RunLevel>HighestAvailable</RunLevel></Principal></Principals>'
           '<Actions Context="Author"><Exec><Command>C:\\Users\\u\\py\\pythonw.exe</Command>'
           '<Arguments>"C:\\Users\\u\\Documents\\agent.py"</Arguments></Exec></Actions></Task>')

    def test_exec_is_replaced_and_the_rest_is_kept(self):
        d = tempfile.mkdtemp(prefix="mw_deploy_xml_")
        try:
            xml = os.path.join(d, "t.xml")
            with open(xml, "w", encoding="utf-8") as fh:
                fh.write(self.XML)
            ps = os.path.join(d, "run.ps1")
            with open(ps, "w", encoding="utf-8-sig") as fh:
                fh.write(_function("Set-TaskExecXml") + "\n"
                         "$x = Set-TaskExecXml (Get-Content -Raw -LiteralPath '%s') 'C:\\ProgramData\\MonitorWidget\\python\\pythonw.exe' "
                         "'\"C:\\ProgramData\\MonitorWidget\\app\\agent.py\"' 'C:\\ProgramData\\MonitorWidget\\app'\n$x\n" % xml)
            out = subprocess.run(["pwsh", "-NoProfile", "-File", ps], capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stderr)
            o = out.stdout
            self.assertIn("<Command>C:\\ProgramData\\MonitorWidget\\python\\pythonw.exe</Command>", o)
            self.assertIn("<WorkingDirectory>C:\\ProgramData\\MonitorWidget\\app</WorkingDirectory>", o)
            self.assertIn("ProgramData\\MonitorWidget\\app\\agent.py", o)
            self.assertNotIn("Documents", o)
            self.assertIn("<LogonTrigger>", o)
            self.assertIn("<RunLevel>HighestAvailable</RunLevel>", o)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
