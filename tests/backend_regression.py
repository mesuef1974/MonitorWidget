"""Isolated backend regressions; never imports server or opens the live DB.

Run: python -B tests/backend_regression.py
Only a temporary directory, in-memory request doubles, and extracted AST are used.
"""
import ast
import hashlib
import hmac
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import atomic_json  # pure stdlib helper, no app initialization or side effects


def extracted():
    tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8-sig"))
    wanted = {"_session_create", "_session_valid", "_session_revoke", "_reject_json_constant",
              "_validate_json_numbers", "Handler", "LocalHTTPServer"}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in wanted]
    ns = dict(json=json, math=math, re=re, socket=socket, time=time, secrets=secrets, hashlib=hashlib,
              hmac=hmac, os=os, threading=threading, BaseHTTPRequestHandler=BaseHTTPRequestHandler,
              ThreadingHTTPServer=ThreadingHTTPServer, PORT=47832, MAX_POST_BYTES=65536,
              MAX_HTTP_THREADS=2, REQUEST_TIMEOUT_SECONDS=15, SESSION_TTL=60,
              MAX_SESSIONS=128, _SESSIONS={}, _SLOCK=threading.Lock(),
              POST_MODULES=(), snapshot=types.SimpleNamespace(invalidate=lambda: None))   # وحدات المسارات (البند 6) تُختبر وحدها؛ هنا لا مسار منها؛ snapshot وهمي (البند 6/ز)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "extracted-server", "exec"), ns)
    return ns


class BackendRequests(unittest.TestCase):
    def setUp(self):
        self.ns = extracted()
        self.sends = []
        self.ns["startup_mgr"] = types.SimpleNamespace(toggle=lambda body: (400, {"error": "rejected"}))
        self.ns["infra"] = types.SimpleNamespace(log=types.SimpleNamespace(exception=lambda *args: None))
        self.ns["POST_MODULES"] = (types.SimpleNamespace(post=lambda route, body, **k: self.ns["startup_mgr"].toggle(body) if route == "/api/startup" else None),)   # /api/startup صار في routes_policy؛ هنا بديل يمرّر إلى startup_mgr

    def request(self, body=b"{}", length=None, **headers):
        req = types.SimpleNamespace(
            _guard=lambda: True, _authed=lambda: True,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body)) if length is None else length, **headers},
            rfile=io.BytesIO(body), path="/api/startup", close_connection=False,
            _send=lambda *args: self.sends.append(args))
        self.ns["Handler"].do_POST(req)
        return req

    def test_startup_returns_one_response(self):
        self.request()
        self.assertEqual(self.sends, [(400, {"error": "rejected"})])

    def test_invalid_lengths(self):
        for val in ("-1", "hello", "1.0", "9" * 5000):
            with self.subTest(val=val[:8]):
                self.sends.clear(); self.request(length=val)
                self.assertEqual(self.sends[0][0], 400)

    def test_large_request_is_rejected_before_read(self):
        req = self.request(length="65537")
        self.assertEqual(self.sends[0][0], 413)
        self.assertTrue(req.close_connection)
        self.assertEqual(req.rfile.tell(), 0)

    def test_incomplete_request(self):
        self.request(length="5")
        self.assertEqual(self.sends[0][0], 400)

    def test_transfer_encoding_rejected(self):
        self.request(**{"Transfer-Encoding": "chunked"})
        self.assertEqual(self.sends[0][0], 400)

    def test_media_type_not_substring(self):
        self.request(**{"Content-Type": "notapplication/json"})
        self.assertEqual(self.sends[0][0], 415)

    def test_invalid_json_is_400(self):
        for body in (b"{", b'{"n":NaN}', b'{"n":Infinity}', b'{"n":-Infinity}', b'{"n":1e309}', b'[]'):
            with self.subTest(body=body):
                self.sends.clear(); self.request(body)
                self.assertEqual(self.sends[0][0], 400)

    def test_internal_error_does_not_leak_exception(self):
        def broken(body):
            raise RuntimeError("private-path-and-secret-placeholder")
        self.ns["startup_mgr"].toggle = broken
        self.request()
        self.assertEqual(self.sends[0][0], 500)
        self.assertEqual(self.sends[0][1]["error"], "internal error")
        self.assertNotIn("private-path", str(self.sends))

    def test_seen_rejects_nonfinite_and_huge_values_before_preview(self):
        import routes_destructive   # البند 6/أ: فحص seen_mb انتقل إلى وحدة المسارات الهدّامة؛ يرفض قبل أي معاينة أو رمز
        for seen in (float("nan"), float("inf"), float("-inf"), 10**1000, True, -1):
            with self.subTest(seen=str(seen)[:20]):
                self.assertEqual(routes_destructive._space_confirmed("/api/space", {"seen_mb": seen}, "clean", "x"),
                                 (400, {"error": "seen_mb required"}))

    def test_sessions_independent_expire_and_revoke(self):
        a, b = self.ns["_session_create"](), self.ns["_session_create"]()
        self.assertNotEqual(a, b)
        self.assertTrue(self.ns["_session_valid"](a))
        self.ns["_session_revoke"](a)
        self.assertFalse(self.ns["_session_valid"](a))
        self.assertTrue(self.ns["_session_valid"](b))
        with patch.object(time, "time", return_value=time.time() + 61):
            self.assertFalse(self.ns["_session_valid"](b))

    def test_session_capacity_is_bounded(self):
        self.ns["MAX_SESSIONS"] = 1
        self.assertTrue(self.ns["_session_create"]())
        self.assertIsNone(self.ns["_session_create"]())

    def test_widget_token_and_browser_sessions_are_independent(self):
        self.ns["TOKEN"] = "synthetic-widget-token"
        value = self.ns["_session_create"]()
        fake = types.SimpleNamespace(headers={"X-Token": "synthetic-widget-token"},
            _cookie=lambda name: "", _send=lambda *a: self.sends.append(a))
        self.assertTrue(self.ns["Handler"]._authed(fake))
        fake.headers = {}
        fake._cookie = lambda name: value
        self.assertTrue(self.ns["Handler"]._authed(fake))
        self.ns["_session_revoke"](value)
        self.assertFalse(self.ns["Handler"]._authed(fake))
        self.assertEqual(self.sends[-1][0], 401)

    def test_logout_revokes_only_its_cookie(self):
        a, b = self.ns["_session_create"](), self.ns["_session_create"]()
        replies = []
        fake = types.SimpleNamespace(_guard=lambda: True, _authed=lambda: True,
            headers={"Content-Type": "application/json", "Content-Length": "2"},
            rfile=io.BytesIO(b"{}"), path="/api/session/logout", _cookie=lambda name: a,
            _send=lambda *args, **kwargs: replies.append((args, kwargs)))
        self.ns["Handler"].do_POST(fake)
        self.assertEqual(replies[0][0], (200, {"ok": True}))
        self.assertIn("Max-Age=0", replies[0][1]["cookie"])
        self.assertFalse(self.ns["_session_valid"](a))
        self.assertTrue(self.ns["_session_valid"](b))

    def test_partial_protected_engine_fails_closed(self):
        import routes_destructive
        with tempfile.TemporaryDirectory(prefix="mw_backend_", dir=ROOT / "tests") as d:
            with patch.object(routes_destructive, "PROT_ENGINE_DIR", d), patch.object(routes_destructive, "IS_ADMIN", False), \
                    patch.object(routes_destructive, "ENGINE", "legacy"):
                path, error = routes_destructive.resolve_engine()
            self.assertIsNone(path)
            self.assertTrue(error)

    def test_http_concurrency_slot_released_after_thread(self):
        cls = self.ns["LocalHTTPServer"]
        srv = object.__new__(cls)
        srv._slots = threading.BoundedSemaphore(1)
        srv._slots.acquire()
        with patch.object(ThreadingHTTPServer, "process_request_thread", side_effect=RuntimeError("test")):
            with self.assertRaises(RuntimeError):
                srv.process_request_thread(None, None)
        self.assertTrue(srv._slots.acquire(blocking=False))


