"""Behavioral regressions in a disposable copied runtime, never the live database."""
import os, pathlib, shutil, subprocess, sys, tempfile

if "--sandbox" not in sys.argv:
    root = pathlib.Path(__file__).resolve().parent.parent
    sandbox_parent = root / "tests" / ".sandboxes"
    sandbox_parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mw_db_ops_", dir=sandbox_parent) as folder:
        target = pathlib.Path(folder)
        for source in root.glob("*.py"):
            shutil.copy2(source, target / source.name)
        runtime = target / "runtime" / "sqlite"
        runtime.mkdir(parents=True)
        for name in ("sqlite3.dll", "manifest.json"):
            shutil.copy2(root / "runtime" / "sqlite" / name, runtime / name)
        shutil.copy2(__file__, target / "db_ops_regressions.py")
        env = dict(os.environ, MW_TEST_SOURCE_ROOT=str(root))
        boot = "import sys,runpy;sys.path.insert(0,sys.argv[1]);sys.argv=[sys.argv[2],'--sandbox'];runpy.run_path(sys.argv[0],run_name='__main__')"
        result = subprocess.run([sys.executable, "-I", "-B", "-c", boot, str(target), str(target / "db_ops_regressions.py")],
                                cwd=target, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        print(result.stdout, end="")
        print(result.stderr, end="")
        sys.exit(result.returncode)

from sqlite_runtime import ensure_sqlite
ensure_sqlite()
import ast, ctypes, importlib.util, json, sqlite3, threading, time, unittest
from unittest import mock
import db, infra, health
spec = importlib.util.spec_from_file_location("mw_scheduler", pathlib.Path("sched.py"))
scheduler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scheduler)


