"""Regression tests for browser CSP normalization and existing-server reopening.

No production imports, processes, windows, tickets or network requests are used.
"""
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import types
import unittest
from unittest.mock import patch
import urllib.request
import ctypes
import ctypes.wintypes
import os

ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "server.py").read_text(encoding="utf-8-sig"))
TREE_SHELL = ast.parse((ROOT / "shellwin.py").read_text(encoding="utf-8-sig"))   # البند 6/هـ: فتح النافذة والتذكرة في shellwin.py
TREE_WIDGET = ast.parse((ROOT / "widgetctl.py").read_text(encoding="utf-8-sig"))   # البند 6/و: الويدجت في widgetctl.py


def functions(names, tree=TREE):
    return ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[])


class WindowSecurity(unittest.TestCase):
    def test_csp_hash_uses_html_parser_normalized_newlines(self):
        namespace = {"re":re, "hashlib":hashlib,"base64":base64,"_csp_cache":{},"_CSP_BASE":"default-src 'none'"}
        exec(compile(functions({"_page_csp"}), "page-csp", "exec"), namespace)
        source = b"const a = 1;\nconst b = 2;\n"
        expected = "'sha256-" + base64.b64encode(hashlib.sha256(source).digest()).decode() + "'"
        for body in (source, source.replace(b"\n",b"\r\n"),source.replace(b"\n",b"\r")):
            policy = namespace["_page_csp"](b"<script>"+body+b"</script>")
            self.assertIn(expected,policy)
            self.assertNotIn("unsafe-inline",policy)

    def test_actual_html_matches_normalized_hash(self):
        namespace = {"re":re, "hashlib":hashlib,"base64":base64,"_csp_cache":{},"_CSP_BASE":"default-src 'none'"}
        exec(compile(functions({"_page_csp"}),"actual-page-csp","exec"),namespace)
        raw = (ROOT / "web" / "index.html").read_bytes()
        policy = namespace["_page_csp"](raw)
        for body in re.findall(rb"<script>(.*?)</script>",raw,re.S):
            normalized=body.replace(b"\r\n",b"\n").replace(b"\r",b"\n")
            self.assertIn("'sha256-"+base64.b64encode(hashlib.sha256(normalized).digest()).decode()+"'",policy)

    def test_reopen_requests_ticket_from_running_server_not_helper_memory(self):
        namespace={"PORT":47832,"TOKEN":"synthetic-test-token","json":json}
        exec(compile(functions({"_ticket_for_window"}, TREE_SHELL),"window-ticket","exec"),namespace)
        received=[]
        def open_request(request,timeout):
            received.append((request,timeout))
            return io.BytesIO(b'{"ticket":"issued-by-running-server"}')
        opener=types.SimpleNamespace(open=open_request)
        with patch.object(urllib.request,"build_opener",return_value=opener):
            self.assertEqual(namespace["_ticket_for_window"](namespace["TOKEN"]),"issued-by-running-server")
        request,timeout=received[0]
        self.assertEqual(request.full_url,"http://127.0.0.1:47832/api/ticket")
        self.assertEqual(request.get_method(),"POST")
        self.assertEqual(request.get_header("X-token"),"synthetic-test-token")
        self.assertEqual(timeout,10)

    def test_ticket_failure_never_opens_unauthenticated_page(self):
        namespace={"PORT":47832,"TOKEN":"synthetic","json":json}
        exec(compile(functions({"_ticket_for_window"}, TREE_SHELL),"window-ticket","exec"),namespace)
        opener=types.SimpleNamespace(open=lambda *a,**k:io.BytesIO(b'{}'))
        with patch.object(urllib.request,"build_opener",return_value=opener):
            with self.assertRaises(RuntimeError):namespace["_ticket_for_window"](namespace["TOKEN"])

    def test_open_placement_survives_short_lived_helper(self):
        function=next(n for n in TREE_SHELL.body if isinstance(n,ast.FunctionDef) and n.name=="open_window")
        workers=[n for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="Thread"]
        self.assertTrue(any(any(k.arg=="daemon" and isinstance(k.value,ast.Constant) and k.value.value is False for k in n.keywords) for n in workers))


