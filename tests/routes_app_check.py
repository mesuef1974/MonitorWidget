"""مسارات التطبيق (routes_app، البند 8): شرح الصحة والأخطاء بالنموذج المحلي وتفريغه، وعدّاد فتح الصفحات، وإيقاف الخادم، بلا خادم
ولا قاعدة بيانات حقيقية: الوحدات التي تعتمد عليها (snapshot وhealth وdb وinfra وerrlog وexplain وpaths) بدائل وهمية تُحقن في
sys.modules قبل الاستيراد، وpageuse حقيقية على SQLite في الذاكرة. معزول: لا استيراد للتطبيق.

Run: python -X utf8 -B tests/routes_app_check.py
"""
import sqlite3
import sys
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CALLS = []
MEM = sqlite3.connect(":memory:", check_same_thread=False)
FAKE = {
    "db": types.SimpleNamespace(conn=lambda: MEM),
    "errlog": types.SimpleNamespace(swallowed=lambda: CALLS.append("swallowed")),
    "health": types.SimpleNamespace(top_procs=lambda n: [{"name": "llama-server.exe", "n": 1, "private_mb": 5713.4}],
                                    leaks=lambda: [{"name": "claude.exe", "fromMB": 100, "toMB": 760}],
                                    score=lambda m, tasks: {"parts": {"mem": 70}}),
    "infra": types.SimpleNamespace(log=types.SimpleNamespace(info=lambda *a: CALLS.append(("info",) + a))),
    "snapshot": types.SimpleNamespace(native_metrics=lambda: {"commitPct": 65, "availGB": 8.5},
                                      tasks=lambda: [{"label": "فاشلة", "enabled": True, "missing": False, "result": 1},
                                                     {"label": "ناجحة", "enabled": True, "missing": False, "result": 0},
                                                     {"label": "مفقودة", "enabled": True, "missing": True, "result": 1},
                                                     {"label": "معطّلة", "enabled": False, "missing": False, "result": 5}]),
    "explain": types.SimpleNamespace(errors=lambda metrics, root, budget=4: {"ok": True, "metrics": metrics, "root": str(root)},
                                     unload=lambda: {"ok": True, "freed": 1},
                                     health=lambda parts, metrics, extra=None: {"ok": True, "parts": parts, "extra": list(extra or [])}),
    "paths": types.SimpleNamespace(ROOT=str(ROOT)),
}
for _name, _mod in FAKE.items():
    sys.modules[_name] = _mod
import routes_app   # noqa: E402


class RouteTable(unittest.TestCase):
    def test_routes_and_foreign(self):
        self.assertEqual(routes_app.ROUTES, ("/api/explain-errors", "/api/llm-unload", "/api/explain", "/api/page-open", "/api/shutdown"))
        for route in ("/api/space", "/api/ticket", "/api/session/logout", "/api/nope"):
            self.assertIsNone(routes_app.post(route, {}))


class Explain(unittest.TestCase):
    def setUp(self):
        del CALLS[:]
        self.ex = sys.modules["explain"]

    def test_errors(self):
        code, r = routes_app.post("/api/explain-errors", {})
        self.assertEqual((code, r["metrics"], r["root"]), (200, {"commitPct": 65, "availGB": 8.5}, str(ROOT)))

    def test_unload(self):
        self.assertEqual(routes_app.post("/api/llm-unload", {}), (200, {"ok": True, "freed": 1}))
        saved = self.ex.unload
        try:
            self.ex.unload = lambda: {"ok": False, "err": "LM Studio غير مشغّل"}
            self.assertEqual(routes_app.post("/api/llm-unload", {}), (503, {"error": "LM Studio غير مشغّل"}))
            self.ex.unload = lambda: {"ok": False}
            self.assertEqual(routes_app.post("/api/llm-unload", {}), (503, {"error": "failed"}))
        finally:
            self.ex.unload = saved

    def test_health_explanation(self):
        code, r = routes_app.post("/api/explain", {})
        self.assertEqual((code, r["parts"]), (200, {"mem": 70}))
        self.assertEqual(r["extra"], ["الذاكرة: التزام 65% ومتاح 8.5 GB",
                                      "أكبر العمليات: llama-server.exe ×1 بذاكرة خاصة 5713 MB",
                                      "تسرّب محتمل: claude.exe من 100 إلى 760 MB",
                                      "مهمة فاشلة: فاشلة (النتيجة 1)"])   # الناجحة والمفقودة والمعطّلة لا تُذكر
        self.assertEqual(CALLS, [])

    def test_health_extra_failure_is_swallowed(self):
        h = sys.modules["health"]
        saved = h.top_procs

        def boom(n):
            raise RuntimeError("x")
        h.top_procs = boom
        try:
            code, r = routes_app.post("/api/explain", {})
        finally:
            h.top_procs = saved
        self.assertEqual((code, r["extra"]), (200, ["الذاكرة: التزام 65% ومتاح 8.5 GB"]))   # السطر الأساسي يبقى والشرح يُعاد
        self.assertEqual(CALLS, ["swallowed"])

    def test_health_model_failure_503(self):
        saved = self.ex.health
        try:
            self.ex.health = lambda parts, metrics, extra=None: {"ok": False, "err": "النموذج غير متاح"}
            self.assertEqual(routes_app.post("/api/explain", {}), (503, {"error": "النموذج غير متاح"}))
            self.ex.health = lambda parts, metrics, extra=None: {"ok": False}
            self.assertEqual(routes_app.post("/api/explain", {}), (503, {"error": "failed"}))
        finally:
            self.ex.health = saved