class DbOps(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=pathlib.Path.cwd(), prefix="case_")
        self.addCleanup(self.temp.cleanup)
        self.folder = pathlib.Path(self.temp.name)
        self.old_path, self.old_conn = db.DB_PATH, db._conn
        db.DB_PATH, db._conn = str(self.folder / "monitor.db"), None
        self.addCleanup(self.close_db)
        self.c = db.bootstrap()

    def close_db(self):
        if db._conn:
            db._conn.close()
        db.DB_PATH, db._conn = self.old_path, self.old_conn

    def test_fresh_bootstrap_and_idempotence(self):
        self.assertEqual(self.c.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)
        self.assertEqual(self.c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], str(db.SCHEMA_VERSION))
        for name in ("recs", "jobs", "quarantine", "gpu"):
            self.c.execute('SELECT count(*) FROM "' + name + '"').fetchone()
        db.bootstrap()
        self.assertEqual(db.info()["schema"], db.SCHEMA_VERSION)
        self.assertFalse(health._inited, "bootstrap must not start probe worker")

    def test_thinning_unaligned_and_pressure_peak(self):
        start, cutoff = 1700000001, 1800000000
        for i in range(1440):
            self.c.execute("INSERT INTO samples(ts,source,commit_pct) VALUES(?,?,?)", (start + i * 60, "live", i % 101))
        self.c.execute("INSERT INTO samples(ts,source) VALUES(?,?)", (start, "guard"))
        self.c.execute("INSERT INTO samples(ts,source) VALUES(?,?)", (cutoff + 1, "live"))
        expected = self.c.execute("SELECT CAST(ts/300 AS INTEGER),max(commit_pct) FROM samples WHERE source='live' AND ts<? GROUP BY CAST(ts/300 AS INTEGER)", (cutoff,)).fetchall()
        db.thin_live_samples(self.c, cutoff)
        actual = self.c.execute("SELECT CAST(ts/300 AS INTEGER),commit_pct FROM samples WHERE source='live' AND ts<? ORDER BY ts", (cutoff,)).fetchall()
        self.assertEqual([tuple(r) for r in expected], [tuple(r) for r in actual])
        self.assertEqual(self.c.execute("SELECT count(*) FROM samples WHERE source='guard'").fetchone()[0], 1)
        self.assertEqual(self.c.execute("SELECT count(*) FROM samples WHERE ts>?", (cutoff,)).fetchone()[0], 1)
        count = len(actual)
        db.thin_live_samples(self.c, cutoff)
        self.assertEqual(self.c.execute("SELECT count(*) FROM samples WHERE source='live' AND ts<?", (cutoff,)).fetchone()[0], count)

    def test_null_only_bucket_retains_real_row(self):
        for stamp in (1001, 1002, 1003):
            self.c.execute("INSERT INTO samples(ts,source) VALUES(?,'live')", (stamp,))
        db.thin_live_samples(self.c, 2000)
        self.assertEqual(self.c.execute("SELECT ts FROM samples").fetchall()[0][0], 1003)
        self.assertEqual(self.c.execute("SELECT count(*) FROM samples").fetchone()[0], 1)

    def test_scheduler_releases_marker_on_bookkeeping_failure(self):
        job = scheduler.Scheduler()
        job.fns["x"] = lambda: None
        job.running.add("x")
        with mock.patch.object(db, "conn", side_effect=sqlite3.OperationalError("synthetic unavailable")):
            job._run("x")
        self.assertNotIn("x", job.running)
        self.assertGreater(job._retry_after["x"], time.monotonic())

    def test_scheduler_cooperative_deadline_and_no_overlap(self):
        job = scheduler.Scheduler()
        def slow_io():
            self.assertLessEqual(scheduler.remaining_timeout(10), 0.05)
            time.sleep(0.02)
            scheduler.check_cancelled()
        job.register("deadline", slow_io, 30, timeout_s=0.01)
        job.running.add("deadline")
        job._run("deadline")
        row = self.c.execute("SELECT last_status,last_error FROM jobs WHERE name='deadline'").fetchone()
        self.assertEqual(row["last_status"], "error")
        self.assertIn("TimeoutError", row["last_error"])
        self.assertNotIn("deadline", job.running)
        self.assertEqual(scheduler.remaining_timeout(7), 7)

    def test_backup_retry_independent_from_local_success(self):
        bk = self.folder / "backups"
        bk.mkdir()
        (bk / "monitor-20261025.db").write_bytes(b"synthetic")
        now = int(time.time())
        self.c.execute("INSERT INTO meta VALUES('last_backup',?)", (str(now),))
        self.c.commit()
        with mock.patch.object(infra, "BK_DIR", str(bk)), mock.patch.object(infra, "_extra_backup", side_effect=OSError("disk absent")):
            with self.assertRaises(OSError):
                infra.external_backup_retry()
        self.assertIsNone(self.c.execute("SELECT value FROM meta WHERE key='backup_extra_source_day'").fetchone())
        with mock.patch.object(infra, "BK_DIR", str(bk)), mock.patch.object(infra, "_extra_backup", return_value="copied") as copy, mock.patch.object(infra.time, "time", return_value=now+301):
            infra.external_backup_retry()
            copy.assert_called_once()
        self.assertEqual(self.c.execute("SELECT value FROM meta WHERE key='backup_extra_source_day'").fetchone()[0], "20261025")
        self.assertEqual(self.c.execute("SELECT value FROM meta WHERE key='last_backup'").fetchone()[0], str(now))

    def test_future_named_backup_does_not_freeze_selection(self):
        # Clock jump 14/11/2026: a "monitor-20261231.db" taken that night must not
        # stay "latest" by name and starve the external copy and rotation.
        bk = self.folder / "backups"
        bk.mkdir()
        base = time.time() - 10 * 86400
        jumped = bk / "monitor-20261231.db"
        jumped.write_bytes(b"jumped")
        os.utime(jumped, (base, base))
        for i, day in enumerate(("20261115", "20261116", "20261117")):
            f = bk / ("monitor-%s.db" % day)
            f.write_bytes(day.encode())
            os.utime(f, (base + (i + 1) * 86400,) * 2)
        self.assertEqual(pathlib.Path(infra.by_age(str(bk))[-1]).name, "monitor-20261117.db")
        with mock.patch.object(infra, "BK_DIR", str(bk)):
            self.assertEqual(infra.backups()[0]["name"], "monitor-20261117.db")
            self.c.execute("INSERT INTO meta VALUES('backup_extra_source_day','20261231')")
            self.c.commit()
            with mock.patch.object(infra, "_extra_backup", return_value="copied") as copy:
                infra.external_backup_retry()
            self.assertTrue(copy.call_args[0][0].endswith("monitor-20261117.db"))
            with mock.patch.object(infra, "KEEP_BACKUPS", 2), mock.patch.object(infra, "integrity", return_value=(True, "ok")):
                infra.backup(self.c)
        names = sorted(p.name for p in bk.glob("monitor-*.db"))
        self.assertNotIn("monitor-20261231.db", names)
        self.assertIn(time.strftime("monitor-%Y%m%d.db"), names)
        self.assertEqual(len(names), 2)

    def test_clock_jump_does_not_erase_history(self):
        import clockguard
        day = 86400
        t0, up0 = 1_800_000_000, 1000.0
        self.assertEqual(clockguard.safe_now(self.c, "k", now=t0, up=up0), t0)
        # Same boot, one real hour later, but the wall clock jumped 47 days ahead.
        got = clockguard.safe_now(self.c, "k", now=t0 + 47 * day, up=up0 + 3600)
        self.assertLessEqual(got, t0 + 3600 + clockguard.SAME_BOOT_SLACK_S + 36)
        # Clock corrected: the anchor was not dragged to the jumped time.
        self.assertEqual(clockguard.safe_now(self.c, "k", now=t0 + 7200, up=up0 + 7200), t0 + 7200)
        # Backward jump deletes less, and the anchor keeps real progress.
        self.assertEqual(clockguard.safe_now(self.c, "k", now=t0, up=up0 + 10800), t0)
        self.assertEqual(clockguard.safe_now(self.c, "k", now=t0 + 14400, up=up0 + 14400), t0 + 14400)
        # Reboot (uptime reset): catch up at most REBOOT_CATCHUP_S per pass.
        got = clockguard.safe_now(self.c, "k", now=t0 + 10 * day, up=50.0)
        self.assertGreaterEqual(got, t0 + 14400 + clockguard.REBOOT_CATCHUP_S)
        self.assertLess(got, t0 + 14400 + clockguard.REBOOT_CATCHUP_S + 3600)
        # An anchor written during a jump (far ahead of the clock) is dropped, not trusted forever.
        self.c.execute("UPDATE meta SET value=? WHERE key='k'", (f"{t0 + 60 * day}|100.0",))
        self.assertEqual(clockguard.safe_now(self.c, "k", now=t0 + day, up=200.0), t0 + day)
        self.assertEqual(clockguard.safe_now(self.c, "k", now=t0 + day + 60, up=260.0), t0 + day + 60)

    def test_db_prune_ignores_forward_clock_jump(self):
        import clockguard
        real = int(time.time())
        self.c.execute("CREATE TABLE IF NOT EXISTS cpu_top(ts INTEGER NOT NULL, name TEXT NOT NULL, cores REAL, n INTEGER)")
        self.c.execute("INSERT INTO cpu_top(ts,name) VALUES(?, 'x')", (real - 86400,))   # retention 3 days
        self.c.commit()
        db.prune()                      # anchors the guard at the real time
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM cpu_top").fetchone()[0], 1)
        with mock.patch.object(clockguard.time, "time", return_value=real + 47 * 86400):
            db.prune()
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM cpu_top").fetchone()[0], 1)

    def test_external_copy_verified_and_failed_copy_keeps_previous(self):
        target = self.folder / "external"
        target.mkdir()
        src = self.folder / "monitor-20261025.db"
        src.write_bytes(b"valid snapshot" * 100000)
        self.c.execute("INSERT INTO settings(key,value,ts) VALUES('backup.extra_dir',?,0)", (json.dumps(str(target)),))
        self.c.commit()
        dst = pathlib.Path(infra._extra_backup(str(src)))
        self.assertEqual(dst.read_bytes(), src.read_bytes())
        src.write_bytes(b"new data")
        with mock.patch.object(infra, "_io_deadline", return_value=0):
            with self.assertRaises(TimeoutError):
                infra._extra_backup(str(src))
        self.assertEqual(dst.read_bytes(), b"valid snapshot" * 100000)
        self.assertFalse(list(target.glob("*.tmp")))

    def test_required_policy_and_engine_missing_fail(self):
        # Extract source snapshots are copied with the test runner's top-level wrapper.
        root = pathlib.Path(os.environ["MW_TEST_SOURCE_ROOT"])
        for name in ("policy_check.py", "engine_check.py"):
            result = subprocess.run([sys.executable, "-B", str(root / "tests" / name), str(self.folder / "missing")], capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 1, name)
            self.assertIn("FAIL", result.stdout)

    def test_collector_mutex_has_kernel_lifetime(self):
        # Extract functions only: importing collector would start no main, but
        # it imports server and would pull in unrelated app dependencies.
        tree = ast.parse(pathlib.Path("collector.py").read_text(encoding="utf-8-sig"))
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("acquire_instance", "release_instance")]
        active = {"handle": False}
        class KernelFn:
            def __init__(self, call): self.call = call
            def __call__(self, *args): return self.call(*args)
        kernel = type("Kernel", (), {})()
        duplicate = [False]
        def create(*args):
            duplicate[0] = active["handle"]
            active["handle"] = True
            return 123
        kernel.CreateMutexW = KernelFn(create)
        kernel.GetLastError = KernelFn(lambda: 183 if duplicate[0] else 0)
        kernel.CloseHandle = KernelFn(lambda handle: None)
        kernel.ReleaseMutex = KernelFn(lambda handle: active.update(handle=False))
        fake = type("Ctypes", (), {"windll":type("Windll", (), {"kernel32":kernel})(), "c_void_p":ctypes.c_void_p,"c_int":ctypes.c_int,"c_wchar_p":ctypes.c_wchar_p})()
        ns = {"ctypes":fake,"_mutex":None}
        exec(compile(ast.Module(body=funcs,type_ignores=[]),"collector-functions","exec"),ns)
        self.assertTrue(ns["acquire_instance"]())
        first = ns["_mutex"]
        self.assertFalse(ns["acquire_instance"]())
        ns["_mutex"] = first
        ns["release_instance"]()
        self.assertTrue(ns["acquire_instance"]())
        ns["release_instance"]()


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
