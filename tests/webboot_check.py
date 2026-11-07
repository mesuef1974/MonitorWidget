"""إقلاع الخادم (webboot، البند 6/ك): احتكار النسخة الواحدة بالمتكس، إطلاق الوكيل مرة واحدة، علامات boot/exit إلى ملف مستقل،
watchdog لا يُخرج إلا بمهلة خمول موجبة، وrun: إن كان الخادم يعمل أصلًا يفتح نافذة عند --open ثم يخرج 0؛ وإلا يربط المنفذ ويهيّئ
القاعدة والصحة ويطلق الخيوط الخلفية كلها ثم serve_forever ويسجّل توقفها.
معزول: كل وحدات التطبيق (db/health/infra/snapshot/shellwin/…) بدائل وهمية تُحقن قبل الاستيراد؛ ctypes وsubprocess وthreading مرقَّعة.

Run: python -X utf8 -B tests/webboot_check.py
"""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="mw_webboot_")


class Log:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


def fake(**attrs):
    return types.SimpleNamespace(**attrs)


LOG = Log()
FAKES = {
    "paths": fake(ROOT=TMP, CNW=0x08000000, PORT=47832),
    "infra": fake(LOG_DIR=TMP, log=LOG, integrity_cached=lambda *a: LOG.calls.append(("integrity_cached", a))),
    "db": fake(bootstrap=lambda: fake(execute=lambda q: fake(fetchone=lambda: (7,)))),
    "health": fake(init=lambda: LOG.calls.append(("health.init", ()))),
    "snapshot": fake(last_request=lambda: 0.0, refresh_extra=lambda: LOG.calls.append(("refresh_extra", ())),
                     sampler=lambda: None, refresher=lambda: None, ingester=lambda: None),
    "winguard": fake(watch=lambda: None),
    "localai": fake(watch=lambda: None),
    "startup_mgr": fake(list_items=lambda: None),
    "shellwin": fake(open_window=lambda token: LOG.calls.append(("open_window", (token,)))),
    "websession": fake(token=lambda: "synthetic-token"),
}
for name, module in FAKES.items():
    sys.modules[name] = module
import webboot


class Kernel:
    """kernel32 وهمي: CreateMutexW يعيد مقبضًا وGetLastError الرمز المضبوط؛ OpenMutexW يعيد ما ضُبط."""

    def __init__(self, last_error=0, open_mutex=0):
        self.last_error, self.open_mutex, self.created = last_error, open_mutex, []

    def CreateMutexW(self, attrs, initial, name):
        self.created.append(name)
        return 1234

    def GetLastError(self):
        return self.last_error

    def OpenMutexW(self, access, inherit, name):
        return self.open_mutex


def with_kernel(k):
    return patch.object(webboot, "ctypes", fake(windll=fake(kernel32=k)))


class SingleInstance(unittest.TestCase):
    def test_claim_succeeds_when_mutex_is_new(self):
        k = Kernel(last_error=0)
        with with_kernel(k):
            self.assertTrue(webboot.claim_single_server())
        self.assertEqual(k.created, [webboot.SERVER_MUTEX])
        self.assertEqual(webboot._server_mutex, 1234)   # المقبض محفوظ حتى لا يُحرَّر

    def test_claim_fails_when_mutex_already_exists(self):
        with with_kernel(Kernel(last_error=webboot.ERROR_ALREADY_EXISTS)):
            self.assertFalse(webboot.claim_single_server())


