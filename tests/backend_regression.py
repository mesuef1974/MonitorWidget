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
              "_validate_json_numbers", "Handler", "LocalHTTPServer", "_purge_task", "_resolve_engine"}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in wanted]
    ns = dict(json=json, math=math, socket=socket, time=time, secrets=secrets, hashlib=hashlib,
              hmac=hmac, os=os, threading=threading, BaseHTTPRequestHandler=BaseHTTPRequestHandler,
              ThreadingHTTPServer=ThreadingHTTPServer, PORT=47832, MAX_POST_BYTES=65536,
              MAX_HTTP_THREADS=2, REQUEST_TIMEOUT_SECONDS=15, SESSION_TTL=60,
              MAX_SESSIONS=128, _SESSIONS={}, _SLOCK=threading.Lock(), _cache={"t": 1})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "extracted-server", "exec"), ns)
    return ns


class BackendRequests(unittest.TestCase):
    def setUp(self):
        self.ns = extracted()
        self.sends = []
        self.ns["startup_mgr"] = types.SimpleNamespace(toggle=lambda body: (400, {"error": "rejected"}))
        self.ns["infra"] = types.SimpleNamespace(log=types.SimpleNamespace(exception=lambda *args: None))

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
        for seen in (float("nan"), float("inf"), float("-inf"), 10**1000, True, -1):
            with self.subTest(seen=str(seen)[:20]):
                self.sends.clear()
                fake = types.SimpleNamespace(_send=lambda *a: self.sends.append(a))
                ok = self.ns["Handler"]._space_confirmed(fake, "/api/space", {"seen_mb": seen}, "clean", "x")
                self.assertFalse(ok)
                self.assertEqual(self.sends[0][0], 400)

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
        with tempfile.TemporaryDirectory(prefix="mw_backend_", dir=ROOT / "tests") as d:
            self.ns.update(PROT_ENGINE_DIR=d, IS_ADMIN=False, ENGINE="legacy")
            path, error = self.ns["_resolve_engine"]()
            self.assertIsNone(path)
            self.assertTrue(error)

    def test_missing_protected_purge_never_falls_back(self):
        self.ns["secure"] = types.SimpleNamespace(protected_expected=lambda: True,
            installed=lambda: True, PURGE_TASKS={"light": "protected"}, PURGE_RESULT="result")
        self.ns["PURGE_TASK"] = "legacy"
        calls = []
        self.ns["_schtasks"] = lambda args: (calls.append(args) or types.SimpleNamespace(returncode=1))
        with self.assertRaisesRegex(RuntimeError, "protected purge task missing"):
            self.ns["_purge_task"]("light")
        self.assertEqual(calls, [["/query", "/tn", "protected"]])

    def test_incomplete_protected_purge_fails_before_task_query(self):
        self.ns["secure"] = types.SimpleNamespace(protected_expected=lambda: True,
            installed=lambda: False, PURGE_TASKS={"light": "protected"})
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.ns["_purge_task"]("light")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