class AtomicConfiguration(unittest.TestCase):
    def test_abort_and_replace_failure_preserve_original(self):
        with tempfile.TemporaryDirectory(prefix="mw_backend_", dir=ROOT / "tests") as d:
            p = Path(d) / "config.json"; p.write_text('{"count":0}', encoding="utf-8")
            with self.assertRaises(RuntimeError):
                with atomic_json.transaction(p) as (_, obj):
                    obj["count"] = 3
                    raise RuntimeError("abort")
            self.assertEqual(json.loads(p.read_text())["count"], 0)
            with patch.object(atomic_json.os, "replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    with atomic_json.transaction(p) as (_, obj):
                        obj["count"] = 4
            self.assertEqual(json.loads(p.read_text())["count"], 0)
            self.assertFalse(list(Path(d).glob("*.tmp")))

    def test_concurrent_processes_do_not_lose_updates(self):
        with tempfile.TemporaryDirectory(prefix="mw_backend_", dir=ROOT / "tests") as d:
            p = Path(d) / "config.json"; p.write_text('{"count":0}', encoding="utf-8")
            code = "import sys;sys.path.insert(0,sys.argv[1]);import atomic_json\nfor i in range(20):\n with atomic_json.transaction(sys.argv[2]) as (_,obj):obj['count']+=1\n"
            workers = [subprocess.Popen([sys.executable, "-B", "-c", code, str(ROOT), str(p)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
            for worker in workers:
                _, err = worker.communicate(timeout=30)
                self.assertEqual(worker.returncode, 0, err.decode("utf-8", "replace"))
            self.assertEqual(json.loads(p.read_text())["count"], 80)

    def test_bom_and_finite_json(self):
        with tempfile.TemporaryDirectory(prefix="mw_backend_", dir=ROOT / "tests") as d:
            p = Path(d) / "config.json"
            p.write_bytes(b'\xef\xbb\xbf{"count":0}')
            with atomic_json.transaction(p) as (_, obj):
                obj["count"] = 1
            self.assertTrue(p.read_bytes().startswith(b"\xef\xbb\xbf"))
            with self.assertRaises(ValueError):
                with atomic_json.transaction(p) as (_, obj):
                    obj["count"] = float("nan")
            self.assertEqual(atomic_json.read(p)[1]["count"], 1)


class ProtectedPin(unittest.TestCase):
    def test_protected_pin_missing_or_invalid_never_unpins(self):
        tree = ast.parse((ROOT / "secure.py").read_text(encoding="utf-8-sig"))
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        with tempfile.TemporaryDirectory(prefix="mw_backend_", dir=ROOT / "tests") as d:
            pin = Path(d) / "pin.json"
            ns = dict(os=os, json=json, hashlib=hashlib, SECURE_DIR=d, REAPER_PIN=str(pin))
            exec(compile(ast.Module(body=funcs, type_ignores=[]), "extracted-secure", "exec"), ns)
            self.assertEqual(ns["reaper_pin_state"]("unused")[0], "pinned-mismatch")
            pin.write_text("[]", encoding="utf-8")
            self.assertEqual(ns["reaper_pin_state"]("unused")[0], "pinned-mismatch")


class ShellStatus(unittest.TestCase):
    """حالة الغلاف في /api/health (shellwin.py): الجاهزية وسببها ومحرك آخر فتح، بلا تشغيل الخادم (ملف حالة مؤقت ومجلد جذر مؤقت)."""

    def _ns(self, root, localappdata):
        tree = ast.parse((ROOT / "shellwin.py").read_text(encoding="utf-8-sig"))   # البند 6/هـ: دوال الغلاف في shellwin.py
        wanted = {"_shell_readiness", "_shell_ready", "_record_shell", "shell_status", "record_shell_report"}
        consts = {"SHELL_ENGINES", "SHELL_REASONS"}
        nodes = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in wanted)
                 or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in consts for t in n.targets))]
        log = types.SimpleNamespace(exception=lambda *a, **k: None)
        ns = dict(os=os, time=time, atomic_json=atomic_json, infra=types.SimpleNamespace(log=log), ROOT=str(root),
                  SHELL_STATE_PATH=os.path.join(localappdata, "MonitorWidget", "shell_state.json"))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "extracted-server", "exec"), ns)
        return ns

    def test_fallback_reasons_and_last_engine(self):
        with tempfile.TemporaryDirectory(prefix="mw_shell_", dir=ROOT / "tests") as d:
            root, lad = Path(d) / "root", Path(d) / "lad"
            root.mkdir(); lad.mkdir()
            with patch.dict(os.environ, {"LOCALAPPDATA": str(lad)}):
                ns = self._ns(root, str(lad))
                self.assertEqual(ns["_shell_readiness"](), (False, "no_shell"))
                st = ns["shell_status"]()
                self.assertTrue(st["fallback"]); self.assertEqual(st["reason"], "no_shell"); self.assertIsNone(st["engine"])
                (root / "app_shell.ps1").write_text("# shell", encoding="utf-8")
                self.assertEqual(ns["_shell_readiness"](), (False, "no_webview2"))
                ns["_record_shell"]("edge", "no_webview2")
                st = ns["shell_status"]()
                self.assertEqual((st["engine"], st["lastReason"], st["fallback"]), ("edge", "no_webview2", True))
                wv = lad / "MonitorWidget" / "webview2"; wv.mkdir(parents=True, exist_ok=True)
                (wv / "Microsoft.Web.WebView2.Wpf.dll").write_bytes(b"")
                self.assertTrue(ns["_shell_ready"]())
                st = ns["shell_status"]()
                self.assertTrue(st["ready"]); self.assertTrue(st["fallback"])   # آخر فتح كان Edge: التحذير يبقى حتى يُعاد الفتح
                ns["_record_shell"]("webview2")
                st = ns["shell_status"]()
                self.assertFalse(st["fallback"]); self.assertEqual(st["engine"], "webview2")

    def test_report_endpoint_and_init_failure_detail(self):
        """POST /api/shell: قائمة بيضاء للمحرك والسبب؛ وسقوط الغلاف نفسه (init_failed) يُحذّر رغم توفر المكتبات ويحمل سطر shell_error.log."""
        with tempfile.TemporaryDirectory(prefix="mw_shell_", dir=ROOT / "tests") as d:
            root, lad = Path(d) / "root", Path(d) / "lad"
            root.mkdir()
            (root / "app_shell.ps1").write_text("# shell", encoding="utf-8")
            wv = lad / "MonitorWidget" / "webview2"
            wv.mkdir(parents=True)
            (wv / "Microsoft.Web.WebView2.Wpf.dll").write_bytes(b"")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(lad)}):
                ns = self._ns(root, str(lad))
                report = ns["record_shell_report"]
                for bad in ({"engine": "chrome"}, {"engine": "edge", "reason": "because"}, {"engine": "edge", "reason": None}, {}):
                    self.assertEqual(report(bad)[0], 400, bad)
                self.assertFalse(os.path.exists(ns["SHELL_STATE_PATH"]))   # المرفوض لا يُسجَّل
                code, out = report({"engine": "edge", "reason": "init_failed"})
                self.assertEqual(code, 200)
                self.assertEqual((out["shell"]["ready"], out["shell"]["fallback"], out["shell"]["lastReason"], out["shell"]["detail"]), (True, True, "init_failed", ""))
                (lad / "MonitorWidget" / "shell_error.log").write_text("2026-10-27T17:00:00 init_failed: HRESULT 0x80070002\n", encoding="utf-8")
                self.assertEqual(ns["shell_status"]()["detail"], "2026-10-27T17:00:00 init_failed: HRESULT 0x80070002")
                code, out = report({"engine": "webview2"})   # السبب الافتراضي "" مقبول
                self.assertEqual(code, 200)
                self.assertEqual((out["shell"]["fallback"], out["shell"]["detail"]), (False, ""))   # التفاصيل تظهر مع init_failed فقط
                # مخطط الملف الذي يكتبه app_shell.ps1 بنفسه (ConvertTo-ShellStateJson) يُقرأ كما هو
                Path(ns["SHELL_STATE_PATH"]).write_text('{"engine":"edge","reason":"no_webview2","ts":5}', encoding="utf-8")
                st = ns["shell_status"]()
                self.assertEqual((st["engine"], st["lastReason"], st["ts"], st["fallback"]), ("edge", "no_webview2", 5, True))


class PageUse(unittest.TestCase):
    """عدّاد فتح الصفحات (البند 5): pageuse.py على قاعدة sqlite في الذاكرة، بلا خادم ولا قاعدة حية."""

    DAY = 86400
    T0 = 1_800_000_000   # 2027-01-15 ~08:00 UTC: بعيد عن حدود اليوم المحلي في كل المناطق الزمنية المعقولة

    def _c(self):
        import sqlite3
        return sqlite3.connect(":memory:")

    def test_record_validates_counts_and_aggregates_per_day(self):
        import pageuse
        c = self._c()
        for bad in ({}, {"page": ""}, {"page": "Overview"}, {"page": "a b"}, {"page": "x" * 33}, {"page": 5}, {"page": None}, {"page": "../x"}, []):
            self.assertEqual(pageuse.record(c, bad, now=self.T0)[0], 400, bad)
        self.assertEqual(pageuse.stats(c, now=self.T0)["pages"], [])   # المرفوض لا يُسجَّل ولا يفتح نافذة القياس
        self.assertIsNone(pageuse.stats(c, now=self.T0)["since"])
        code, out = pageuse.record(c, {"page": "overview"}, now=self.T0)
        self.assertEqual((code, out["ok"], out["page"], out["day"]), (200, True, "overview", pageuse._day(self.T0)))
        pageuse.record(c, {"page": "overview"}, now=self.T0 + 60)
        pageuse.record(c, {"page": "space-clean"}, now=self.T0 + 120)
        pageuse.record(c, {"page": "overview"}, now=self.T0 + self.DAY)   # اليوم التالي: صفّ جديد للصفحة نفسها
        st = pageuse.stats(c, now=self.T0 + self.DAY)
        self.assertEqual([(p["page"], p["opens"], p["days"], p["last"]) for p in st["pages"]],
                         [("overview", 3, 2, self.T0 + self.DAY), ("space-clean", 1, 1, self.T0 + 120)])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM page_opens").fetchone()[0], 3)
        self.assertEqual((st["windowDays"], st["since"], st["elapsedDays"], st["complete"]), (14, pageuse._day(self.T0), 2, False))

    def test_window_rolls_and_completes_after_fourteen_days(self):
        import pageuse
        c = self._c()
        pageuse.record(c, {"page": "health"}, now=self.T0)
        st = pageuse.stats(c, now=self.T0)
        self.assertEqual((st["elapsedDays"], st["complete"], st["from"], st["today"]), (1, False, pageuse._day(self.T0 - 13 * self.DAY), pageuse._day(self.T0)))
        st = pageuse.stats(c, now=self.T0 + 13 * self.DAY)   # اليوم الرابع عشر: المدة اكتملت والصفّ الأول ما زال داخل النافذة
        self.assertEqual((st["elapsedDays"], st["complete"], [p["opens"] for p in st["pages"]]), (14, True, [1]))
        st = pageuse.stats(c, now=self.T0 + 14 * self.DAY)   # اليوم الخامس عشر: النافذة المتحركة تُسقط الصفّ الأول لكن بداية القياس تبقى
        self.assertEqual((st["elapsedDays"], st["complete"], st["pages"], st["since"]), (15, True, [], pageuse._day(self.T0)))
        pageuse.record(c, {"page": "health"}, now=self.T0 + 14 * self.DAY)
        self.assertEqual([p["opens"] for p in pageuse.stats(c, now=self.T0 + 14 * self.DAY)["pages"]], [1])

    def test_prune_old_rows_and_cap_distinct_pages(self):
        import pageuse
        c = self._c()
        pageuse.record(c, {"page": "old"}, now=self.T0)
        pageuse.record(c, {"page": "kept"}, now=self.T0 + 59 * self.DAY)
        pageuse.record(c, {"page": "fresh"}, now=self.T0 + pageuse.KEEP_DAYS * self.DAY + self.DAY)   # أول تسجيل بعد 60 يومًا يحذف الأقدم فقط
        self.assertEqual(sorted(r[0] for r in c.execute("SELECT page FROM page_opens")), ["fresh", "kept"])
        for i in range(pageuse.MAX_PAGES - 2):
            self.assertEqual(pageuse.record(c, {"page": "p%d" % i}, now=self.T0 + 70 * self.DAY)[0], 200)
        self.assertEqual(pageuse.record(c, {"page": "one-too-many"}, now=self.T0 + 70 * self.DAY), (400, {"error": "too many pages"}))
        self.assertEqual(pageuse.record(c, {"page": "kept"}, now=self.T0 + 70 * self.DAY)[0], 200)   # المعروف يُقبل رغم السقف
        self.assertEqual(c.execute("SELECT COUNT(DISTINCT page) FROM page_opens").fetchone()[0], pageuse.MAX_PAGES)

    def test_server_and_page_are_wired(self):
        """المسارات في وحدتي المسارات والواجهة تبلّغ عند الانتقال وتعرض الملخص في صفحة الإعدادات والتحكم؛ وثابت الفحص المرئي موجود."""
        app = (ROOT / "routes_app.py").read_text(encoding="utf-8-sig")   # POST صار في وحدة مسارات التطبيق (البند 8)
        reads = (ROOT / "routes_read.py").read_text(encoding="utf-8-sig")   # GET صار في جدول المسارات القرائية (البند 6/ح)
        page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for needle in ('"/api/page-use"', "pageuse.stats(db.conn())"):
            self.assertIn(needle, reads)
        for needle in ('"/api/page-open"', "pageuse.record(db.conn(), body)"):
            self.assertIn(needle, app)
        for needle in ('fetch("/api/page-open"', "track(key)", 'api("/api/page-use")', "pageUseBody(pu)"):
            self.assertIn(needle, page)
        self.assertNotIn('localStorage.setItem("mw-v2-pageuse', page)   # العدّ في الخادم لا في المتصفح
        fx = json.loads((ROOT / "tests" / "fixtures" / "api" / "page-use.json").read_text(encoding="utf-8"))
        self.assertEqual(set(fx) >= {"windowDays", "since", "elapsedDays", "complete", "pages"}, True)