class PageOpen(unittest.TestCase):
    def test_record_and_reject(self):
        code, r = routes_app.post("/api/page-open", {"page": "mem"})
        self.assertEqual((code, r["ok"], r["page"]), (200, True, "mem"))
        self.assertEqual(MEM.execute("SELECT n FROM page_opens WHERE page='mem'").fetchone()[0], 1)
        routes_app.post("/api/page-open", {"page": "mem"})
        self.assertEqual(MEM.execute("SELECT n FROM page_opens WHERE page='mem'").fetchone()[0], 2)   # صفّ يومي يتراكم
        self.assertEqual(routes_app.post("/api/page-open", {"page": "Bad Key"}), (400, {"error": "bad page key"}))
        self.assertEqual(routes_app.post("/api/page-open", {}), (400, {"error": "bad page key"}))


class Shutdown(unittest.TestCase):
    def setUp(self):
        del CALLS[:]
        self.saved = (routes_app.EXIT, routes_app.EXIT_DELAY)
        self.exited, self.done = [], threading.Event()
        routes_app.EXIT, routes_app.EXIT_DELAY = (lambda code: (self.exited.append(code), self.done.set())), 0.05
        self.addCleanup(lambda: setattr(routes_app, "EXIT", self.saved[0]))
        self.addCleanup(lambda: setattr(routes_app, "EXIT_DELAY", self.saved[1]))

    def test_reply_then_exit(self):
        self.assertEqual(routes_app.post("/api/shutdown", {}), (200, {"ok": True}))
        self.assertEqual(self.exited, [])             # الردّ يُعاد قبل الخروج
        self.assertTrue(self.done.wait(2))
        self.assertEqual(self.exited, [0])
        self.assertIn(("info", "shutdown requested"), CALLS)
        self.assertNotIn("swallowed", CALLS)         # wal_checkpoint نجح على الاتصال

    def test_checkpoint_failure_is_swallowed(self):
        dbm, saved = sys.modules["db"], sys.modules["db"].conn

        class Broken:
            def execute(self, *a):
                raise sqlite3.OperationalError("locked")
        dbm.conn = lambda: Broken()
        try:
            self.assertEqual(routes_app.post("/api/shutdown", {}), (200, {"ok": True}))   # الإيقاف لا يُعرقله فشل الضغط
        finally:
            dbm.conn = saved
        self.assertTrue(self.done.wait(2))
        self.assertEqual(CALLS.count("swallowed"), 1)


class ContractTests(unittest.TestCase):
    def test_server_delegates(self):
        src = (ROOT / "server.py").read_text(encoding="utf-8-sig")
        self.assertIn("import routes_app", src)
        self.assertIn("routes_app)", src.split("POST_MODULES = ", 1)[1].split("\n", 1)[0])
        for route in routes_app.ROUTES:
            self.assertNotIn('route == "%s"' % route, src)       # لم يعد مضمّنًا في do_POST
        for needle in ("pageuse.", "explain.", "wal_checkpoint", "import errlog"):
            self.assertNotIn(needle, src)
        for route in ("/api/ticket", "/api/session/logout"):     # ما بقي في الخادم: التذكرة والجلسة (حالة المعالج وكعكته)
            self.assertIn('route == "%s"' % route, src)

    def test_page_calls_routes(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for route in ("/api/explain-errors", "/api/llm-unload", "/api/explain", "/api/page-open"):
            self.assertIn('"%s"' % route, html)


if __name__ == "__main__":
    unittest.main(verbosity=1)
