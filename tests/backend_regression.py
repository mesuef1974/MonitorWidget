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
              MAX_SESSIONS=128, _SESSIONS={}, _SLOCK=threading.Lock(), _cache={"t": 1},
              POST_MODULES=(), _after_mutation=lambda: None)   # وحدات المسارات (البند 6) تُختبر وحدها؛ هنا لا مسار منها
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
    """حالة الغلاف في /api/health: الجاهزية وسببها ومحرك آخر فتح، بلا تشغيل الخادم (ملف حالة مؤقت ومجلد جذر مؤقت)."""

    def _ns(self, root, localappdata):
        tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8-sig"))
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
        """المسارات في server.py والواجهة تبلّغ عند الانتقال وتعرض الملخص في صفحة الإعدادات والتحكم؛ وثابت الفحص المرئي موجود."""
        server = (ROOT / "server.py").read_text(encoding="utf-8-sig")
        page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for needle in ('"/api/page-use"', "pageuse.stats(db.conn())", '"/api/page-open"', "pageuse.record(db.conn(), body)"):
            self.assertIn(needle, server)
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

        def fake_clean(kind, dry=True, src="manual", early=False):
            calls.append((kind, dry, src, early))
            return {"mb": 10.0, "kind": kind, "dry": dry}
        with patch.object(self.rd.space, "clean", fake_clean):
            self.assertEqual(self.rd.post("/api/space", {"action": "clean", "kind": "temp"}), (400, {"error": "seen_mb required"}))
            self.assertEqual(self.rd.post("/api/space", {"action": "clean", "kind": "temp", "seen_mb": True})[0], 400)
            body = {"action": "clean", "kind": "temp", "seen_mb": 10}
            code, rep = self.rd.post("/api/space", body)
            self.assertEqual((code, rep["error"], rep["ttl"]), (428, "confirm_required", 120))
            self.assertTrue(rep["confirm"])
            self.assertEqual(calls, [])                                                 # لا معاينة ولا تنظيف قبل الرمز
            code2, rep2 = self.rd.post("/api/space", {**body, "seen_mb": 11, "confirm": rep["confirm"]})   # معاملات مختلفة
            self.assertEqual(code2, 428)
            self.assertNotEqual(rep2["confirm"], rep["confirm"])
            code3, rep3 = self.rd.post("/api/space", {**body, "confirm": "bogus"})
            self.assertEqual(code3, 428)
            code4, rep4 = self.rd.post("/api/space", {**body, "confirm": rep3["confirm"]})
            self.assertEqual((code4, rep4["dry"]), (200, False))
            self.assertEqual(calls, [("temp", True, "manual", False), ("temp", False, "manual", False)])   # تجربة بعد الرمز ثم التنفيذ
            self.assertEqual(self.rd.post("/api/space", {**body, "confirm": rep3["confirm"]})[0], 428)    # الرمز استُهلك

    def test_space_cleanall_refuses_grown_candidates(self):
        with patch.object(self.rd.space, "clean", lambda kind, dry=True, src="manual", early=False: {"mb": 100.0}), \
                patch.object(self.rd.space, "BULK", ("temp", "edgecache")):
            body = {"action": "cleanall", "seen_mb": 50}                                # المعاينة رأت 50 والآن 200 > 50*1.25+50
            code, rep = self.rd.post("/api/space", body)
            self.assertEqual(code, 428)
            code, rep = self.rd.post("/api/space", {**body, "confirm": rep["confirm"]})
            self.assertEqual((code, rep["error"], rep["now_mb"]), (409, "candidates_changed", 200.0))

    def test_space_safe_actions_need_no_confirm(self):
        with patch.object(self.rd.space, "clean", lambda kind, dry=True, src="manual", early=False: {"mb": 1.0, "dry": dry}):
            self.assertEqual(self.rd.post("/api/space", {"action": "preview", "kind": "temp"}), (200, {"mb": 1.0, "dry": True}))
        self.assertEqual(self.rd.post("/api/space", {"action": "nope"}), (400, {"error": "bad action"}))
        self.assertIsNone(self.rd.post("/api/other", {}))
        self.assertEqual(self.rd.ROUTES, ("/api/space", "/api/reaper-run", "/api/run-cleanup"))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