class WidgetProcessScope(unittest.TestCase):
    def namespace(self, run=None):
        ns = dict(ctypes=ctypes, ROOT=r"C:\Users\Owner's Folder\MonitorWidget", json=json,
                  os=os, PWSH="pwsh", CNW=0,
                  sched=types.SimpleNamespace(remaining_timeout=lambda n:n),
                  subprocess=types.SimpleNamespace(run=run))
        exec(compile(functions({"_widget_process_matches", "widget_stop"}, TREE_WIDGET), "widget-scope", "exec"), ns)
        return ns

    def test_real_windows_argv_selects_only_checkout_file(self):
        match = self.namespace()["_widget_process_matches"]
        target = r"C:\Users\Owner's Folder\MonitorWidget\widget.ps1"
        for command in (f'"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File "{target}"',
                        f'pwsh.exe -File "{target.upper()}"', f'powershell.exe -NoProfile -File "{target}"'):
            with self.subTest(command=command):
                self.assertTrue(match(dict(Name="pwsh.exe", CommandLine=command)))
        bad = [f'pwsh.exe -Command "Write-Host -File {target}"',
               f'pwsh.exe -co Write-Output -File "{target}"',
               f'pwsh.exe -File "C:\\OtherProject\\widget.ps1" -File "{target}"',
               'pwsh.exe -File widget.ps1', 'pwsh.exe -EncodedCommand ignored',
               f'pwsh.exe -File "{target}.backup"']
        for command in bad:
            with self.subTest(command=command):
                self.assertFalse(match(dict(Name="pwsh.exe", CommandLine=command)))
        self.assertFalse(match(dict(Name="python.exe", CommandLine=f'python.exe -File "{target}"')))
        self.assertFalse(match(dict(Name="pwsh.exe", CommandLine=None)))

    def test_stop_uses_validated_pid_creation_time_and_no_path_interpolation(self):
        calls = []
        rows = [dict(Name="pwsh.exe", ProcessId=42, CreationTicks="123456789",
                     CommandLine='pwsh.exe -NoProfile -File "C:\\Users\\Owner\'s Folder\\MonitorWidget\\widget.ps1"'),
                dict(Name="pwsh.exe", ProcessId=43, CreationTicks="123456790",
                     CommandLine='pwsh.exe -File "C:\\OtherProject\\widget.ps1"')]
        def run(args, **kw):
            calls.append(args)
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(rows).encode() if len(calls)==1 else b"", stderr=b"")
        self.namespace(run)["widget_stop"]()
        self.assertEqual(len(calls), 2)
        stop = calls[1][-1]
        self.assertIn("ProcessId = 42", stop)
        self.assertIn("123456789", stop)
        self.assertNotIn("43", stop)
        self.assertNotIn("Owner", stop)
        self.assertNotIn("widget.ps1", stop)
        self.assertNotIn("-like", calls[0][-1])

    def test_query_and_kill_failures_are_not_silenced(self):
        def failed(*args, **kw):
            return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"private-process-command")
        with self.assertRaisesRegex(RuntimeError, "^widget process query failed$"):
            self.namespace(failed)["widget_stop"]()
        rows = [dict(Name="pwsh.exe", ProcessId=42, CreationTicks="123456789",
                     CommandLine='pwsh.exe -File "C:\\Users\\Owner\'s Folder\\MonitorWidget\\widget.ps1"')]
        count = [0]
        def kill_failed(*args, **kw):
            count[0] += 1
            return types.SimpleNamespace(returncode=0 if count[0]==1 else 1, stdout=json.dumps(rows).encode(), stderr=b"private")
        with self.assertRaisesRegex(RuntimeError, "^widget stop failed$"):
            self.namespace(kill_failed)["widget_stop"]()
        empty = lambda *a,**k:types.SimpleNamespace(returncode=0, stdout=b"[]", stderr=b"")
        self.namespace(empty)["widget_stop"]()