class DestructiveRoutes(unittest.TestCase):
    """البند 6/أ: routes_destructive.py بلا خادم: 428 بلا رمز لكل فعل هدّام، الرمز أحادي الاستعمال ومربوط بالمسار والمعاملات،
    409 عند تغيّر المرشّحين، 400 بلا معاينة، والتجربة dry تمرّ بلا تأكيد. space/insight/subprocess مستبدلة بدوال وهمية."""

    def setUp(self):
        import routes_destructive
        self.rd = routes_destructive
        self.rd.CONFIRMS = routes_destructive.ConfirmStore()   # متجر رموز نظيف لكل اختبار
        self.rd.MANUAL.update(proc=None, tier=None, dry=None, started=None)

    def test_confirm_store_is_single_use_bound_and_expiring(self):
        now = [1000.0]
        store = self.rd.ConfirmStore(ttl=10, limit=2, clock=lambda: now[0])
        key = self.rd.ConfirmStore.key("/api/x", {"a": 1, "confirm": "ignored"})
        self.assertEqual(key, self.rd.ConfirmStore.key("/api/x", {"a": 1}))          # confirm لا يدخل في المفتاح
        tok = store.issue(key)
        self.assertFalse(store.take(self.rd.ConfirmStore.key("/api/x", {"a": 2}), tok))   # معاملات مختلفة: يُرفض ويُستهلك
        tok = store.issue(key)
        self.assertTrue(store.take(key, tok))
        self.assertFalse(store.take(key, tok))                                          # أحادي الاستعمال
        tok = store.issue(key)
        now[0] += 11
        self.assertFalse(store.take(key, tok))                                          # انتهت المدة
        a = store.issue(key)
        store.issue(key)
        c = store.issue(key)                                                            # السقف 2: الأقدم يسقط
        self.assertFalse(store.take(key, a))
        self.assertTrue(store.take(key, c))

    def test_space_clean_needs_preview_then_confirm_bound_to_params(self):
        calls = []

        def fake_start(key, seen, early=False):
            calls.append((key, seen, early))
            return {"ok": True, "state": "running", "phase": "measure"}
        with patch.object(self.rd.space, "clean_start", fake_start), \
                patch.object(self.rd.space, "clean", lambda *a, **k: self.fail("the request must not measure or delete")):
            self.assertEqual(self.rd.post("/api/space", {"action": "clean", "kind": "temp"}), (400, {"error": "seen_mb required"}))
            self.assertEqual(self.rd.post("/api/space", {"action": "clean", "kind": "temp", "seen_mb": True})[0], 400)
            body = {"action": "clean", "kind": "temp", "seen_mb": 10}
            code, rep = self.rd.post("/api/space", body)
            self.assertEqual((code, rep["error"], rep["ttl"]), (428, "confirm_required", 120))
            self.assertTrue(rep["confirm"])
            self.assertEqual(calls, [])                                                 # لا بدء قبل الرمز
            code2, rep2 = self.rd.post("/api/space", {**body, "seen_mb": 11, "confirm": rep["confirm"]})   # معاملات مختلفة
            self.assertEqual(code2, 428)
            self.assertNotEqual(rep2["confirm"], rep["confirm"])
            code3, rep3 = self.rd.post("/api/space", {**body, "confirm": "bogus"})
            self.assertEqual(code3, 428)
            code4, rep4 = self.rd.post("/api/space", {**body, "confirm": rep3["confirm"]})
            self.assertEqual((code4, rep4["state"]), (200, "running"))                 # يعود فورًا؛ القياس والحذف في المهمة
            self.assertEqual(calls, [("temp", 10, False)])
            self.assertEqual(self.rd.post("/api/space", {**body, "confirm": rep3["confirm"]})[0], 428)    # الرمز استُهلك

    def test_space_clean_start_keys_and_codes(self):
        replies = {"edgecache": {"ok": False, "state": "error", "error": "clean already running", "running": "temp"},
                   "nope": {"ok": False, "state": "error", "error": "غير مسموح"}}
        calls = []

        def fake_start(key, seen, early=False):
            calls.append((key, early))
            return replies.get(key, {"ok": True, "state": "running"})

        def confirmed(body):
            code, rep = self.rd.post("/api/space", body)
            self.assertEqual(code, 428)
            return self.rd.post("/api/space", {**body, "confirm": rep["confirm"]})[0]
        with patch.object(self.rd.space, "clean_start", fake_start):
            self.assertEqual(confirmed({"action": "clean", "kind": "edgecache", "seen_mb": 1}), 409)   # تنظيف آخر يعمل
            self.assertEqual(confirmed({"action": "clean", "kind": "nope", "seen_mb": 1}), 400)
            self.assertEqual(confirmed({"action": "cleanall", "seen_mb": 1}), 200)
            confirmed({"action": "clean", "kind": "winold", "seen_mb": 1, "early": True})
            confirmed({"action": "clean", "kind": "temp", "seen_mb": 1, "early": True})                # early لـ Windows.old وحده
        self.assertEqual(calls, [("edgecache", False), ("nope", False), ("all", False), ("winold", True), ("temp", False)])

    def test_space_safe_actions_need_no_confirm(self):
        with patch.object(self.rd.space, "preview_start", lambda kind: {"ok": True, "state": "running", "kind": kind}), \
                patch.object(self.rd.space, "clean", lambda *a, **k: self.fail("preview must not measure in the request")):
            self.assertEqual(self.rd.post("/api/space", {"action": "preview", "kind": "temp"}), (200, {"ok": True, "state": "running", "kind": "temp"}))
        self.assertEqual(self.rd.post("/api/space", {"action": "nope"}), (400, {"error": "bad action"}))
        self.assertIsNone(self.rd.post("/api/other", {}))
        self.assertEqual(self.rd.ROUTES, ("/api/space", "/api/reaper-run", "/api/run-cleanup", "/api/proc-close"))

    def test_proc_close_policy_then_confirm_then_close(self):
        """إغلاق عملية بعينها: 400 لمعاملات غير صالحة، 409 لما ترفضه السياسة قبل أي تأكيد، 428 للمسموح، ثم الإغلاق بالرمز وتسجيله."""
        closed, logged = [], []
        rows = {100: "llama-server.exe", 200: "explorer.exe"}

        class FakeConn:
            def execute(self, sql, args=()):
                logged.append(args)

            def commit(self):
                pass

        def fake_close(pid, name, force=False):
            closed.append((pid, name, force))
            return {"ok": True, "pid": pid, "name": name, "method": "terminate", "exited": True, "windows": 0, "commitMB": 5716}
        with patch.object(self.rd.procclose, "names_by_pid", lambda: rows), patch.object(self.rd.procclose, "close", fake_close), \
                patch.object(self.rd.db, "conn", lambda: FakeConn()):
            self.assertEqual(self.rd.post("/api/proc-close", {"pid": "100", "name": "llama-server.exe"}), (400, {"error": "bad params"}))
            self.assertEqual(self.rd.post("/api/proc-close", {"pid": 100, "name": "llama-server.exe", "force": "yes"}), (400, {"error": "bad params"}))
            self.assertEqual(self.rd.post("/api/proc-close", {"pid": 200, "name": "explorer.exe"})[0], 409)              # محمية: لا يُطلب تأكيد
            self.assertEqual(self.rd.post("/api/proc-close", {"pid": 100, "name": "claude.exe"}), (409, {"error": "name_mismatch", "pid": 100, "name": "claude.exe"}))
            self.assertEqual(self.rd.post("/api/proc-close", {"pid": 999, "name": "x.exe"})[1]["error"], "not_found")
            code, rep = self.rd.post("/api/proc-close", {"pid": 100, "name": "llama-server.exe", "force": False})
            self.assertEqual((code, rep["error"], closed), (428, "confirm_required", []))
            code2, rep2 = self.rd.post("/api/proc-close", {"pid": 100, "name": "llama-server.exe", "force": True, "confirm": rep["confirm"]})
            self.assertEqual((code2, closed), (428, []))                                                                   # force جزء من المفتاح: الرمز يُرفض ويُستهلك ويصدر آخر
            code3, rep3 = self.rd.post("/api/proc-close", {"pid": 100, "name": "llama-server.exe", "force": True, "confirm": rep2["confirm"]})
            self.assertEqual((code3, rep3["exited"], closed), (200, True, [(100, "llama-server.exe", True)]))
            self.assertEqual(logged[-1][1:4], ("procclose", "page", 1))
            self.assertIn("pid=100 name=llama-server.exe force=True method=terminate exited=True commitMB=5716", logged[-1][4])
            self.assertEqual(self.rd.post("/api/proc-close", {"pid": 100, "name": "llama-server.exe", "force": True, "confirm": rep2["confirm"]})[0], 428)   # أحادي الاستعمال

    def test_reaper_run_dry_passes_and_real_requires_confirm(self):
        seen = []

        def fake_run(dry, mcdir):
            seen.append((dry, os.path.basename(mcdir)))
            return {"ok": True, "dry": dry}
        with patch.object(self.rd.insight, "reaper_run", fake_run):
            self.assertEqual(self.rd.post("/api/reaper-run", {"dry": "yes"}), (400, {"error": "dry must be boolean"}))
            self.assertEqual(self.rd.post("/api/reaper-run", {"dry": True}), (200, {"ok": True, "dry": True}))
            code, rep = self.rd.post("/api/reaper-run", {"dry": False})
            self.assertEqual((code, seen), (428, [(True, "MachineCare")]))
            self.assertEqual(self.rd.post("/api/reaper-run", {"dry": False, "confirm": rep["confirm"]}), (200, {"ok": True, "dry": False}))

    def test_real_cleanup_uses_elevated_task_unless_paused_or_dry(self):
        """التشغيل الفعلي بالنسخة المحمية يمرّ بمهمة Cleanup_v2_* المرتفعة، ويبقى مباشرًا للتجربة وللمستوى الموقوف وعند فشل المهمة."""
        runs, popen_args, state = [], [], {"rc": 0, "paused": False, "out": '"h","t","n","Running","i","l","267009","x"'}

        def fake_run(args, **kw):
            runs.append(list(args))
            return types.SimpleNamespace(returncode=state["rc"], stdout=state["out"])

        class FakeProc:
            def __init__(self, args, creationflags=0):
                popen_args.append(list(args))

            def poll(self):
                return 0
        prot = os.path.join(self.rd.PROT_ENGINE_DIR, "CleanupEngine.ps1")

        def go(body):
            code, rep = self.rd.post("/api/run-cleanup", body)
            if code == 428:
                code, rep = self.rd.post("/api/run-cleanup", dict(body, confirm=rep["confirm"]))
            return code, rep
        with patch.object(self.rd.subprocess, "run", fake_run), patch.object(self.rd.subprocess, "Popen", FakeProc), \
                patch.object(self.rd, "resolve_engine", lambda: (prot, None)), patch.object(self.rd, "IS_ADMIN", False), \
                patch.object(self.rd, "_tier_paused", lambda t: state["paused"]):
            self.assertEqual(go({"tier": "quarterly", "dry": False}), (200, {"ok": True}))
            self.assertEqual(runs[0], ["schtasks", "/run", "/tn", "Cleanup_v2_Quarterly"])
            self.assertEqual(popen_args, [])
            run = self.rd.MANUAL["proc"]
            self.assertTrue(self.rd.manual_status()["running"])                  # مهلة بدء المهمة
            run.started -= self.rd.TASK_GRACE_S + 1
            self.assertIsNone(run.poll())                                        # 267009: ما زالت تعمل
            self.assertEqual(self.rd.post("/api/run-cleanup", {"tier": "weekly"})[0], 409)
            state["out"] = '"h","t","n","Ready","i","l","0","x"'
            run.checked = 0
            self.assertEqual(run.poll(), 0)
            self.assertFalse(self.rd.manual_status()["running"])
            self.assertEqual(go({"tier": "weekly", "dry": True}), (200, {"ok": True}))   # التجربة مباشرة
            self.assertEqual(popen_args[-1][-1], "-DryRun")
            state["paused"] = True
            self.assertEqual(go({"tier": "daily", "dry": False}), (200, {"ok": True}))   # الموقوف: المهمة بلا -Force لن تعمل
            self.assertIn("-Force", popen_args[-1])
            state.update(paused=False, rc=1)
            self.assertEqual(go({"tier": "daily", "dry": False}), (200, {"ok": True}))   # فشل المهمة لا يُسقط الطلب
            self.assertEqual(len(popen_args), 3)

    def test_run_cleanup_confirm_engine_and_single_process(self):
        popen_args, invalidated = [], []

        class FakeProc:
            def __init__(self, args, creationflags=0):
                popen_args.append(list(args))
                self.done = False

            def poll(self):
                return 0 if self.done else None
        with patch.object(self.rd.subprocess, "Popen", FakeProc), patch.object(self.rd, "resolve_engine", lambda: ("engine.ps1", None)):
            self.assertEqual(self.rd.post("/api/run-cleanup", {"tier": "daily", "dry": 1}), (400, {"error": "dry must be boolean"}))
            self.assertEqual(self.rd.post("/api/run-cleanup", {"tier": "yearly"}), (400, {"error": "bad tier"}))
            code, rep = self.rd.post("/api/run-cleanup", {"tier": "daily", "dry": False})
            self.assertEqual((code, popen_args), (428, []))
            code, rep = self.rd.post("/api/run-cleanup", {"tier": "daily", "dry": False, "confirm": rep["confirm"]},
                                     invalidate=lambda: invalidated.append(1))
            self.assertEqual((code, rep, invalidated), (200, {"ok": True}, [1]))
            self.assertEqual(popen_args[0][-5:], ["-Tier", "daily", "-Force", "-PolicyPath", self.rd.POLICY])
            st = self.rd.manual_status()
            self.assertEqual((st["running"], st["tier"], st["dry"], bool(st["started"])), (True, "daily", False, True))
            self.assertEqual(self.rd.post("/api/run-cleanup", {"tier": "weekly"}), (409, {"error": "a manual cleanup is already running"}))
            self.rd.MANUAL["proc"].done = True
            self.assertEqual(self.rd.post("/api/run-cleanup", {"tier": "weekly"}), (200, {"ok": True}))   # التجربة تمرّ بلا 428
            self.assertEqual(popen_args[1][-1], "-DryRun")
        with patch.object(self.rd, "resolve_engine", lambda: (None, "لا محرّك")):
            self.rd.MANUAL["proc"] = None
            self.assertEqual(self.rd.post("/api/run-cleanup", {"tier": "daily"}), (503, {"error": "لا محرّك"}))


