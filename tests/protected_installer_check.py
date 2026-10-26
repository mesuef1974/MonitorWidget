"""Installer preparation/snapshot tests; no Windows task mutation or live DB.

The embedded Python block is extracted from the installer and exercised only
against disposable app trees. No Stage/Apply/Rollback mode is executed here.
"""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlite_runtime import ensure_sqlite
sqlite3 = ensure_sqlite()
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import types
import unittest
from quarantine_store import QuarantineStore


class ProtectedInstaller(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mw_install_", dir=ROOT / "tests")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source, self.stage, self.final = [self.base / n for n in ("source", "stage", "app")]
        for path in (self.source, self.stage):
            path.mkdir()
        shutil.copy2(ROOT / "sqlite_runtime.py", self.stage / "sqlite_runtime.py")
        runtime = self.stage / "runtime" / "sqlite"; runtime.mkdir(parents=True)
        shutil.copy2(ROOT / "runtime" / "sqlite" / "sqlite3.dll", runtime / "sqlite3.dll")
        self.text = (ROOT / "install_protected_app_20261025.ps1").read_text(encoding="utf-8-sig")
        self.code = re.search(r"\$snapshotCode = @'\r?\n(.*?)\r?\n'@", self.text, re.S).group(1)
        self.origins = self.base / "temp"; self.origins.mkdir()
        self.original = self.origins / "sample.txt"
        self.source_file = self.source / "quarantine" / "operation" / "sample.txt"
        self.source_file.parent.mkdir(parents=True)
        self.source_file.write_bytes(b"quarantined sample")
        self.staged_file = self.stage / "quarantine" / "operation" / "sample.txt"
        self.staged_file.parent.mkdir(parents=True)
        shutil.copy2(self.source_file, self.staged_file)

    def database(self, status="held", qpath=None):
        db = sqlite3.connect(self.source / "monitor.db")
        db.execute("CREATE TABLE quarantine(id INTEGER PRIMARY KEY,ts INTEGER,orig TEXT,qpath TEXT,bytes INTEGER,expires INTEGER,status TEXT,action_id INTEGER,op_identity TEXT)")
        identity = os.stat(self.source_file, follow_symlinks=False)
        db.execute("INSERT INTO quarantine VALUES(1,0,?,?,18,2147483647,?,NULL,?)", (str(self.original), str(qpath or self.source_file), status, json.dumps([identity.st_dev, identity.st_ino])))
        db.commit(); db.close()

    def snapshot(self):
        return subprocess.run([sys.executable,"-I","-S","-B","-c",self.code,str(self.stage),str(self.source),str(self.final)],
                              capture_output=True,text=True,timeout=30)

    def test_qpath_remap_identity_and_held_restore_after_final_move(self):
        self.database()
        before = hashlib.sha256((self.source / "monitor.db").read_bytes()).hexdigest()
        result = self.snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(hashlib.sha256((self.source / "monitor.db").read_bytes()).hexdigest(), before)
        self.stage.rename(self.final)
        db = sqlite3.connect(self.final / "monitor.db"); db.row_factory = sqlite3.Row
        try:
            row = dict(db.execute("SELECT * FROM quarantine").fetchone())
            self.assertEqual(Path(row["qpath"]), self.final / "quarantine" / "operation" / "sample.txt")
            self.assertEqual(row["orig"], str(self.original))
            info = os.stat(row["qpath"], follow_symlinks=False)
            self.assertEqual(json.loads(row["op_identity"]), [info.st_dev, info.st_ino])
            store = QuarantineStore(types.SimpleNamespace(_lock=threading.RLock()),
                str(self.final / "quarantine"), lambda: db, [str(self.origins)])
            self.assertTrue(store.restore(1)["ok"])
            self.assertEqual(self.original.read_bytes(), b"quarantined sample")
            self.assertTrue(self.source_file.exists())
        finally:
            db.close()

    def test_active_quarantine_outside_source_root_blocks_snapshot(self):
        self.database(qpath=self.base / "outside" / "sample.txt")
        result = self.snapshot()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the source quarantine root", result.stderr)
        self.assertFalse((self.stage / "monitor.db").exists())

    def test_unresolved_operation_blocks_snapshot(self):
        self.database(status="moving")
        result = self.snapshot()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unresolved operation", result.stderr)

    def test_static_no_activation_or_principal_elevation(self):
        self.assertIn("[string]$Mode = 'Plan'", self.text)
        self.assertIn("if ($StageOnly) { $Mode = 'Stage' }", self.text)
        for command in ("Start-ScheduledTask", "Stop-ScheduledTask", "Enable-ScheduledTask", "Stop-Process", "Remove-Item", "Set-ExecutionPolicy"):
            self.assertNotIn(command, self.text)
        self.assertNotIn("New-ScheduledTaskPrincipal", self.text)
        self.assertNotRegex(self.text, r"RunLevel\s*=\s*'Highest|level\.InnerText\s*=")
        self.assertIn("$enabled.InnerText = 'false'", self.text)
        self.assertIn("Superseded: the protected MonitorWidget-Agent", self.text)
        self.assertNotRegex(self.text, r"Get-Content[^\n]*\.token[^\n]*Write-(Host|Output)")

    def test_apply_requires_explicit_token_rotation_and_shortcut_migration(self):
        self.assertIn("[switch]$RotateProtectedToken", self.text)
        self.assertIn("[switch]$MigrateStartupShortcut", self.text)
        apply = self.text.split("if ($Mode -eq 'Apply') {", 1)[1].split("if ($Mode -eq 'Rollback')", 1)[0]
        self.assertIn("if (-not $RotateProtectedToken) { throw", apply)
        self.assertIn("if ($shortcutExists -and -not $MigrateStartupShortcut) { throw", apply)
        self.assertLess(apply.index("Copy-DataSnapshot $finalStage"), apply.index("Set-ProtectedToken $finalStage"))
        self.assertLess(apply.index("Set-ProtectedToken $finalStage"), apply.index("Write-TaskAction $name $original"))
        self.assertIn("Restore-PreparedActions (Join-Path $AppRoot '.deployment') $shortcutInfo", apply)
        self.assertIn("startupShortcut = [pscustomobject]", self.text)
        self.assertLess(apply.index("Move-Item -LiteralPath $resolvedFinal"), apply.index("Set-ProtectedLaunchMarker"))
        self.assertLess(apply.index("Set-ProtectedLaunchMarker"), apply.index("Write-TaskAction $name $original"))
        stage = self.text.split("if ($Mode -eq 'Stage') {", 1)[1].split("if ($Mode -eq 'Apply')", 1)[0]
        self.assertNotIn("Set-ProtectedLaunchMarker", stage)
        self.assertNotIn("$env:ProgramData", self.text)
        self.assertNotIn("$env:ProgramFiles", self.text)
        self.assertIn("GetFolderPath('CommonApplicationData')", self.text)
        self.assertIn("GetFolderPath('ProgramFiles')", self.text)
        self.assertIn("$AppRoot.IndexOfAny", self.text)
        launcher = (ROOT / "launch_mini.vbs").read_text(encoding="utf-8-sig")
        self.assertIn('fs.FileExists(here & "\\.protected-launch")', launcher)
        self.assertNotIn('FolderExists(here & "\\.deployment")', launcher)


if __name__ == "__main__":
    unittest.main(verbosity=2)
