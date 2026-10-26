"""Elevated reaper refuses user-writable source, with no process execution."""
import ast
import ctypes
import os
from pathlib import Path
import threading
import types
import unittest
from unittest.mock import patch


class ElevatedExecution(unittest.TestCase):
    def test_missing_protected_bundle_never_executes_source_reaper(self):
        root=Path(__file__).resolve().parents[1]
        tree=ast.parse((root/'insight.py').read_text(encoding='utf-8-sig'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='reaper_run')
        called=[]
        fake_os=types.SimpleNamespace(path=types.SimpleNamespace(join=os.path.join,isfile=lambda _:False))
        lock=threading.Lock()
        namespace={'os':fake_os,'secure':types.SimpleNamespace(BIN='protected-bin'),'_run_lock':lock,
                   'subprocess':types.SimpleNamespace(run=lambda *a,**k:called.append(a))}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'isolated-reaper','exec'),namespace)
        with patch.object(ctypes.windll.shell32,'IsUserAnAdmin',return_value=1):
            result=namespace['reaper_run'](False,'user-writable-folder')
        self.assertFalse(result['ok']);self.assertIn('حزمة محمية',result['error'])
        self.assertEqual(called,[]);self.assertFalse(lock.locked())


if __name__=='__main__':unittest.main()
