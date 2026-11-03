"""Exercise protected-only collector registration without importing agent/server."""
import ast
from pathlib import Path
import sys
import types
import unittest
import json
import os
import tempfile
import time
from unittest.mock import patch


class AgentDeployment(unittest.TestCase):
    def test_prepared_marker_ignores_user_secure_override_and_final_location_must_match(self):
        root = Path(__file__).resolve().parents[1]
        scratch = root / "tests" / ".sandboxes"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as folder:
            app = Path(folder) / "app"
            marker = app / ".deployment"
            marker.mkdir(parents=True)
            (marker / "manifest.json").write_text(json.dumps({"destination": str(app)}), encoding="utf-8")
            namespace = {"__file__":str(app / "secure.py"), "__name__":"isolated_secure"}
            with patch.dict(os.environ, {"MW_SECURE_DIR":str(Path(folder) / "hostile")}):
                exec(compile((root / "secure.py").read_text(encoding="utf-8-sig"), "isolated_secure", "exec"), namespace)
            self.assertNotEqual(namespace["SECURE_DIR"], str(Path(folder) / "hostile"))
            self.assertFalse(namespace["protected_app_ready"]())
            namespace["_system_secure_dir"] = lambda: folder
            self.assertTrue(namespace["protected_app_ready"]())

    def test_repeated_collector_calls_initialize_and_release_without_external_work(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "collector.py").read_text(encoding="utf-8-sig"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        calls = []
        now = time.time()
        record = lambda *a, **k: calls.append((a,k))
        health = types.SimpleNamespace(ADMIN_PROBE="unused", add_procs=record, read_procs=lambda:[],
            read_gpu=lambda:(None, []), add_igpu=record, read_igpu=lambda _:None,
            get_hw=lambda _:(now,{}), score=lambda *a:{}, save_daily=record)
        db = types.SimpleNamespace(bootstrap=lambda:calls.append("bootstrap"), import_all=record,
            add_sample=record,get_json=lambda _:{"ts":now,"tasks":[]}, prune_if_due=record)
        namespace = {"acquire_instance":lambda:True,"release_instance":lambda:calls.append("release"),
                     "db":db,"health":health,"snapshot":types.SimpleNamespace(native_metrics=lambda:{}),   # القياسات الأصلية في snapshot (البند 6/ز) لا server
                     "space":types.SimpleNamespace(scan_if_due=record),"time":time,"log":record,
                     "os":types.SimpleNamespace(path=types.SimpleNamespace(exists=lambda _:False)),
                     "errlog":types.SimpleNamespace(swallowed=record)}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),"collector-main","exec"),namespace)
        namespace["main"](raise_errors=True)
        namespace["main"](raise_errors=True)
        self.assertEqual(calls.count("bootstrap"),2)
        self.assertEqual(calls.count("release"),2)

    def test_source_has_no_internal_collector_and_protected_uses_existing_agent(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "agent.py").read_text(encoding="utf-8-sig"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "register_protected_collector")
        calls = []
        collection = []
        fake_os = types.SimpleNamespace(path=types.SimpleNamespace(
            isdir=lambda _: False, join=lambda *p: "/".join(p), dirname=lambda _: "application", abspath=lambda p:p))
        namespace = {"os": fake_os, "__file__": "agent.py"}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "extracted-registration", "exec"), namespace)
        scheduler = types.SimpleNamespace(register=lambda *a, **k:calls.append((a, k)))
        register = namespace["register_protected_collector"]
        with patch.dict(sys.modules, {"secure": types.SimpleNamespace(protected_app_ready=lambda:False)}):
            self.assertFalse(register(scheduler)); self.assertEqual(calls, [])
        with patch.dict(sys.modules, {"secure": types.SimpleNamespace(protected_app_ready=lambda:True), "collector": types.SimpleNamespace(main=lambda **k:collection.append(k))}):
            self.assertTrue(register(scheduler))
        self.assertEqual(calls[0][0][0], "collector")
        self.assertEqual(calls[0][0][2], 300)
        self.assertEqual(calls[0][1]["mode"], "after_finish")
        calls[0][0][1]()
        self.assertEqual(collection, [{"raise_errors": True}])


if __name__ == "__main__":
    unittest.main()