class MemTrim(unittest.TestCase):
    """البند 6/ب: memtrim.py بلا خادم ولا قاعدة: مسارا trim-auto وtrim-memory بعقودهما، 409 أثناء تنظيف جارٍ، العدّ التنازلي
    حين يكون المفتاح مطفأً، والمهمة المحمية الناقصة لا تسقط إلى المهمة القديمة أبدًا."""

    def setUp(self):
        import memtrim
        self.mt = memtrim
        self.mt.TRIM.update(running=False, err=None, ts=None)

    def test_trim_auto_route_validates_and_persists(self):
        saved = []
        with patch.object(self.mt, "set_trim_auto", lambda on: saved.append(on)):
            self.assertEqual(self.mt.post("/api/trim-auto", {"on": "yes"}), (400, {"error": "on must be boolean"}))
            self.assertEqual(self.mt.post("/api/trim-auto", {"on": True}), (200, {"ok": True, "auto": True}))
            self.assertEqual(self.mt.post("/api/trim-auto", {"on": False}), (200, {"ok": True, "auto": False}))
        self.assertEqual(saved, [True, False])
        self.assertIsNone(self.mt.post("/api/other", {}))
        self.assertEqual(self.mt.ROUTES, ("/api/trim-memory", "/api/trim-auto"))

    def test_trim_memory_route_starts_one_pass_and_refuses_overlap(self):
        started, invalidated = [], []

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=False):
                started.append((target.__name__, args))

            def start(self):
                pass
        with patch.object(self.mt.threading, "Thread", FakeThread):
            self.assertEqual(self.mt.post("/api/trim-memory", {}, invalidate=lambda: invalidated.append(1)), (200, {"ok": True}))
            self.assertEqual(self.mt.post("/api/trim-memory", {"mode": "deep"}), (200, {"ok": True}))
            self.mt.TRIM["running"] = True
            self.assertEqual(self.mt.post("/api/trim-memory", {}), (409, {"error": "trim already running"}))
        self.assertEqual(started, [("trim_memory", ("light",)), ("trim_memory", ("deep",))])
        self.assertEqual(invalidated, [1])   # الجولة الناجحة تعيد جمع حالة المهام كما كان

    def test_guard_next_is_idle_when_auto_is_off_or_trim_runs(self):
        with patch.object(self.mt, "auto_trim_on", lambda: False):
            self.assertEqual(self.mt.guard_next(), {"next": None, "period": self.mt.AUTO_PERIOD, "running": False, "auto": False})
            self.mt.TRIM["running"] = True
            self.assertEqual(self.mt.guard_next()["running"], True)

    def test_missing_protected_purge_never_falls_back(self):
        calls = []
        fake_secure = types.SimpleNamespace(protected_expected=lambda: True, installed=lambda: True,
                                            PURGE_TASKS={"light": "protected"}, PURGE_RESULT="result")
        with patch.object(self.mt, "secure", fake_secure), patch.object(self.mt, "PURGE_TASK", "legacy"), \
                patch.object(self.mt, "_schtasks", lambda args, timeout=10: (calls.append(args) or types.SimpleNamespace(returncode=1))):
            with self.assertRaisesRegex(RuntimeError, "protected purge task missing"):
                self.mt._purge_task("light")
        self.assertEqual(calls, [["/query", "/tn", "protected"]])

    def test_incomplete_protected_purge_fails_before_task_query(self):
        fake_secure = types.SimpleNamespace(protected_expected=lambda: True, installed=lambda: False, PURGE_TASKS={"light": "protected"})
        with patch.object(self.mt, "secure", fake_secure), patch.object(self.mt, "_schtasks", lambda *a, **k: self.fail("schtasks must not run")):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                self.mt._purge_task("light")

    def test_trim_memory_records_error_and_releases_running_flag(self):
        with patch.object(self.mt, "_purge_task", lambda mode: None), patch.object(self.mt.sched, "set_next", lambda *a: None):
            self.mt.trim_memory("light")
        self.assertEqual((self.mt.TRIM["running"], self.mt.TRIM["err"]), (False, "purge task not installed"))


