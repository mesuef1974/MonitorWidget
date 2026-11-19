"""Quarantine behavior tests using only disposable files and a private SQLite DB."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from sqlite_runtime import ensure_sqlite
ensure_sqlite()
import concurrent.futures, json, os, sqlite3, subprocess, tempfile, threading, time, types, unittest
from unittest import mock
import quarantine_store as qs


class Quarantine(unittest.TestCase):
    def setUp(self):
        parent = pathlib.Path(__file__).resolve().parent / ".sandboxes"
        parent.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="quarantine_", dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.orig = self.base / "temp"
        self.orig.mkdir()
        self.root = self.base / "quarantine"
        self.db = sqlite3.connect(self.base / "test.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE quarantine(id INTEGER PRIMARY KEY,ts INTEGER,orig TEXT,qpath TEXT,bytes INTEGER,expires INTEGER,status TEXT DEFAULT 'held',action_id INTEGER)")
        self.db.commit()
        self.addCleanup(self.db.close)
        self.module = types.SimpleNamespace(_lock=threading.RLock())
        self.store = qs.QuarantineStore(self.module, str(self.root), lambda:self.db, [str(self.orig)])

    def source(self, name="sample.txt", data=b"original"):
        p = self.orig / name
        p.write_bytes(data)
        return p

    def row(self, qid):
        return dict(self.db.execute("SELECT * FROM quarantine WHERE id=?", (qid,)).fetchone())

    def expire(self, qid):
        self.db.execute("UPDATE quarantine SET expires=0 WHERE id=?", (qid,))
        self.db.commit()

    def test_intent_is_durable_before_atomic_move_and_uuid_unique(self):
        original = self.source()
        move = qs._rename_no_replace
        def inspect(src, dst, *roots):
            row = dict(self.db.execute("SELECT * FROM quarantine").fetchone())
            self.assertEqual(row["status"], "moving")
            self.assertTrue(row["op_uuid"])
            check = sqlite3.connect(self.base / "test.db")
            try:self.assertEqual(check.execute("SELECT status FROM quarantine").fetchone()[0], "moving")
            finally:check.close()
            move(src,dst,*roots)
        with mock.patch.object(qs, "_rename_no_replace", side_effect=inspect):
            qid = self.store.move(str(original))
        first = self.row(qid)
        self.assertFalse(original.exists())
        self.assertEqual(first["status"], "held")
        second = self.store.move(str(self.source(data=b"other")))
        self.assertNotEqual(first["op_uuid"], self.row(second)["op_uuid"])
        self.assertNotEqual(first["qpath"], self.row(second)["qpath"])

    def test_crash_after_move_before_db_finish_reconciles_without_loss(self):
        original = self.source()
        mark = self.store._mark
        def crash(qid,status,**kwargs):
            if status == "held":raise sqlite3.OperationalError("simulated commit outage")
            return mark(qid,status,**kwargs)
        with mock.patch.object(self.store,"_mark",side_effect=crash):
            with self.assertRaises(sqlite3.OperationalError):self.store.move(str(original))
        self.assertFalse(original.exists())
        self.assertEqual(self.db.execute("SELECT status FROM quarantine").fetchone()[0], "moving")
        self.assertEqual(self.store.listing()[0]["status"], "held")
        self.assertTrue(self.store.restore(1)["ok"])
        self.assertEqual(original.read_bytes(), b"original")

    def test_crash_after_restore_reconciles_legacy_identity(self):
        self.root.mkdir()
        q = self.root / "legacy.txt"
        q.write_bytes(b"legacy")
        original = self.orig / "legacy.txt"
        self.db.execute("INSERT INTO quarantine(ts,orig,qpath,bytes,expires) VALUES(0,?,?,6,9999999999)", (str(original),str(q)))
        self.db.commit()
        mark = self.store._mark
        def crash(qid,status,**kwargs):
            if status == "restored":raise sqlite3.OperationalError("simulated commit outage")
            return mark(qid,status,**kwargs)
        with mock.patch.object(self.store,"_mark",side_effect=crash):
            with self.assertRaises(sqlite3.OperationalError):self.store.restore(1)
        self.assertEqual(self.row(1)["status"],"restoring")
        self.assertTrue(self.row(1)["op_identity"])
        self.store.listing()
        self.assertEqual(self.row(1)["status"],"restored")
        self.assertEqual(original.read_bytes(),b"legacy")

    def test_crash_before_move_keeps_original_and_reports_failed(self):
        original = self.source()
        with mock.patch.object(qs,"_rename_no_replace",side_effect=SystemExit("crash")):
            with self.assertRaises(SystemExit):self.store.move(str(original))
        self.assertEqual(original.read_bytes(), b"original")
        self.assertEqual(self.store.listing()[0]["status"], "failed")

    def test_restore_no_replace_even_when_target_appears_after_check(self):
        original = self.source()
        qid = self.store.move(str(original))
        rename = qs._rename_no_replace
        def race(src,dst,*roots):
            pathlib.Path(dst).write_bytes(b"new user file")
            rename(src,dst,*roots)
        with mock.patch.object(qs,"_rename_no_replace",side_effect=race):
            result = self.store.restore(qid)
        self.assertFalse(result["ok"])
        self.assertEqual(original.read_bytes(), b"new user file")
        self.assertEqual(pathlib.Path(self.row(qid)["qpath"]).read_bytes(), b"original")
        self.assertEqual(self.row(qid)["status"], "held")

    def test_legacy_held_rows_restore(self):
        self.root.mkdir()
        q = self.root / "legacy.txt"
        q.write_bytes(b"legacy")
        original = self.orig / "legacy.txt"
        self.db.execute("INSERT INTO quarantine(ts,orig,qpath,bytes,expires) VALUES(0,?,?,6,9999999999)", (str(original),str(q)))
        self.db.commit()
        self.assertTrue(self.store.restore(1)["ok"])
        self.assertEqual(original.read_bytes(), b"legacy")

    def test_concurrent_restore_and_purge_have_one_safe_outcome(self):
        qid = self.store.move(str(self.source()))
        self.expire(qid)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(self.store.restore,qid),workers.submit(self.store.purge)]
            for future in futures:future.result(timeout=10)
        row = self.row(qid)
        self.assertIn(row["status"], ("restored","purged"))
        if row["status"] == "restored":self.assertEqual(pathlib.Path(row["orig"]).read_bytes(),b"original")
        self.assertFalse(pathlib.Path(row["qpath"]).exists())

    def test_purge_crash_finishes_tombstone_on_recovery(self):
        qid = self.store.move(str(self.source()))
        self.expire(qid)
        mark = self.store._mark
        def crash(qid,status,**kwargs):
            if status == "purged":raise sqlite3.OperationalError("simulated commit outage")
            return mark(qid,status,**kwargs)
        with mock.patch.object(self.store,"_mark",side_effect=crash):
            with self.assertRaises(sqlite3.OperationalError):self.store.purge()
        self.assertEqual(self.row(qid)["status"],"purging")
        self.store.listing()
        self.assertEqual(self.row(qid)["status"],"purged")

    def test_purge_sweeps_empty_dirs_and_only_stale_failed_rows_with_intact_original(self):
        gone = self.store.move(str(self.source()))
        self.expire(gone)
        keep = self.store.move(str(self.source("keep.txt", b"k")))
        old, fresh = int(time.time()) - 2 * 86400, int(time.time())
        busy, missing, young = self.source("busy.txt", b"busy"), self.orig / "missing.txt", self.source("young.txt", b"y")
        for p, ts in ((busy, old), (missing, old), (young, fresh)):
            self.db.execute("INSERT INTO quarantine(ts,orig,qpath,bytes,expires,status,op_updated) VALUES(?,?,?,4,0,'failed',?)", (ts, str(p), str(self.root / "zz" / p.name), ts))
        self.db.commit()
        self.store.purge()
        self.assertFalse(pathlib.Path(self.row(gone)["qpath"]).parent.exists())   # the purged item's folder is gone
        self.assertEqual(pathlib.Path(self.row(keep)["qpath"]).read_bytes(), b"k")   # a held item and its folder stay
        left = {pathlib.Path(r[0]).name for r in self.db.execute("SELECT orig FROM quarantine WHERE status='failed'")}
        self.assertEqual(left, {"missing.txt", "young.txt"})   # only the stale row with an intact original is dropped
        self.assertEqual(busy.read_bytes(), b"busy")

    def test_purge_waits_for_expiry_but_force_purges_every_held_item(self):
        a = self.store.move(str(self.source("a.txt", b"aa")))
        b = self.store.move(str(self.source("b.txt", b"bbb")))
        self.assertEqual(self.store.held_bytes(), 5)
        self.store.purge()
        self.assertEqual({self.row(a)["status"], self.row(b)["status"]}, {"held"})   # not expired: untouched
        self.store.purge(force=True)
        self.assertEqual({self.row(a)["status"], self.row(b)["status"]}, {"purged"})
        self.assertEqual(self.store.held_bytes(), 0)
        self.assertFalse(pathlib.Path(self.row(a)["qpath"]).exists())

    def test_outside_quarantine_path_is_never_deleted(self):
        outside=self.source("outside.txt",b"keep")
        self.db.execute("INSERT INTO quarantine(ts,orig,qpath,bytes,expires) VALUES(0,?,?,4,0)",(str(self.orig/'fake'),str(outside)))
        self.db.commit()
        self.store.purge()
        self.assertEqual(outside.read_bytes(),b"keep")
        self.assertEqual(self.row(1)["status"],"failed")

    def test_reparse_point_is_refused_without_following_target(self):
        target=self.source("target.txt",b"keep")
        link=self.orig/'link.txt'
        try:os.symlink(target,link)
        except OSError:self.skipTest("symlink creation unavailable for this Windows user")
        with self.assertRaises(OSError):self.store.move(str(link))
        self.assertEqual(target.read_bytes(),b"keep")

    def test_live_process_mutex_blocks_recovery_until_owner_releases(self):
        qid=self.store.move(str(self.source()))
        self.db.execute("UPDATE quarantine SET status='moving' WHERE id=?",(qid,))
        self.db.commit()
        script = "import sys\nfrom quarantine_store import operation_lock\nwith operation_lock(sys.argv[1]):\n print('held',flush=True)\n sys.stdin.readline()\n"
        flags=0x08000000 if os.name=='nt' else 0
        child=subprocess.Popen([sys.executable,"-B","-c",script,str(self.root)],cwd=pathlib.Path(__file__).resolve().parent.parent,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=flags)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
                ready=workers.submit(child.stdout.readline)
                self.assertEqual(ready.result(timeout=5).strip(),"held")
                recovery=workers.submit(self.store.listing)
                time.sleep(0.1)
                self.assertFalse(recovery.done())
                self.assertEqual(self.row(qid)["status"],"moving")
                child.communicate("release\n",timeout=5)
                self.assertEqual(recovery.result(timeout=5)[0]["status"],"held")
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)

    def test_failed_purge_is_retried_and_failed_item_stays_restorable(self):
        """مراجعة 19/11 البند 4: عنصر فشل حذفه (ملف مستخدم) يصير failed ولا يبقى عالقًا: التفريغ التالي يعيد المحاولة، والاسترجاع متاح."""
        a = self.store.move(str(self.source("a.txt", b"aa")))
        b = self.store.move(str(self.source("b.txt", b"bb")))
        self.expire(a); self.expire(b)
        with mock.patch.object(qs.safedel, "remove_tree", side_effect=PermissionError(32, "in use")):
            self.store.purge()
        self.assertEqual((self.row(a)["status"], self.row(b)["status"]), ("failed", "failed"))
        self.assertTrue(pathlib.Path(self.row(a)["qpath"]).exists())
        self.assertTrue(self.store.restore(b)["ok"])            # failed مع بقاء المحتوى: يُسترجع
        self.assertEqual((self.orig / "b.txt").read_bytes(), b"bb")
        self.store.purge()                                       # إعادة المحاولة تنجح الآن
        self.assertEqual(self.row(a)["status"], "purged")
        self.assertFalse(pathlib.Path(self.row(a)["qpath"]).exists())

    def test_failed_row_without_content_is_not_retried(self):
        """failed بلا محتوى في الحجر (نقل لم يتم): لا تفريغ ولا استرجاع؛ يتولاه _sweep."""
        original = self.source()
        with mock.patch.object(qs, "_rename_no_replace", side_effect=OSError("busy")):
            with self.assertRaises(OSError):
                self.store.move(str(original))
        self.expire(1)
        self.store.purge(force=True)
        self.assertEqual(self.row(1)["status"], "failed")
        self.assertFalse(self.store.restore(1)["ok"])
        self.assertEqual(original.read_bytes(), b"original")

    def test_restored_paths_are_reported_for_the_cleanup_to_skip(self):
        """البند 5: المسترجَع يحتفظ بوقته القديم؛ المخزن يبلّغ مساره فيتخطاه التنظيف الساعي."""
        original = self.source("big.bin", b"big")
        qid = self.store.move(str(original))
        self.assertEqual(self.store.restored_paths(), set())
        self.assertTrue(self.store.restore(qid)["ok"])
        self.assertEqual(self.store.restored_paths(), {os.path.normcase(os.path.abspath(original))})

    def test_purge_never_follows_a_junction_planted_in_the_quarantine(self):
        """الحذف بالمقبض: junction زُرع داخل عنصر محجوز يُحذف كرابط ولا يُنزل فيه."""
        if os.name != "nt":
            self.skipTest("Windows only")
        import _winapi
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "victim.txt").write_bytes(b"keep")
        folder = self.orig / "folder"
        folder.mkdir()
        (folder / "f.txt").write_bytes(b"f")
        qid = self.store.move(str(folder))
        qpath = pathlib.Path(self.row(qid)["qpath"])
        with mock.patch.object(qs, "_safe_tree", lambda p: None):   # الزرع بعد الفحص النصي
            _winapi.CreateJunction(str(outside), str(qpath / "planted"))
            self.store.purge(force=True)
        self.assertEqual((outside / "victim.txt").read_bytes(), b"keep")
        self.assertEqual(self.row(qid)["status"], "purged")
        self.assertFalse(qpath.exists())

    def test_cross_volume_move_refused_before_intent(self):
        original=self.source()
        real=os.stat
        original_name=os.path.normcase(os.path.abspath(original))
        def drive(path,*args,**kwargs):
            info=real(path,*args,**kwargs)
            if os.path.normcase(os.path.abspath(path)) == original_name:
                return types.SimpleNamespace(st_dev=info.st_dev+1,st_ino=info.st_ino,st_mode=info.st_mode)
            return info
        with mock.patch.object(qs.os,"stat",side_effect=drive):
            with self.assertRaises(OSError):self.store.move(str(original))
        self.assertTrue(original.exists())
        self.assertEqual(self.db.execute("SELECT count(*) FROM quarantine").fetchone()[0],0)


if __name__=="__main__":unittest.main(verbosity=2)