class DashboardPlacementScope(unittest.TestCase):
    def run_placement(self, windows, denied=()):
        class Call:
            def __init__(self, fn): self.fn = fn
            def __call__(self, *args): return self.fn(*args)
        events, closed, queried = [], [], []
        def text(hwnd, buf, size):
            buf.value = windows[hwnd][0]
            return len(buf.value)
        def pid(hwnd, out):
            out._obj.value = hwnd + 100
            return 1
        def image(handle, flags, buf, size):
            queried.append(handle)
            buf.value = windows[handle - 100][1]
            return True
        def enumerate_windows(callback, value):
            for hwnd in windows:
                if not callback(hwnd, value): break
            return True
        user = types.SimpleNamespace(
            IsWindowVisible=Call(lambda hwnd:True), GetWindowTextW=Call(text),
            GetWindowThreadProcessId=Call(pid), EnumWindows=Call(enumerate_windows),
            ShowWindow=Call(lambda hwnd,n:events.append(("restore",hwnd,n))),
            MoveWindow=Call(lambda hwnd,*a:events.append(("move",hwnd,*a))),
            SetForegroundWindow=Call(lambda hwnd:events.append(("foreground",hwnd))))
        kernel = types.SimpleNamespace(
            OpenProcess=Call(lambda rights,inherit,pid:0 if pid in denied else pid),
            QueryFullProcessImageNameW=Call(image), CloseHandle=Call(lambda h:closed.append(h)))
        fake_ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(user32=user,kernel32=kernel),
            WINFUNCTYPE=lambda *a:lambda fn:fn, POINTER=ctypes.POINTER, c_int=ctypes.c_int,
            create_unicode_buffer=ctypes.create_unicode_buffer, byref=ctypes.byref)
        ns = dict(ctypes=fake_ctypes, wintypes=ctypes.wintypes, os=os,
                  time=types.SimpleNamespace(sleep=lambda _:None))
        exec(compile(functions({"_place_window"}, TREE_SHELL), "dashboard-placement", "exec"), ns)
        ns["_place_window"]("مرصد الجهاز", 10,20,800,600)
        return events, closed, queried

    def test_only_exact_title_with_edge_owner_is_moved(self):
        windows = {1:("مرصد الجهاز — الويدجت",r"C:\PowerShell\pwsh.exe"),
                   2:("مرصد الجهاز — تقرير",r"C:\Edge\msedge.exe"),
                   3:("مرصد الجهاز",r"C:\PowerShell\pwsh.exe"),
                   4:("مرصد الجهاز",r"C:\Edge\msedge.exe")}
        events, closed, queried = self.run_placement(windows)
        self.assertEqual(events, [("restore",4,9),("move",4,10,20,800,600,True),("foreground",4)])
        self.assertEqual(queried, [103,104])
        self.assertEqual(closed, [103,104])

    def test_widget_or_report_alone_never_moved(self):
        events, closed, queried = self.run_placement({1:("مرصد الجهاز — الويدجت",r"C:\pwsh.exe"),
                                                    2:("مرصد الجهاز — تقرير",r"C:\msedge.exe")})
        self.assertEqual(events, [])
        self.assertEqual(queried, [])
        self.assertEqual(closed, [])

    def test_unknown_owner_or_access_denied_never_moved(self):
        windows={1:("مرصد الجهاز",r"C:\Other\not-msedge.exe"),
                 2:("مرصد الجهاز",r"C:\Edge\msedge.exe")}
        events, closed, queried = self.run_placement(windows, denied=(102,))
        self.assertEqual(events, [])
        self.assertEqual(set(closed), {101})
        self.assertNotIn(102, queried)


if __name__ == "__main__":unittest.main()