class PolicyRoutes(unittest.TestCase):
    """البند 6/ج: routes_policy.py بلا خادم: مهام الوكيل ووضع الصيانة وبدء التشغيل والمهام المجدولة بقائمة tasks.json البيضاء،
    وتبديل القواعد والمستويات في ملف سياسة مؤقت مع نسخة احتياطية، وإعادة جمع الحالة بعد كل تعديل ناجح فقط."""

    def setUp(self):
        import routes_policy
        self.rp = routes_policy
        self.invalidated = []

    def post(self, route, body):
        return self.rp.post(route, body, invalidate=lambda: self.invalidated.append(route))

    def test_jobs_run_maintenance_and_startup(self):
        nxt = []
        with patch.object(self.rp.sched, "next_run", lambda name: {"next_run": 1} if name == "trim" else None), \
                patch.object(self.rp.sched, "set_next", lambda name, ts: nxt.append(name)):
            self.assertEqual(self.post("/api/jobs/run", {"name": "trim"}), (200, {"ok": True}))
            self.assertEqual(self.post("/api/jobs/run", {"name": "nope"}), (404, {"error": "unknown job"}))
        self.assertEqual(nxt, ["trim"])
        with patch.object(self.rp, "maintenance_set", lambda on: {"on": on, "rules": [], "since": 1}):
            self.assertEqual(self.post("/api/maintenance", {"on": "x"}), (400, {"error": "on must be boolean"}))
            self.assertEqual(self.post("/api/maintenance", {"on": True}), (200, {"on": True, "rules": [], "since": 1}))
        with patch.object(self.rp.startup_mgr, "toggle", lambda body: (400, {"error": "rejected"})):
            self.assertEqual(self.post("/api/startup", {}), (400, {"error": "rejected"}))
        self.assertIsNone(self.post("/api/other", {}))
        self.assertEqual(self.invalidated, [])   # هذه المسارات لا تعيد جمع حالة المهام
        self.assertEqual(self.rp.ROUTES, ("/api/rule", "/api/tier-pause", "/api/maintenance", "/api/task", "/api/startup", "/api/jobs/run"))

    def test_task_whitelist_and_admin_guard(self):
        ran = []
        wl = {("\\A", "t1"): {"canRun": True}, ("\\A", "admin"): {"needsAdmin": True}, ("\\A", "norun"): {"canRun": False}}
        with patch.object(self.rp, "whitelist", lambda: wl), patch.object(self.rp, "task_op", lambda op, path, name: ran.append((op, path, name))):
            self.assertEqual(self.post("/api/task", {"op": "run", "path": "\\A", "name": "zz"}), (400, {"error": "not allowed"}))
            self.assertEqual(self.post("/api/task", {"op": "delete", "path": "\\A", "name": "t1"}), (400, {"error": "not allowed"}))
            self.assertEqual(self.post("/api/task", {"op": "enable", "path": "\\A", "name": "admin"})[0], 400)
            self.assertEqual(self.post("/api/task", {"op": "run", "path": "\\A", "name": "norun"}), (400, {"error": "run not allowed for this task"}))
            self.assertEqual(self.post("/api/task", {"op": "run", "path": "\\A", "name": "t1"}), (200, {"ok": True}))
        self.assertEqual((ran, self.invalidated), ([("run", "\\A", "t1")], ["/api/task"]))

    def test_rule_and_tier_pause_edit_policy_file_with_backup(self):
        with tempfile.TemporaryDirectory(prefix="mw_backend_", dir=ROOT / "tests") as d:
            pol = Path(d) / "policy.json"
            pol.write_text(json.dumps({"rules": [{"id": "r1", "enabled": True}], "pausedTiers": ["weekly"]}), encoding="utf-8")
            logged = []
            fake_db = types.SimpleNamespace(_lock=threading.Lock(), conn=lambda: types.SimpleNamespace(execute=lambda q, p: logged.append(p), commit=lambda: None))
            with patch.object(self.rp, "POLICY", str(pol)), patch.object(self.rp.cfgjson, "ROOT", d), patch.object(self.rp, "db", fake_db):
                self.assertEqual(self.post("/api/rule", {"id": "r1", "enabled": "no"}), (400, {"error": "enabled must be boolean"}))
                self.assertEqual(self.post("/api/rule", {"id": "zz", "enabled": False}), (400, {"error": "unknown rule"}))
                self.assertEqual(self.post("/api/rule", {"id": "r1", "enabled": False}), (200, {"ok": True}))
                self.assertEqual(self.post("/api/tier-pause", {"tier": "yearly", "paused": True}), (400, {"error": "bad request"}))
                self.assertEqual(self.post("/api/tier-pause", {"tier": "daily", "paused": True}), (200, {"ok": True}))
                self.assertEqual(self.post("/api/tier-pause", {"tier": "weekly", "paused": False}), (200, {"ok": True}))
                state = self.rp.maintenance_state()
            data = json.loads(pol.read_text(encoding="utf-8"))
            self.assertEqual((data["rules"][0]["enabled"], data["pausedTiers"]), (False, ["daily"]))
            self.assertEqual(logged[0][1:3], ("rule", "r1"))
            self.assertEqual(self.invalidated, ["/api/rule", "/api/tier-pause", "/api/tier-pause"])
            self.assertEqual((state["on"], state["rules"]), (False, list(self.rp.MAINT_RULES)))
            self.assertTrue(list(Path(d).glob("backups/*policy.json")))   # نسخة احتياطية واحدة قبل أول كتابة


class SettingsRoutes(unittest.TestCase):
    """البند 6/د: routes_settings.py بلا خادم: المظهر والوضع (409 عند التعارض أو تعذّر الاسترداد، 400 للقيم غير الصالحة)،
    التنبيهات وخطة الطاقة عبر insight، وإعدادات reaper في ملف إعدادات مؤقت مع التحقق من المفاتيح والمدى والأسماء."""

    def setUp(self):
        import routes_settings
        self.rs = routes_settings
        self.invalidated = []

    def post(self, route, body):
        return self.rs.post(route, body, invalidate=lambda: self.invalidated.append(route))

    def test_appearance_and_theme_contracts(self):
        import appearance

        class Store:
            def __init__(self):
                self.mode = "auto"

            def update(self, body):
                if body.get("conflict"):
                    raise appearance.AppearanceConflict({"mode": "dark"})
                if body.get("down"):
                    raise appearance.AppearanceUnavailable()
                if body.get("bad"):
                    raise ValueError("bad palette")
                return {"mode": body.get("mode", "auto"), "palette": "teal"}

            def get(self):
                return {"mode": self.mode, "palette": "teal"}

            def set_mode(self, m):
                if m == "dark":
                    raise appearance.AppearanceUnavailable()
                self.mode = m
                return {"mode": m, "palette": "teal"}
        with patch.object(self.rs, "APPEARANCE", Store()):
            self.assertEqual(self.post("/api/appearance", {"conflict": 1}), (409, {"error": "appearance changed", "appearance": {"mode": "dark"}}))
            self.assertEqual(self.post("/api/appearance", {"down": 1}), (409, {"error": "appearance recovery required"}))
            self.assertEqual(self.post("/api/appearance", {"bad": 1}), (400, {"error": "bad palette"}))
            self.assertEqual(self.post("/api/appearance", {"mode": "light"}), (200, {"ok": True, "mode": "light", "palette": "teal"}))
            self.assertEqual(self.post("/api/theme", {"theme": "blue"}), (400, {"error": "bad theme"}))
            self.assertEqual(self.post("/api/theme", {"theme": "dark"}), (409, {"error": "appearance recovery required"}))
            self.assertEqual(self.post("/api/theme", {"theme": "light"}), (200, {"ok": True, "theme": "light", "appearance": {"mode": "light", "palette": "teal"}}))
            self.assertEqual(self.rs.get_theme(), "light")
        self.assertEqual(self.invalidated, [])
        self.assertIsNone(self.post("/api/other", {}))

    def test_alerts_and_power_plan_pass_through_insight(self):
        with patch.object(self.rs.insight, "alerts_set", lambda body: (None, "bad threshold") if body.get("x") else ({"mem": 1}, None)), \
                patch.object(self.rs.insight, "power_set", lambda guid: ([], "unknown plan") if guid == "zz" else ([{"guid": guid}], None)):
            self.assertEqual(self.post("/api/alerts", {"x": 1}), (400, {"error": "bad threshold"}))
            self.assertEqual(self.post("/api/alerts", {}), (200, {"cfg": {"mem": 1}}))
            self.assertEqual(self.post("/api/power-plan", {"guid": "zz"}), (400, {"error": "unknown plan"}))
            self.assertEqual(self.post("/api/power-plan", {"guid": "g1"}), (200, {"plans": [{"guid": "g1"}]}))

    def test_reaper_settings_validate_and_edit_config_file(self):
        with tempfile.TemporaryDirectory(prefix="mw_backend_", dir=ROOT / "tests") as d:
            cfg = Path(d) / "machinecare.config.json"
            cfg.write_text(json.dumps({"reaper": {"killBareClaudeShells": False, "protect": ["old\\.exe"]}}), encoding="utf-8")
            with patch.object(self.rs, "MCCFG", str(cfg)), patch.object(self.rs.cfgjson, "ROOT", d):
                self.assertEqual(self.post("/api/reaper-set", {"key": "other", "value": 100}), (400, {"error": "not allowed"}))
                self.assertEqual(self.post("/api/reaper-set", {"key": "bareClaudeShellMinutes", "value": True}), (400, {"error": "not allowed"}))
                self.assertEqual(self.post("/api/reaper-set", {"key": "bareClaudeShellMinutes", "value": 30}), (400, {"error": "not allowed"}))
                self.assertEqual(self.post("/api/reaper-set", {"key": "bareClaudeShellMinutes", "value": 120}), (200, {"bareMin": 120}))
                self.assertEqual(self.post("/api/reaper-protect", {"op": "add", "name": "bad;name"}), (400, {"error": "bad name"}))
                self.assertEqual(self.post("/api/reaper-protect", {"op": "add", "name": "node.exe"}), (200, {"protect": ["old\\.exe", "node\\.exe"]}))
                self.assertEqual(self.post("/api/reaper-protect", {"op": "add", "name": "NODE.exe"}), (200, {"protect": ["old\\.exe", "node\\.exe"]}))   # بلا تكرار
                self.assertEqual(self.post("/api/reaper-protect", {"op": "remove", "name": "old.exe"}), (200, {"protect": ["node\\.exe"]}))
                self.assertEqual(self.post("/api/reaper", {"key": "killBareClaudeShells", "value": "yes"}), (400, {"error": "not allowed"}))
                self.assertEqual(self.post("/api/reaper", {"key": "killBareClaudeShells", "value": True}), (200, {"ok": True}))
            data = json.loads(cfg.read_text(encoding="utf-8"))["reaper"]
            self.assertEqual((data["bareClaudeShellMinutes"], data["protect"], data["killBareClaudeShells"]), (120, ["node\\.exe"], True))
            self.assertEqual(self.invalidated, ["/api/reaper"])