class Agent(unittest.TestCase):
    def setUp(self):
        LOG.calls.clear()

    def test_not_launched_when_agent_mutex_is_held(self):
        with with_kernel(Kernel(open_mutex=99)), patch.object(webboot.subprocess, "Popen") as popen:
            self.assertFalse(webboot.ensure_agent())
        popen.assert_not_called()

    def test_launched_with_pythonw_when_absent(self):
        with with_kernel(Kernel(open_mutex=0)), patch.object(webboot.subprocess, "Popen") as popen, \
                patch.object(webboot.sys, "executable", r"C:\py\python.exe"):
            self.assertTrue(webboot.ensure_agent())
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [r"C:\py\pythonw.exe", os.path.join(TMP, "agent.py")])
        self.assertEqual((kwargs["cwd"], kwargs["creationflags"]), (TMP, 0x08000000))
        self.assertIn(("info", ("agent launched by server",)), LOG.calls)

    def test_failure_is_logged_not_raised(self):
        with with_kernel(Kernel(open_mutex=0)), patch.object(webboot.subprocess, "Popen", side_effect=OSError("no exe")):
            self.assertFalse(webboot.ensure_agent())
        self.assertIn(("exception", ("ensure_agent",)), LOG.calls)


class Marks(unittest.TestCase):
    def test_mark_appends_event_and_pid(self):
        path = os.path.join(TMP, "server_marks.txt")
        if os.path.exists(path):
            os.remove(path)
        webboot.mark("boot")
        webboot.mark("exit")
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith(f"boot pid={os.getpid()}"))
        self.assertTrue(lines[1].endswith(f"exit pid={os.getpid()}"))
        self.assertRegex(lines[0], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ")

    def test_mark_swallows_io_errors(self):
        with patch.object(webboot.infra, "LOG_DIR", os.path.join(TMP, "missing", "dir")):
            webboot.mark("boot")   # لا استثناء وإن غاب المجلد


class Watchdog(unittest.TestCase):
    def run_once(self, idle, last_request):
        class Stop(Exception):
            pass

        sleeps = iter([None, Stop()])

        def sleep(_):
            v = next(sleeps)
            if v:
                raise v
        exits = []
        with patch.object(webboot, "IDLE_EXIT_SECONDS", idle), patch.object(webboot.time, "sleep", sleep), \
                patch.object(webboot.snapshot, "last_request", lambda: last_request), \
                patch.object(webboot.os, "_exit", lambda code: exits.append(code)):
            with self.assertRaises(Stop):
                webboot.watchdog()
        return exits

    def test_zero_idle_limit_never_exits(self):
        self.assertEqual(self.run_once(0, 0.0), [])

    def test_exits_after_idle_limit(self):
        self.assertEqual(self.run_once(1, 0.0), [0])

    def test_recent_request_keeps_running(self):
        self.assertEqual(self.run_once(3600, webboot.time.time()), [])


class FakeServer:
    instances = []

    def __init__(self, addr, handler):
        self.addr, self.handler, self.served = addr, handler, False
        FakeServer.instances.append(self)

    def serve_forever(self):
        self.served = True


class BusyServer(FakeServer):
    def __init__(self, addr, handler):
        raise OSError("port busy")


class FakeThread:
    started = []

    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args, self.daemon = target, args, daemon

    def start(self):
        FakeThread.started.append(self)


class Run(unittest.TestCase):
    def setUp(self):
        LOG.calls.clear()
        FakeServer.instances.clear()
        FakeThread.started.clear()
        self.old_hook = webboot.threading.excepthook
        self.addCleanup(setattr, webboot.threading, "excepthook", self.old_hook)
        self.registered = []
        self.spawned = []
        self.patches = [patch.object(webboot.threading, "Thread", FakeThread),
                        patch.object(webboot.atexit, "register", self.registered.append),
                        patch.object(webboot.subprocess, "Popen", lambda *a, **k: self.spawned.append(a[0])),   # لا يُطلَق وكيل حقيقي
                        patch.object(webboot.faulthandler, "enable", lambda **k: LOG.calls.append(("faulthandler", tuple(sorted(k)))))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: webboot._fault_fh and webboot._fault_fh.close())

    def test_already_running_opens_window_only_with_open_flag(self):
        with with_kernel(Kernel(last_error=webboot.ERROR_ALREADY_EXISTS)):
            with self.assertRaises(SystemExit) as cm:
                webboot.run(FakeServer, object, ["server.py", "--open"])
            self.assertEqual(cm.exception.code, 0)
            self.assertIn(("open_window", ("synthetic-token",)), LOG.calls)
            LOG.calls.clear()
            with self.assertRaises(SystemExit):
                webboot.run(FakeServer, object, ["server.py"])
        self.assertNotIn(("open_window", ("synthetic-token",)), LOG.calls)
        self.assertEqual(FakeServer.instances, [])   # لم يُربط منفذ

    def test_port_busy_exits_zero_like_second_instance(self):
        with with_kernel(Kernel(last_error=0)):
            with self.assertRaises(SystemExit) as cm:
                webboot.run(BusyServer, object, ["server.py", "--open"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(("open_window", ("synthetic-token",)), LOG.calls)

    def test_boot_sequence_and_workers(self):
        with with_kernel(Kernel(last_error=0)):
            webboot.run(FakeServer, "HANDLER", ["server.py"])
        srv = FakeServer.instances[0]
        self.assertEqual((srv.addr, srv.handler, srv.served), (("127.0.0.1", 47832), "HANDLER", True))
        self.assertFalse(FakeServer.allow_reuse_address)
        names = [c[0] for c in LOG.calls]
        self.assertEqual(names.index("faulthandler") < names.index("refresh_extra") < names.index("health.init")
                         < names.index("integrity_cached"), True)
        self.assertIn(("faulthandler", ("all_threads", "file")), LOG.calls)
        self.assertIn(("info", ("server start pid=%d schema=%s", os.getpid(), 7)), LOG.calls)
        self.assertEqual(len(self.registered), 1)
        self.assertEqual(self.spawned, [[webboot.sys.executable.replace("python.exe", "pythonw.exe"), os.path.join(TMP, "agent.py")]])   # ensure_agent مرة واحدة
        targets = [t.target for t in FakeThread.started]
        self.assertEqual(targets, list(webboot.background_workers()) + [webboot.startup_mgr.list_items])
        self.assertTrue(all(t.daemon for t in FakeThread.started))
        self.assertNotIn(webboot.shellwin.open_window, targets)   # بلا --open لا نافذة
        with open(os.path.join(TMP, "server_marks.txt"), encoding="utf-8") as f:
            self.assertIn("boot pid=", f.read())
        self.registered[0]()   # معالج الخروج يكتب علامة exit ويسجّل
        with open(os.path.join(TMP, "server_marks.txt"), encoding="utf-8") as f:
            self.assertIn("exit pid=", f.read())

    def test_open_flag_opens_window_in_daemon_thread(self):
        with with_kernel(Kernel(last_error=0)):
            webboot.run(FakeServer, "HANDLER", ["server.py", "--open"])
        opener = [t for t in FakeThread.started if t.target is webboot.shellwin.open_window]
        self.assertEqual(len(opener), 1)
        self.assertEqual((opener[0].args, opener[0].daemon), (("synthetic-token",), True))

    def test_serve_forever_failure_is_logged_and_reraised(self):
        class Crashing(FakeServer):
            def serve_forever(self):
                raise RuntimeError("socket died")

        with with_kernel(Kernel(last_error=0)):
            with self.assertRaises(RuntimeError):
                webboot.run(Crashing, "HANDLER", ["server.py"])
        self.assertIn(("exception", ("serve_forever stopped",)), LOG.calls)

    def test_thread_excepthook_logs_crash(self):
        with with_kernel(Kernel(last_error=0)):
            webboot.run(FakeServer, "HANDLER", ["server.py"])
        try:
            raise ValueError("worker boom")
        except ValueError as exc:
            webboot.threading.excepthook(fake(thread=fake(name="sampler"), exc_type=type(exc), exc_value=exc, exc_traceback=exc.__traceback__))
        errors = [c for c in LOG.calls if c[0] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][1][1], "sampler")
        self.assertIn("worker boom", errors[0][1][2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
