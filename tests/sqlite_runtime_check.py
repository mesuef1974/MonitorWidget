"""Private SQLite integration checks in fresh isolated Python processes.

Only temporary test directories are written; no live monitor.db is opened.
Run: python -B tests/sqlite_runtime_check.py
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def child(location, data, code):
    prefix = "import sys;sys.path.insert(0,sys.argv[1]);from pathlib import Path\n"
    result = subprocess.run([sys.executable, "-I", "-B", "-c", prefix + code, str(location), str(data)],
                            capture_output=True, text=True, timeout=30)
    return result


class PrivateRuntime(unittest.TestCase):
    def test_pinned_version_idempotent_and_native_path(self):
        code = """
from sqlite_runtime import ensure_sqlite, runtime_info
import json
sqlite = ensure_sqlite()
assert ensure_sqlite() is sqlite
assert sqlite.sqlite_version == '3.54.1', sqlite.sqlite_version
info = runtime_info()
assert tuple(p.lower() for p in Path(info['dll_path']).parts[-3:]) == ('runtime','sqlite','sqlite3.dll')
print(json.dumps(info))
"""
        with tempfile.TemporaryDirectory(prefix="mw_sqlite_", dir=ROOT / "tests") as d:
            result = child(ROOT, d, code)
            self.assertEqual(result.returncode, 0, result.stderr)
            info = json.loads(result.stdout)
            self.assertEqual(info["dll_sha256"], "4b59d3cded9dc1608544160f3b4b10ac974c6ac0d6ffc23064e5f4c8dbd05aea")

    def test_transactions_wal_backup_and_fresh_restart(self):
        create = """
from sqlite_runtime import ensure_sqlite
sqlite = ensure_sqlite()
base = Path(sys.argv[2])
c = sqlite.connect(base / 'test.db')
assert c.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
c.execute('CREATE TABLE checks(id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
c.execute('INSERT INTO checks VALUES(1,?)', ('original',)); c.commit()
try:
    with c:
        c.execute('INSERT INTO checks VALUES(2,?)', ('must rollback',))
        raise RuntimeError('test rollback')
except RuntimeError:
    pass
assert c.execute('SELECT count(*) FROM checks').fetchone()[0] == 1
r = sqlite.connect(base / 'test.db')
r.execute('BEGIN')
assert r.execute('SELECT count(*) FROM checks').fetchone()[0] == 1
with c:
    c.execute('INSERT INTO checks VALUES(3,?)', ('committed',))
assert r.execute('SELECT count(*) FROM checks').fetchone()[0] == 1
r.rollback()
assert r.execute('SELECT count(*) FROM checks').fetchone()[0] == 2
b = sqlite.connect(base / 'backup.db')
c.backup(b)
assert b.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
assert b.execute('SELECT id,value FROM checks ORDER BY id').fetchall() == [(1,'original'),(3,'committed')]
b.close(); r.close(); c.close()
print('WAL+transactions+backup OK')
"""
        restart = """
from sqlite_runtime import ensure_sqlite
sqlite = ensure_sqlite()
assert sqlite.sqlite_version == '3.54.1'
for name in ('test.db','backup.db'):
    c = sqlite.connect((Path(sys.argv[2]) / name).as_uri() + '?mode=ro', uri=True)
    assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert c.execute('SELECT id,value FROM checks ORDER BY id').fetchall() == [(1,'original'),(3,'committed')]
    c.close()
print('fresh process restart OK')
"""
        with tempfile.TemporaryDirectory(prefix="mw_sqlite_", dir=ROOT / "tests") as d:
            first = child(ROOT, d, create)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = child(ROOT, d, restart)
            self.assertEqual(second.returncode, 0, second.stderr)

    def test_missing_runtime_refuses_fallback(self):
        with tempfile.TemporaryDirectory(prefix="mw_sqlite_", dir=ROOT / "tests") as d:
            shutil.copy2(ROOT / "sqlite_runtime.py", Path(d) / "sqlite_runtime.py")
            result = child(d, d, "from sqlite_runtime import ensure_sqlite\nensure_sqlite()")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing or unreadable", result.stderr)

    def test_modified_runtime_refuses_fallback(self):
        with tempfile.TemporaryDirectory(prefix="mw_sqlite_", dir=ROOT / "tests") as d:
            target = Path(d)
            shutil.copy2(ROOT / "sqlite_runtime.py", target / "sqlite_runtime.py")
            runtime = target / "runtime" / "sqlite"; runtime.mkdir(parents=True)
            dll = runtime / "sqlite3.dll"
            shutil.copy2(ROOT / "runtime" / "sqlite" / "sqlite3.dll", dll)
            with dll.open("r+b") as fh:
                fh.seek(-1, 2); value = fh.read(1)
                fh.seek(-1, 2); fh.write(bytes([value[0] ^ 1]))
            result = child(d, d, "from sqlite_runtime import ensure_sqlite\nensure_sqlite()")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SHA-256 mismatch", result.stderr)

    def test_preimported_sqlite_is_rejected(self):
        result = child(ROOT, ROOT, "import sqlite3\nfrom sqlite_runtime import ensure_sqlite\nensure_sqlite()")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("imported before its private runtime", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