class ReadRoutes(unittest.TestCase):
    """البند 6/ح: routes_read.py بلا خادم: جدول GET القرائية؛ 200 مع الحمولة، 400 bad params لمعاملات db/rows، 503 عند غياب homeapi،
    500 بالنصّ القديم لكل مجموعة (data unavailable / collect failed / str(e)) مع request_id وتسجيل السبب في infra.log،
    وNone لمسار ليس من هنا؛ وكل مسار GET كانت الواجهة تطلبه موجود في ROUTES."""

    def setUp(self):
        import routes_read
        self.rr = routes_read
        self.logged = []
        self.p = patch.object(routes_read.infra, "log", types.SimpleNamespace(exception=lambda *a: self.logged.append(a), info=lambda *a: None))
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_unknown_path_is_not_ours(self):
        self.assertIsNone(self.rr.get("/api/nope", {}))
        self.assertEqual(self.rr.get("/api/space-preview", {"kind": "never-started"}), (200, {"state": "none"}))
        self.assertEqual(self.rr.get("/api/space-clean", {"kind": "never-started"}), (200, {"state": "none"}))
        self.assertNotIn("/api/healthz", self.rr.ROUTES)   # بلا مصادقة: يبقى في الخادم

    def test_ok_payload_gets_200(self):
        with patch.object(self.rr.widgetctl, "status", lambda: {"running": True}):
            self.assertEqual(self.rr.get("/api/widget", {}), (200, {"running": True}))

    def test_bad_rows_params_400(self):
        with patch.object(self.rr.dbview, "rows", lambda *a: (_ for _ in ()).throw(ValueError("x"))):
            self.assertEqual(self.rr.get("/api/db/rows", {"table": "zz"}), (400, {"error": "bad params"}))
        self.assertEqual(self.logged, [])

    def test_group_errors_keep_their_text_and_log_cause(self):
        cases = [("/api/cpu", "api_payload", self.rr.cpuwatch, "cpu data unavailable"),
                 ("/api/procs", "procs_payload", self.rr.insight, "data unavailable"),
                 ("/api/history", "history", self.rr.db, "boom-history"),
                 ("/api/state", "state", self.rr.snapshot, "collect failed: boom-state")]
        for path, name, mod, text in cases:
            with self.subTest(path=path):
                self.logged.clear()
                with patch.object(mod, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom-" + path.rsplit("/", 1)[1]))):
                    code, out = self.rr.get(path, {})
                self.assertEqual(code, 500)
                self.assertEqual(out["error"], text)
                self.assertRegex(out["request_id"], r"^[0-9a-f]{12}$")
                self.assertEqual(len(self.logged), 1)
                self.assertIn(out["request_id"], self.logged[0][1:])

    def test_route_without_old_handler_now_answers_500(self):
        with patch.object(self.rr.widgetctl, "status", lambda: (_ for _ in ()).throw(RuntimeError("w"))):
            code, out = self.rr.get("/api/widget", {})
        self.assertEqual((code, out["error"]), (500, "internal error"))
        self.assertEqual(len(self.logged), 1)

    def test_home_without_module_503(self):
        with patch.object(self.rr, "homeapi", None):
            self.assertEqual(self.rr.get("/api/home", {}), (503, {"error": "home module unavailable"}))

    def test_page_get_routes_are_all_in_table(self):
        page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        called = set(re.findall(r'api\("(/api/[^"?]+)"', page)) | set(re.findall(r"api\(`(/api/[^`?]+)`", page))
        posted = set()
        for m in ("routes_destructive", "memtrim", "routes_policy", "routes_settings", "shellwin", "widgetctl", "gpupurge", "routes_app"):
            posted |= set(__import__(m).ROUTES)
        missing = sorted(p for p in called if p not in self.rr.ROUTES and p not in posted and p not in ("/api/ticket", "/api/session/logout"))   # ما بقي في الخادم: التذكرة والجلسة
        self.assertEqual(missing, [])


class IntegrityCache(unittest.TestCase):
    """quick_check مخزَّن (infra.integrity_cached): الطلب لا ينتظر؛ أول نداء يعيد pending ويطلق الفحص في خيط، ثم النتيجة تُعاد من الذاكرة
    حتى انقضاء TTL، والفشل يصير ok=False برسالة error؛ وoverview و/api/healthz يقرآن الذاكرة لا القاعدة."""

    def setUp(self):
        import contextlib
        import db
        import infra
        self.infra = infra
        self.saved = dict(infra._INTEG)
        infra._INTEG.update(t=0.0, ok=None, msg="pending", ms=None, running=False)
        self.addCleanup(lambda: infra._INTEG.update(self.saved))

        @contextlib.contextmanager
        def ro():
            yield None   # الاتصال لا يُستعمل: integrity مستبدلة
        self.p = patch.object(db, "ro", ro)
        self.p.start()
        self.addCleanup(self.p.stop)

    def _join(self):
        t = self.infra._integ_thread
        if t is not None:
            t.join(5)
            self.assertFalse(t.is_alive())

    def test_first_call_is_pending_then_cached(self):
        with patch.object(self.infra, "integrity", lambda c, full=False: (True, "ok")):
            first = self.infra.integrity_cached()
            self.assertEqual((first["ok"], first["msg"], first["running"]), (None, "pending", True))
            self._join()
            t1 = self.infra._integ_thread
            second = self.infra.integrity_cached()
            self.assertEqual((second["ok"], second["msg"], second["running"]), (True, "ok", False))
            self.assertIsInstance(second["ms"], int)
            self.assertGreater(second["t"], 0)
            self.assertIs(self.infra._integ_thread, t1)   # ضمن TTL: لا فحص جديد

    def test_stale_result_triggers_one_refresh(self):
        self.infra._INTEG.update(t=time.time() - 10**6, ok=True, msg="ok", running=False)
        calls, gate = [], threading.Event()

        def slow(c, full=False):
            calls.append(1)
            gate.wait(5)   # يبقى الفحص «جاريًا» حتى نتحقق من النداء الثاني
            return True, "ok"
        with patch.object(self.infra, "integrity", slow):
            a = self.infra.integrity_cached()
            b = self.infra.integrity_cached()   # الفحص جارٍ: لا خيط ثانٍ ولا انتظار
            gate.set()
            self._join()
        self.assertEqual((a["msg"], a["running"], b["running"], calls), ("ok", True, True, [1]))

    def test_failure_is_visible(self):
        with patch.object(self.infra, "integrity", lambda c, full=False: (_ for _ in ()).throw(RuntimeError("disk gone"))):
            self.infra.integrity_cached()
            self._join()
        out = self.infra.integrity_cached()
        self.assertEqual((out["ok"], out["msg"]), (False, "error: disk gone"))

    def test_overview_and_healthz_read_the_cache(self):
        import inspect
        src = inspect.getsource(self.infra.overview)
        self.assertIn("integrity_cached()", src)
        self.assertNotIn("integrity(c)", src)
        server = (ROOT / "server.py").read_text(encoding="utf-8-sig")
        self.assertIn('infra.integrity_cached(60)["ok"] is not False', server)
        self.assertNotIn("infra.integrity(rc)", server)
        page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('dbh.integrity === "pending" ? "قيد الفحص"', page)


class GpuPurge(unittest.TestCase):
    """gpupurge.py بلا خادم ولا قاعدة ولا nvidia-smi ولا شبكة: POST يبدأ مرورًا واحدًا في خيط ويرفض التداخل بـ 409، والمرور العام
    يقيس NVIDIA والمشتركة قبل وبعد ويُخرج نماذج LM Studio وOllama؛ dwm خطوة لا تجري إلا بطلب صريح وعبر المهمة الثابتة بعد مفتاح المالك؛
    GET يعرض الحالة ومفتاح dwm؛ الخطأ يُحفظ ويحرّر علم التشغيل."""

    def setUp(self):
        import gpupurge
        self.gp = gpupurge
        self.gp.STATE.update(running=False, ts=None, beforeMB=None, afterMB=None, freedMB=None, sharedBeforeMB=None, sharedAfterMB=None,
                             sharedFreedMB=None, dwmMB=None, dwm=False, steps=[], text=None, err=None)
        self.shared = iter([(9000, 8000), (8800, 7900)])
        self.ollama = {"ok": True, "text": "لا نموذج محمّل في Ollama."}
        self.patches = [patch.object(self.gp, "_shared", lambda: next(self.shared)), patch.object(self.gp, "_ollama_unload", lambda: self.ollama),
                        patch.object(self.gp.space, "get_elevated", lambda: {"dwm": True}), patch.object(self.gp.time, "sleep", lambda s: None),
                        patch.object(self.gp, "_today", lambda: (11604, 3)), patch.object(self.gp, "_last", lambda: {})]
        for p in self.patches:
            p.start()
        self.addCleanup(patch.stopall)

    def test_post_starts_one_pass_and_refuses_overlap(self):
        started, invalidated = [], []

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=False):
                started.append((target.__name__, args))

            def start(self):
                pass
        with patch.object(self.gp.threading, "Thread", FakeThread):
            self.assertIsNone(self.gp.post("/api/trim-memory", {}))
            self.assertEqual(self.gp.post("/api/gpu-purge", {}, invalidate=lambda: invalidated.append(1)), (200, {"ok": True}))
            self.assertTrue(self.gp.STATE["running"])
            self.assertEqual(self.gp.post("/api/gpu-purge", {"dwm": True}), (409, {"error": "gpu purge already running"}))
            self.gp.STATE["running"] = False
            self.assertEqual(self.gp.post("/api/gpu-purge", {"dwm": True}), (200, {"ok": True}))
        self.assertEqual(started, [("purge", (False,)), ("purge", (True,))])
        self.assertEqual(invalidated, [1])
        self.assertEqual(self.gp.ROUTES, ("/api/gpu-purge",))

    def test_purge_measures_all_cards_unloads_models_and_never_touches_dwm_by_default(self):
        reads, recorded, forgotten, tasks = iter([{"vram": 5120.0}, {"vram": 1536.4}]), [], [], []
        self.gp.STATE["running"] = True
        with patch.object(self.gp.gpuwatch, "live_info", lambda: next(reads)), \
                patch.object(self.gp.gpuwatch, "invalidate_live", lambda: forgotten.append(1)), \
                patch.object(self.gp.explain, "unload", lambda: {"ok": True, "text": "أُخرج 1 نموذج"}), \
                patch.object(self.gp.space, "run_fixed_task", lambda *a: tasks.append(a)), \
                patch.object(self.gp, "_record", lambda ok, detail: recorded.append((ok, detail))):
            self.gp.purge()
        v = self.gp.view()
        self.assertFalse(v["running"])
        self.assertEqual((v["beforeMB"], v["afterMB"], v["freedMB"], v["err"]), (5120.0, 1536.4, 3584, None))
        self.assertEqual((v["sharedBeforeMB"], v["sharedAfterMB"], v["sharedFreedMB"], v["dwmMB"], v["dwm"], v["dwmOn"]), (9000, 8800, 200, 7900, False, True))
        self.assertEqual([s["name"] for s in v["steps"]], ["LM Studio", "Ollama"])
        self.assertEqual(v["text"], "أُخرج 1 نموذج لا نموذج محمّل في Ollama.")
        self.assertEqual(tasks, [])   # dwm لا يُمسّ بلا طلب صريح
        self.assertEqual(forgotten, [1])   # القياس الثاني بعد نسيان القراءة المخزّنة، لا من الذاكرة
        self.assertEqual(recorded[0][0], True)
        self.assertIn("shared=9000->8800", recorded[0][1])
        self.assertEqual((v["todayFreedMB"], v["todayCount"]), (11604, 3))

    def test_day_total_sums_nvidia_and_shared_from_action_details(self):
        details = ["before=36.0 after=36.0 shared=13598->13574 dwm=0 LM Studio:x",
                   "before=36.0 after=37.0 shared=13578->2010 dwm=1 dwm:أُعيد تشغيل مدير سطح المكتب.",
                   "before=193.0 after=93.0 freed=100 LM Studio:قديم بلا مشتركة",
                   "before=None after=None shared=None->None dwm=0"]
        self.assertEqual(self.gp.day_total(details), (24 + 11568 + 100, 4))   # الزيادة في NVIDIA لا تُطرح، والقياس الغائب لا يُحسب
        self.assertEqual(self.gp.day_total([]), (0, 0))

    def test_view_restores_last_purge_from_log_after_server_restart(self):
        r = self.gp.restored(1793222147, "before=37.0 after=37.0 shared=2024->2012 dwm=0 LM Studio:x")
        self.assertEqual(r, {"ts": 1793222147, "dwm": False, "beforeMB": 37.0, "afterMB": 37.0, "freedMB": 0,
                             "sharedBeforeMB": 2024, "sharedAfterMB": 2012, "sharedFreedMB": 12})
        self.assertTrue(self.gp.restored(1, "before=36.0 after=37.0 shared=13578->2010 dwm=1 dwm:x")["dwm"])
        with patch.object(self.gp, "_last", lambda: r):
            self.assertEqual((self.gp.view()["ts"], self.gp.view()["sharedFreedMB"]), (1793222147, 12))   # الحالة الفارغة تُملأ من السجل
            self.gp.STATE.update(ts=5, sharedFreedMB=99)
            self.assertEqual((self.gp.view()["ts"], self.gp.view()["sharedFreedMB"]), (5, 99))   # الحالة الحية تغلب السجل

    def test_dwm_step_runs_only_through_the_fixed_task_after_the_owner_gate(self):
        tasks = []
        self.gp.STATE["running"] = True
        with patch.object(self.gp.gpuwatch, "live_info", lambda: None), \
                patch.object(self.gp.gpuwatch, "invalidate_live", lambda: None), \
                patch.object(self.gp.explain, "unload", lambda: {"ok": True, "text": "لا نموذج"}), \
                patch.object(self.gp.space, "elevated_gate", lambda k: None), \
                patch.object(self.gp.space, "run_fixed_task", lambda task, res, t: tasks.append((task, t)) or ({"ok": True, "state": "done"}, None)), \
                patch.object(self.gp, "_record", lambda ok, detail: None):
            self.gp.purge(True)
        v = self.gp.view()
        self.assertEqual(tasks, [("MonitorWidget-Gpu-Dwm", self.gp.DWM_TIMEOUT_S)])
        self.assertEqual((v["steps"][-1]["name"], v["steps"][-1]["ok"], v["dwm"], v["err"]), ("dwm", True, True, None))

    def test_dwm_step_refused_by_the_owner_switch_runs_nothing(self):
        tasks = []
        self.gp.STATE["running"] = True
        with patch.object(self.gp.gpuwatch, "live_info", lambda: None), \
                patch.object(self.gp.gpuwatch, "invalidate_live", lambda: None), \
                patch.object(self.gp.explain, "unload", lambda: {"ok": True, "text": "لا نموذج"}), \
                patch.object(self.gp.space, "elevated_gate", lambda k: {"ok": False, "error": "معطّل من الإعدادات"}), \
                patch.object(self.gp.space, "run_fixed_task", lambda *a: tasks.append(a)), \
                patch.object(self.gp, "_record", lambda ok, detail: None):
            self.gp.purge(True)
        v = self.gp.view()
        self.assertEqual(tasks, [])
        self.assertEqual((v["running"], v["err"]), (False, "معطّل من الإعدادات"))

    def test_purge_without_nvidia_smi_or_with_failure_keeps_state_consistent(self):
        recorded = []
        self.gp.STATE["running"] = True
        with patch.object(self.gp.gpuwatch, "live_info", lambda: None), \
                patch.object(self.gp.gpuwatch, "invalidate_live", lambda: None), \
                patch.object(self.gp.explain, "unload", lambda: {"ok": False, "err": "تعذّر التفريغ آلياً"}), \
                patch.object(self.gp, "_record", lambda ok, detail: recorded.append(ok)):
            self.gp.purge()
        v = self.gp.view()
        self.assertEqual((v["running"], v["beforeMB"], v["freedMB"], v["err"]), (False, None, None, "تعذّر التفريغ آلياً"))
        self.assertEqual(recorded, [False])

    def test_step_exception_is_stored_and_releases_running_flag(self):
        def boom():
            raise RuntimeError("lm studio down")
        self.gp.STATE["running"] = True
        with patch.object(self.gp.gpuwatch, "live_info", lambda: {"vram": 100.0}), \
                patch.object(self.gp.gpuwatch, "invalidate_live", lambda: None), \
                patch.object(self.gp.explain, "unload", boom), \
                patch.object(self.gp, "_record", lambda ok, detail: None):
            self.gp.purge()
        v = self.gp.view()
        self.assertFalse(v["running"])
        self.assertEqual(v["err"], "lm studio down")
        self.assertEqual(v["beforeMB"], 100.0)

    def test_ollama_unload_asks_each_loaded_model_to_leave_and_tolerates_no_service(self):
        import urllib.error

        class Resp:
            def __init__(self, body=b"{}"):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, *a):
                return self.body
        calls = []

        def fake(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            calls.append((url, None if isinstance(req, str) else req.data))
            return Resp(b'{"models": [{"name": "qwen3:8b"}, {"model": "llama3"}]}') if url.endswith("/api/ps") else Resp()
        self.patches[1].stop()   # الدالة الحقيقية هنا، والشبكة مستبدلة بالكامل
        with patch.object(self.gp.urllib.request, "urlopen", fake):
            r = self.gp._ollama_unload()
        self.assertEqual(r, {"ok": True, "text": "أُخرج 2 نموذج من Ollama."})
        self.assertEqual([json.loads(d) for u, d in calls if d], [{"model": "qwen3:8b", "keep_alive": 0}, {"model": "llama3", "keep_alive": 0}])

        def down(req, timeout=None):
            raise urllib.error.URLError("refused")
        with patch.object(self.gp.urllib.request, "urlopen", down):
            self.assertEqual(self.gp._ollama_unload(), {"ok": True, "text": "Ollama غير مشغّل."})


class GpuMiniView(unittest.TestCase):
    """لقطة الرسوميات للويدجت (/api/mini -> gpu) لا تنتظر nvidia-smi: تُعيد المخزّن فورًا، وتطلب تحديثًا واحدًا في خيط حين يقدُم
    (5 ث) ولا تكرّره أثناء جريانه، وتُخفي القراءة الأقدم من دقيقة، والتحديث يحرّر علمه ولو فشل."""

    def setUp(self):
        import gpuwatch
        self.gw = gpuwatch
        self.saved = (dict(gpuwatch._cache), dict(gpuwatch._mini_busy))
        self.started = []
        started = self.started

        class FakeThread:
            def __init__(self, target=None, daemon=False):
                started.append(target.__name__)

            def start(self):
                pass
        self.fake = FakeThread

    def tearDown(self):
        self.gw._cache.clear()
        self.gw._cache.update(self.saved[0])
        self.gw._mini_busy.clear()
        self.gw._mini_busy.update(self.saved[1])

    def view(self, now):
        with patch.object(self.gw.threading, "Thread", self.fake), patch.object(self.gw.time, "time", lambda: now):
            return self.gw.mini_view()

    def test_empty_cache_answers_none_and_starts_one_refresh(self):
        self.gw._cache.update(static=(0.0, None), live=(0.0, None))
        self.gw._mini_busy["on"] = False
        self.assertIsNone(self.view(1000.0))
        self.assertIsNone(self.view(1000.5))          # التحديث الأول ما زال جاريًا: لا خيط ثانٍ
        self.assertEqual(self.started, ["_mini_refresh"])

    def test_fresh_cache_is_returned_without_refresh(self):
        live = {"temp": 53.0, "util": 26.0, "power": 3.87, "vram": 54.0, "vramTotal": 8188.0}
        static = {"name": "RTX", "powerMax": 140.0, "powerLimit": None, "vramTotal": 8188.0}
        self.gw._cache.update(static=(990.0, static), live=(998.0, live))
        self.gw._mini_busy["on"] = False
        v = self.view(1000.0)
        self.assertEqual(self.started, [])
        self.assertEqual(v, {"age": 2, "name": "RTX", "temp": 53.0, "util": 26.0, "power": 3.87, "powerMax": 140.0,
                             "vram": 54.0, "vramTotal": 8188.0})

    def test_old_reading_refreshes_and_very_old_is_hidden(self):
        self.gw._cache.update(static=(990.0, {"name": "RTX"}), live=(990.0, {"temp": 50.0}))
        self.gw._mini_busy["on"] = False
        self.assertEqual(self.view(1000.0)["temp"], 50.0)      # 10 ث: تُعرض ويُطلب تحديث
        self.assertEqual(self.started, ["_mini_refresh"])
        self.gw._mini_busy["on"] = False
        self.assertIsNone(self.view(1061.0))                   # أقدم من دقيقة: لا تُعرض

    def test_refresh_releases_busy_flag_on_failure(self):
        def boom():
            raise RuntimeError("nvidia-smi gone")
        self.gw._mini_busy["on"] = True
        with patch.object(self.gw, "static_info", boom), patch.object(self.gw.errlog, "swallowed", lambda: None):
            self.gw._mini_refresh()
        self.assertFalse(self.gw._mini_busy["on"])


class SpacePreviewJob(unittest.TestCase):
    """معاينة المساحة مهمة خلفية: البدء فوري ولا يكرّر الخيط لمعاينة جارية، التقدّم يُقرأ أثناء القياس، نوع غير معروف يُرفض بلا خيط،
    والنتيجة الحديثة (أقل من PREVIEW_TTL) تغني فحص التأكيد عن قياس ثانٍ متزامن، والقديمة لا."""

    def setUp(self):
        import space
        self.sp = space
        space._pv.clear()
        self.addCleanup(space._pv.clear)

    def wait(self, cond, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.01)
        self.fail("timed out")

    def test_background_preview_progress_result_and_reuse(self):
        gate, calls = threading.Event(), []

        def fake_impl(kind, dry=True, src="manual", early=False, prog=None):
            calls.append((kind, dry, prog is not None))
            if prog is not None:
                prog["dirs"], prog["files"], prog["mb"] = 9000, 400, 7.5
                gate.wait(5)
            return {"ok": True, "dry": dry, "mb": 12.0, "files": 401}
        with patch.object(self.sp, "_clean_impl", fake_impl):
            t0 = time.time()
            r = self.sp.preview_start("temp")
            self.assertLess(time.time() - t0, 0.3)
            self.assertTrue(r["ok"])
            self.assertEqual(r["state"], "running")
            self.wait(lambda: self.sp.preview_status("temp")["files"] == 400)
            self.assertEqual(self.sp.preview_status("temp")["dirs"], 9000)
            self.assertEqual(self.sp.preview_status("temp")["mb"], 7.5)
            self.assertEqual(self.sp.preview_start("temp")["state"], "running")   # معاينة جارية تُعاد كما هي
            self.assertEqual(len(calls), 1)
            self.assertIsNone(self.sp.fresh_preview_mb("temp"))                   # لا نتيجة قبل الانتهاء
            for bad in ("nope", ["temp"], None):
                self.assertEqual(self.sp.preview_start(bad)["ok"], False)
            self.assertEqual(set(self.sp._pv), {"temp"})
            gate.set()
            self.wait(lambda: self.sp.preview_status("temp")["state"] == "done")
            st = self.sp.preview_status("temp")
            self.assertEqual(st["res"]["mb"], 12.0)
            self.assertEqual(self.sp.dry_mb("temp"), 12.0)                        # حديثة: بلا قياس ثانٍ
            self.assertEqual(len(calls), 1)
            self.sp._pv["temp"]["ended"] -= self.sp.PREVIEW_TTL + 1
            self.assertIsNone(self.sp.fresh_preview_mb("temp"))
            self.assertEqual(self.sp.dry_mb("temp"), 12.0)                        # قديمة: قياس متزامن جديد
            self.assertEqual(calls[-1], ("temp", True, False))

    def test_real_temp_walk_reports_progress(self):
        with tempfile.TemporaryDirectory() as d:
            old = time.time() - 3 * 86400
            for i in range(450):
                os.makedirs(os.path.join(d, "e%d" % (i % 3)), exist_ok=True)   # مجلدات فارغة تُعدّ أيضًا
                f = os.path.join(d, "f%03d.tmp" % i)
                with open(f, "wb") as fh:
                    fh.write(b"x" * 1024)
                os.utime(f, (old, old))
            open(os.path.join(d, "fresh.tmp"), "wb").close()   # أحدث من يوم: خارج المعاينة
            prog = {"dirs": 0, "files": 0, "mb": 0.0}
            with patch.object(self.sp, "TEMP", d):
                r = self.sp._clean_impl("temp", True, prog=prog)
            self.assertEqual((r["ok"], r["dry"], r["files"], r["skipped"]), (True, True, 450, 0))
            self.assertEqual((prog["dirs"], prog["files"], prog["mb"]), (4, 450, 0.4))   # الجذر وثلاثة فارغة؛ كل ملف مرشّح
            self.assertEqual(len(os.listdir(d)), 454)                             # القياس الجاف لا يحذف شيئًا ولا المجلدات الفارغة

    def test_failed_preview_reports_error(self):
        def boom(*a, **k):
            raise OSError("disk gone")
        with patch.object(self.sp, "_clean_impl", boom), patch.object(self.sp.errlog, "swallowed", lambda *a, **k: None):
            self.sp.preview_start("npmcache")
            self.wait(lambda: self.sp.preview_status("npmcache")["state"] == "error")
            self.assertIn("disk gone", self.sp.preview_status("npmcache")["error"])
            self.assertIsNone(self.sp.fresh_preview_mb("npmcache"))


class SpaceCleanJob(unittest.TestCase):
    """التنظيف الفعلي مهمة خلفية: البدء فوري، والقياس يسبق الحذف فيُرفض نموّ المرشّحين فوق التسامح بلا حذف، والمعاينة الحديثة تغني عن القياس،
    والتوقّع المقاس يُمرَّر فلا يُقاس مرتين، ومهمة تنظيف واحدة في كل وقت، والحذف الحقيقي يُبلغ التقدّم. لا قاعدة حية ولا مسح لاحق."""

    wait = SpacePreviewJob.wait

    def setUp(self):
        import space
        self.sp = space
        for d in (space._cj, space._pv):
            d.clear()
            self.addCleanup(d.clear)

        def no_db():
            raise RuntimeError("no database in tests")
        for obj, name, fake in ((space, "_c", no_db), (space, "scan", lambda *a, **k: None), (space, "_setting", lambda k, d=None: d),
                                (space.errlog, "swallowed", lambda *a, **k: None)):
            pt = patch.object(obj, name, fake)
            pt.start()
            self.addCleanup(pt.stop)

    def fake_impl(self, sizes, calls, gate=None):
        def impl(kind, dry=True, src="manual", early=False, prog=None):
            calls.append((kind, dry))
            if prog is not None:
                prog["dirs"], prog["files"], prog["mb"] = 50, 3, 1.5
                if gate is not None and not dry:
                    gate.wait(5)
            return {"ok": True, "dry": dry, "mb": sizes[kind], "files": 3, "skipped": 0}
        return impl

    def test_single_clean_measures_once_then_deletes_in_background(self):
        gate, calls = threading.Event(), []
        with patch.object(self.sp, "_clean_impl", self.fake_impl({"temp": 12.0}, calls, gate)):
            t0 = time.time()
            r = self.sp.clean_start("temp", 12)
            self.assertLess(time.time() - t0, 0.3)
            self.assertEqual((r["ok"], r["state"]), (True, "running"))
            self.wait(lambda: self.sp.clean_status("temp")["phase"] == "clean" and self.sp.clean_status("temp")["files"] == 3)
            self.assertEqual(self.sp.clean_start("temp", 12)["state"], "running")      # المهمة نفسها تُعاد كما هي
            busy = self.sp.clean_start("all", 12)
            self.assertEqual((busy["ok"], busy["error"], busy["running"]), (False, "clean already running", "temp"))
            gate.set()
            self.wait(lambda: self.sp.clean_status("temp")["state"] == "done")
            st = self.sp.clean_status("temp")
        self.assertEqual((st["res"]["kind"], st["res"]["mb"], st["res"]["expectedMB"]), ("temp", 12.0, 12.0))
        self.assertEqual(calls, [("temp", True), ("temp", False)])                      # قياس واحد ثم حذف واحد

    def test_fresh_preview_stands_in_for_the_check(self):
        calls, now = [], time.time()
        self.sp._pv["temp"] = {"state": "done", "started": now, "ended": now, "dirs": 0, "files": 0, "mb": 9.0, "res": {"ok": True, "mb": 9.0}}
        with patch.object(self.sp, "_clean_impl", self.fake_impl({"temp": 9.0}, calls)):
            self.sp.clean_start("temp", 9)
            self.wait(lambda: self.sp.clean_status("temp")["state"] == "done")
        self.assertEqual(calls, [("temp", False)])
        self.assertNotIn("temp", self.sp._pv)                                           # المعاينة لم تعد تصف المجلد

    def test_grown_candidates_refused_before_any_delete(self):
        calls = []
        with patch.object(self.sp, "_clean_impl", self.fake_impl({"temp": 100.0, "edgecache": 100.0}, calls)), \
                patch.object(self.sp, "BULK", ("temp", "edgecache")):
            self.sp.clean_start("all", 50)                                              # رأى 50 والآن 200 > 50*1.25+50
            self.wait(lambda: self.sp.clean_status("all")["state"] == "error")
            st = self.sp.clean_status("all")
        self.assertEqual((st["error"], st["now_mb"]), ("candidates_changed", 200.0))
        self.assertEqual(calls, [("temp", True), ("edgecache", True)])

    def test_clean_all_sums_parts_and_bad_keys_start_nothing(self):
        calls = []
        with patch.object(self.sp, "_clean_impl", self.fake_impl({"temp": 10.0, "edgecache": 5.5}, calls)), \
                patch.object(self.sp, "BULK", ("temp", "edgecache")):
            self.sp.clean_start("all", 15)
            self.wait(lambda: self.sp.clean_status("all")["state"] == "done")
            res = self.sp.clean_status("all")["res"]
        self.assertEqual((res["ok"], res["mb"], [x["kind"] for x in res["parts"]]), (True, 15.5, ["temp", "edgecache"]))
        self.assertEqual(calls, [("temp", True), ("edgecache", True), ("temp", False), ("edgecache", False)])
        self.sp._cj.clear()
        for bad in ("nope", None, ["temp"]):
            self.assertEqual(self.sp.clean_start(bad, 1)["ok"], False)
        self.assertEqual(self.sp._cj, {})
        self.assertEqual(self.sp.clean_status("never"), {"state": "none"})

    def test_real_temp_delete_reports_progress(self):
        with tempfile.TemporaryDirectory() as d:
            old = time.time() - 3 * 86400
            for i in range(30):
                sub = os.path.join(d, "e%d" % (i % 3))
                os.makedirs(sub, exist_ok=True)
                f = os.path.join(sub, "f%02d.tmp" % i)
                with open(f, "wb") as fh:
                    fh.write(b"x" * 1024)
                os.utime(f, (old, old))
            keep = os.path.join(d, "fresh.tmp")
            open(keep, "wb").close()                                                    # أحدث من يوم: يبقى
            prog = {"dirs": 0, "files": 0, "mb": 0.0}
            with patch.object(self.sp, "TEMP", d):
                r = self.sp.clean("temp", dry=False, prog=prog, exp=0.03)
            self.assertEqual((r["ok"], r["dry"], r["files"], r["expectedMB"]), (True, False, 30, 0.03))
            self.assertEqual((prog["dirs"], prog["files"]), (4, 30))                     # الجذر وثلاثة مجلدات؛ كل ملف عولج
            self.assertEqual([f for _, _, fs in os.walk(d) for f in fs], ["fresh.tmp"])

    def test_real_temp_clean_removes_old_empty_folders_only(self):
        with tempfile.TemporaryDirectory() as d:
            old = time.time() - 3 * 86400
            for rel in ("chain/a/b/c", "oldfiles", "busy", "newempty", "Low", "parent/newchild"):
                os.makedirs(os.path.join(d, *rel.split("/")))
            for rel, t in (("oldfiles/x.tmp", old), ("busy/fresh.tmp", None)):
                p = os.path.join(d, *rel.split("/"))
                with open(p, "wb") as fh:
                    fh.write(b"x" * 512)
                if t:
                    os.utime(p, (t, t))
            for rel in ("chain/a/b/c", "chain/a/b", "chain/a", "chain", "oldfiles", "busy", "Low", "parent"):   # الأعمق أولًا
                os.utime(os.path.join(d, *rel.split("/")), (old, old))
            prog = {"dirs": 0, "files": 0, "mb": 0.0}
            with patch.object(self.sp, "TEMP", d):
                r = self.sp.clean("temp", dry=False, prog=prog, exp=0.0)
                dry = self.sp.clean("temp", dry=True)
            left = sorted(os.path.relpath(os.path.join(dp, x), d).replace(os.sep, "/") for dp, dn, _ in os.walk(d) for x in dn)
            # السلسلة كلها ومجلد الملفات القديمة (عُمره قيس قبل حذف ملفه)؛ ويبقى المشغول والحديث وLow والأب الذي فيه ابن حديث
            self.assertEqual((r["files"], r["emptyDirs"], prog["emptyDirs"]), (1, 5, 5))
            self.assertEqual(left, ["Low", "busy", "newempty", "parent", "parent/newchild"])
            self.assertTrue(os.path.isdir(d))
            self.assertEqual(dry["emptyDirs"], 0)                                       # المعاينة لا تحذف شيئًا


if __name__ == "__main__":
    unittest.main(verbosity=2)
