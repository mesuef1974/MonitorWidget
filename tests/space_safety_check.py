"""أمان الحذف وتصحيحات مراجعة المساحة (19/11/2026): نقطة إعادة توجيه تُزرع بين السرد والحذف لا توجّه الحذف خارج الجذر،
والرابط يُحذف كرابط، وتراجع DISM الشهري بعد الفشل، وملف أوامر diskpart باسم عشوائي في المجلد المحمي ونجاحه برمز الخروج،
وDocker يسجّل المحرَّر الفعلي. معزول: مجلدات مؤقتة تحت tests/.sandboxes فقط، بلا خادم ولا قاعدة حية ولا تنظيف حقيقي.

Run: python -X utf8 -B tests/space_safety_check.py
"""
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlite_runtime import ensure_sqlite   # noqa: E402
ensure_sqlite()
import safedel   # noqa: E402

WIN = os.name == "nt"


def junction(link, target):
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


class Sandbox(unittest.TestCase):
    def setUp(self):
        parent = ROOT / "tests" / ".sandboxes"
        parent.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="spacesafe_", dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "root"
        self.outside = self.base / "outside"
        self.root.mkdir()
        self.outside.mkdir()
        self.victim = self.outside / "victim.txt"
        self.victim.write_bytes(b"keep")

    def swap_for_junction(self, folder):
        """يحاكي برنامجًا عاديًا: يحذف المجلد الحقيقي ويضع مكانه junction إلى خارج الجذر."""
        for p in sorted(folder.rglob("*"), reverse=True):
            p.rmdir() if p.is_dir() else p.unlink()
        folder.rmdir()
        junction(folder, self.outside)


@unittest.skipUnless(WIN, "Windows only")
class SafeDelete(Sandbox):
    def test_planted_junction_in_a_component_is_refused(self):
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "victim.txt").write_bytes(b"x")
        guard = safedel.Root(self.root)
        self.swap_for_junction(sub)
        with self.assertRaises(OSError):
            guard.remove(sub / "victim.txt")
        self.assertEqual(self.victim.read_bytes(), b"keep")

    def test_handle_check_refuses_even_if_the_component_check_is_raced(self):
        """لو زُرع الرابط بعد فحص المكوّنات وقبل فتح المقبض: المسار الحقيقي من المقبض خارج الجذر فيُرفض."""
        sub = self.root / "sub"
        junction(sub, self.outside)
        guard = safedel.Root(self.root)
        with mock.patch.object(safedel, "check_chain", lambda p, r: p):
            with self.assertRaises(safedel.UnsafePath):
                guard.remove(sub / "victim.txt")
        self.assertEqual(self.victim.read_bytes(), b"keep")

    def test_junction_itself_is_removed_as_a_link(self):
        link = self.root / "link"
        junction(link, self.outside)
        safedel.Root(self.root).remove(link)
        self.assertFalse(os.path.lexists(link))
        self.assertEqual(self.victim.read_bytes(), b"keep")

    def test_root_reached_through_a_junction_is_refused(self):
        link = self.base / "rootlink"
        junction(link, self.root)
        with self.assertRaises(OSError):
            safedel.Root(link)
        sub = self.base / "real"
        sub.mkdir()
        inner = self.base / "via"
        junction(inner, sub)
        (sub / "child").mkdir()
        with self.assertRaises(OSError):
            safedel.Root(inner / "child")   # الجذر سليم لكن أحد آبائه رابط

    def test_folder_only_when_empty_and_errors_keep_their_codes(self):
        d = self.root / "d"
        d.mkdir()
        (d / "x").write_bytes(b"x")
        guard = safedel.Root(self.root)
        with self.assertRaises(OSError) as cm:
            guard.remove(d)
        self.assertEqual(cm.exception.winerror, 145)
        with open(d / "x", "rb"):
            with self.assertRaises(OSError) as cm:
                guard.remove(d / "x")
        self.assertEqual(cm.exception.winerror, 32)
        with self.assertRaises(OSError):
            guard.remove(self.outside / "victim.txt")   # خارج الجذر نصًّا
        safedel.remove_tree(d, guard)
        self.assertFalse(d.exists())

    def test_remove_tree_never_descends_into_a_link(self):
        tree = self.root / "tree"
        (tree / "a").mkdir(parents=True)
        (tree / "a" / "f").write_bytes(b"f")
        junction(tree / "a" / "out", self.outside)
        safedel.remove_tree(tree, self.root)
        self.assertFalse(tree.exists())
        self.assertEqual(self.victim.read_bytes(), b"keep")

    def test_move_by_handle_never_replaces_and_refuses_a_planted_destination(self):
        q = self.base / "q"
        (q / "op").mkdir(parents=True)
        src = self.root / "m.txt"
        src.write_bytes(b"m")
        safedel.Root(self.root).move(src, q, q / "op" / "m.txt")
        self.assertEqual((q / "op" / "m.txt").read_bytes(), b"m")
        src.write_bytes(b"new")
        with self.assertRaises(OSError) as cm:
            safedel.Root(self.root).move(src, q, q / "op" / "m.txt")
        self.assertEqual(cm.exception.winerror, 183)
        # الوجهة: مجلدها صار junction إلى الخارج بعد الفحص النصي -> يُرفض ولا يُكتب شيء خارج الجذر
        (q / "op2").mkdir()
        self.swap_for_junction(q / "op2")
        with mock.patch.object(safedel, "check_chain", lambda p, r: p):
            with self.assertRaises(safedel.UnsafePath):
                safedel.Root(self.root).move(src, q, q / "op2" / "m.txt")
        self.assertFalse((self.outside / "m.txt").exists())
        self.assertEqual(src.read_bytes(), b"new")


@unittest.skipUnless(WIN, "Windows only")
class PlantedAfterListing(Sandbox):
    """الانحدار المطلوب: السرد يرى مجلدًا حقيقيًا، ثم يُستبدل بـ junction قبل الحذف."""

    def setUp(self):
        super().setUp()
        import space
        self.sp = space
        self.sub = self.root / "sub"
        self.sub.mkdir()
        (self.sub / "victim.txt").write_bytes(b"x")
        for obj, name, fake in ((space, "scan", lambda *a, **k: None), (space.errlog, "swallowed", lambda *a, **k: None),
                                (space, "_c", self.no_db)):
            pt = mock.patch.object(obj, name, fake)
            pt.start()
            self.addCleanup(pt.stop)

    @staticmethod
    def no_db():
        raise RuntimeError("no database in tests")

    def planting(self, real):
        """يغلّف مولّد السرد: يجمعه كله ثم يزرع الرابط ثم يسلّم النتائج (كما لو حدث الزرع بين السرد والحذف)."""
        def wrapped(*a, **k):
            rows = list(real(*a, **k))
            self.swap_for_junction(self.sub)
            yield from rows
        return wrapped

    def test_purge_dir(self):
        with mock.patch.object(self.sp, "_walk", self.planting(self.sp._walk)):
            freed, n, skipped = self.sp._purge_dir(str(self.root))
        self.assertEqual(self.victim.read_bytes(), b"keep")
        self.assertEqual((n, skipped), (0, 1))
        self.assertTrue(self.outside.is_dir())   # الرابط المزروع حُذف كرابط فقط؛ هدفه سليم

    def test_temp_clean(self):
        old = os.path.getmtime(self.sub / "victim.txt") - 3 * 86400
        os.utime(self.sub / "victim.txt", (old, old))
        with mock.patch.object(self.sp, "TEMP", str(self.root)), mock.patch.object(self.sp, "_setting", lambda k, d=None: []), \
                mock.patch.object(self.sp, "_restored_paths", lambda: set()), \
                mock.patch.object(self.sp, "_old_files", self.planting(self.sp._old_files)), \
                mock.patch.object(self.sp, "_rm_empty_dirs", lambda *a, **k: 0):
            r = self.sp._clean_impl("temp", dry=False)
        self.assertEqual(self.victim.read_bytes(), b"keep")
        self.assertEqual((r["files"], r["skipped"]), (0, 1))

    def test_rm_empty_dirs(self):
        empty = self.root / "empty"
        empty.mkdir()
        dirs = [(str(empty), 0)]
        os.rmdir(empty)
        inner = self.outside / "inner"
        inner.mkdir()
        junction(self.root / "j", self.outside)
        n = self.sp._rm_empty_dirs(str(self.root), [(str(self.root / "j" / "inner"), 0)] + dirs, 1)
        self.assertEqual(n, 0)
        self.assertTrue(inner.is_dir())   # المجلد الفارغ خارج الجذر لم يُحذف عبر الرابط

    def test_extras_light(self):
        import extras
        spec = dict(extras.LIGHT["pipcache"], roots=lambda: [str(self.root)], guard=(), days=0, admin=False)
        with mock.patch.dict(extras.LIGHT, {"pipcache": spec}), mock.patch.object(extras, "_blocked_by", lambda g: []),                 mock.patch.object(self.sp, "_walk", self.planting(self.sp._walk)):
            r = extras._light("pipcache", False, "test")
        self.assertEqual(self.victim.read_bytes(), b"keep")
        self.assertEqual(r["files"], 0)

    def test_restored_file_is_skipped_by_the_temp_clean(self):
        """البند 5: الملف المسترجَع من الحجر يحتفظ بوقته القديم؛ لا يُحذف ولا يُحجر ثانيةً ما دام هو الملف نفسه (الهوية)،
        أما ملف جديد بالاسم نفسه فيُنظَّف كالمعتاد."""
        import quarantine_store
        f = self.root / "restored.bin"
        f.write_bytes(b"r")
        old = os.path.getmtime(f) - 3 * 86400
        os.utime(f, (old, old))
        os.utime(self.sub / "victim.txt", (old, old))
        keep = {os.path.normcase(os.path.abspath(f)): quarantine_store._identity(str(f))}

        def run():
            with mock.patch.object(self.sp, "TEMP", str(self.root)), mock.patch.object(self.sp, "_setting", lambda k, d=None: []), \
                    mock.patch.object(self.sp, "_restored_paths", lambda: keep), mock.patch.object(self.sp, "_rm_empty_dirs", lambda *a, **k: 0):
                return self.sp._clean_impl("temp", dry=False)
        r = run()
        self.assertTrue(f.exists())
        self.assertFalse((self.sub / "victim.txt").exists())
        self.assertEqual(r["files"], 1)
        f.unlink()                     # ملف جديد بالاسم نفسه: هوية أخرى
        f.write_bytes(b"new")
        os.utime(f, (old, old))
        run()
        self.assertFalse(f.exists())


@unittest.skipUnless(WIN, "Windows only")
class PosixDeleteAndMakedirs(Sandbox):
    def test_open_file_with_delete_sharing_vanishes_at_once_and_its_parent_goes(self):
        """FileDispositionInfoEx بـ POSIX: الاسم يختفي فورًا ولو بقي الملف مفتوحًا، فيُحذف أبوه الفارغ مباشرة."""
        d = self.root / "d"
        d.mkdir()
        f = d / "open.txt"
        f.write_bytes(b"x")
        h = safedel._k32.CreateFileW(str(f), 0x80000000, 7, None, 3, 0, None)   # GENERIC_READ، مشاركة القراءة والكتابة والحذف
        self.assertNotEqual(h, safedel._INVALID)
        try:
            guard = safedel.Root(self.root)
            guard.remove(f)
            self.assertFalse(os.path.lexists(f))
            guard.remove(d)
            self.assertFalse(d.exists())
        finally:
            safedel._k32.CloseHandle(h)

    def test_readonly_file_is_still_refused(self):
        f = self.root / "ro.txt"
        f.write_bytes(b"x")
        os.chmod(f, 0o444)
        self.addCleanup(os.chmod, f, 0o666)
        with self.assertRaises(OSError):
            safedel.Root(self.root).remove(f)
        self.assertTrue(f.exists())

    def test_makedirs_by_handle_never_creates_through_a_junction(self):
        guard = safedel.Root(self.root)
        guard.makedirs(self.root / "a" / "b")
        self.assertTrue((self.root / "a" / "b").is_dir())
        guard.makedirs(self.root / "a" / "b")   # موجود: مقبول
        astral = "ملف_\U0001F600_\U00020000"   # محارف خارج BMP: طول UNICODE_STRING بالبايتات لا بعدد المحارف
        guard.makedirs(self.root / astral / "c")
        self.assertIn(astral, os.listdir(self.root))
        self.assertTrue((self.root / astral / "c").is_dir())
        junction(self.root / "j", self.outside)
        with self.assertRaises(OSError):
            guard.makedirs(self.root / "j" / "planted")
        self.assertFalse((self.outside / "planted").exists())
        with self.assertRaises(OSError):
            guard.makedirs(self.outside / "x")


PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"


@unittest.skipUnless(WIN and os.path.isfile(PWSH), "pwsh 7 required")
class ElevatedCleanByHandle(Sandbox):
    """elevated_clean.ps1 (wintemp/swdist): MwSafeDel بالمقبض؛ junction يُزرع بعد السرد لا يوجّه الحذف المرتفع خارج الجذر.
    يُشغَّل mwsafedel.cs نفسه (الذي يحمّله السكربتان المرتفعان) في pwsh بلا نافذة وبلا صلاحية مسؤول، على مجلدات الاختبار فقط."""

    def run_ps(self, body):
        import subprocess
        script = self.base / "harness.ps1"
        script.write_text("$ErrorActionPreference = 'Stop'\nAdd-Type -Path '%s'\n%s\n"
                          % (ROOT / "mwsafedel.cs", body), encoding="utf-8-sig")
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(script)], capture_output=True, text=True, timeout=120,
                           creationflags=0x08000000)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip().splitlines()[-1]

    def test_planted_junction_after_listing_is_not_followed(self):
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "victim.txt").write_bytes(b"x")
        (self.root / "plain.txt").write_bytes(b"p")
        out = self.run_ps(
            "$plant = [Action]{ Remove-Item -LiteralPath '%s' -Recurse -Force; New-Item -ItemType Junction -Path '%s' -Target '%s' | Out-Null }\n"
            "$r = [MwSafeDel]::Clean('%s', [DateTime]::UtcNow.AddDays(1), $plant)\n'{0} {1} {2}' -f $r[0], $r[1], $r[2]"
            % (sub, sub, self.outside, self.root))
        self.assertEqual(out, "1 1 1")   # plain.txt حُذف، والمزروع رُفض
        self.assertEqual(self.victim.read_bytes(), b"keep")
        self.assertFalse((self.root / "plain.txt").exists())

    def test_root_through_junction_and_age_cut(self):
        junction(self.base / "rootlink", self.root)
        out = self.run_ps("try { [MwSafeDel]::Clean('%s', [DateTime]::UtcNow, $null) | Out-Null; 'BAD' } catch { 'refused' }"
                          % (self.base / "rootlink"))
        self.assertEqual(out, "refused")
        (self.root / "new.txt").write_bytes(b"n")
        out = self.run_ps("$r = [MwSafeDel]::Clean('%s', [DateTime]::UtcNow.AddDays(-3), $null)\n'{0}' -f $r[0]" % self.root)
        self.assertEqual(out, "0")
        self.assertTrue((self.root / "new.txt").exists())

    def test_long_path_and_readonly_files_are_deleted(self):
        """مسار يتجاوز 260 حرفًا يُفتح بـ \\\\?\\ (pwsh لا يعلن longPathAware)، وملف القراءة فقط يُحذف كما كان Remove-Item -Force يفعل."""
        deep = "\\\\?\\" + str(self.root)
        for i in range(12):
            deep += "\\" + ("d%02d_" % i) + "x" * 20
        os.makedirs(deep)
        longf = deep + "\\long.txt"
        with open(longf, "wb") as fh:
            fh.write(b"L")
        self.assertGreater(len(longf) - 4, 260)
        ro = self.root / "ro.txt"
        ro.write_bytes(b"r")
        os.chmod(ro, 0o444)
        self.addCleanup(lambda: ro.exists() and os.chmod(ro, 0o666))
        out = self.run_ps("$r = [MwSafeDel]::Clean('%s', [DateTime]::UtcNow.AddDays(1), $null)\n'{0} {1}' -f $r[0], $r[2]" % self.root)
        self.assertEqual(out, "2 0")
        self.assertFalse(os.path.lexists(longf))
        self.assertFalse(ro.exists())
        self.assertEqual(os.listdir(self.root), [])   # المجلدات الفارغة الطويلة حُذفت أيضًا

    def test_script_uses_the_handle_class_not_remove_item(self):
        src = (ROOT / "elevated_clean.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("[MwSafeDel]::Clean(", src)
        self.assertIn("Add-Type -Path (Join-Path $PSScriptRoot 'mwsafedel.cs')", src)
        self.assertNotRegex(src, r"Remove-Item\s+-LiteralPath")

    def test_purge_removes_everything_but_never_crosses_a_link(self):
        """Windows.old: كل شيء يُحذف مع الجذر، والوصلة تُحذف كوصلة فيبقى هدفها خارج الجذر سليمًا بمالكه وACL."""
        (self.root / "a" / "b").mkdir(parents=True)
        (self.root / "a" / "b" / "deep.txt").write_bytes(b"d")
        ro = self.root / "a" / "ro.txt"
        ro.write_bytes(b"r")
        os.chmod(ro, 0o444)
        self.addCleanup(lambda: ro.exists() and os.chmod(ro, 0o666))
        junction(self.root / "Documents and Settings", self.outside)
        junction(self.root / "a" / "Application Data", self.outside)
        out = self.run_ps("$r = [MwSafeDel]::Purge('%s', $null)\n'{0} {1} {2}' -f $r[0], $r[1], $r[2]" % self.root)
        self.assertEqual(out, "4 2 0")   # ملفّان ووصلتان
        self.assertFalse(self.root.exists())
        self.assertEqual(self.victim.read_bytes(), b"keep")

    def test_purge_refuses_a_root_that_is_a_link_and_a_link_planted_after_listing(self):
        junction(self.base / "rootlink", self.root)
        out = self.run_ps("try { [MwSafeDel]::Purge('%s', $null) | Out-Null; 'BAD' } catch { 'refused' }" % (self.base / "rootlink"))
        self.assertEqual(out, "refused")
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "victim.txt").write_bytes(b"x")
        out = self.run_ps(
            "$plant = [Action]{ Remove-Item -LiteralPath '%s' -Recurse -Force; New-Item -ItemType Junction -Path '%s' -Target '%s' | Out-Null }\n"
            "$r = [MwSafeDel]::Purge('%s', $plant)\n'{0} {1}' -f $r[0], $r[2]" % (sub, sub, self.outside, self.root))
        self.assertEqual(out, "0 1")   # sub\victim.txt صار عبر الوصلة فرُفض
        self.assertEqual(self.victim.read_bytes(), b"keep")
        self.assertTrue(self.outside.is_dir())
        self.assertFalse(self.root.exists())   # الوصلة المزروعة حُذفت كوصلة (مرحلة المجلدات) فخلا الجذر وحُذف

    def test_enable_backup_restore_fails_loudly_without_the_privileges(self):
        """بلا مسؤول: الامتيازان غير موجودين في الرمز، فيُرفع خطأ صريح (1300) ولا يتابع الحذف صامتًا."""
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            self.skipTest("elevated runner")
        out = self.run_ps("try { [MwSafeDel]::EnableBackupRestore(); 'enabled' } catch { 'refused' }")
        self.assertEqual(out, "refused")


class DismManualOnly(unittest.TestCase):
    """البند 32 من المجلس 19/11: DISM يدوي فقط (ويندوز يشغّل StartComponentCleanup بمهمته)؛ لا مهمة شهرية ولا مفتاح لها."""

    def test_no_monthly_job(self):
        import extras
        self.assertFalse(hasattr(extras, "dism_monthly"))
        self.assertIn("dism", extras.KINDS)   # الزر اليدوي باقٍ
        src = open(os.path.join(ROOT, "routes_destructive.py"), encoding="utf-8").read()
        self.assertNotIn("dismmonthly", src)


class CrashDumpsKeepNewest(unittest.TestCase):
    """البند 14 والبند 32: الزر اليدوي لتقارير الأعطال يبقي أحدث 3 ملفات في كل مجلد مهما كان عمرها، كقاعدة المحرّك."""

    def test_newest_three_stay(self):
        import extras
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        old = time.time() - 30 * 86400
        for n in range(6):
            p = os.path.join(d, "dump%d.dmp" % n)
            with open(p, "wb") as f:
                f.write(b"x" * 10)
            os.utime(p, (old + n * 60, old + n * 60))
        spec = dict(extras.LIGHT["crashdumps"], roots=lambda: [d])
        with mock.patch.dict(extras.LIGHT, {"crashdumps": spec}), \
             mock.patch.object(extras.space, "scan", lambda *a, **k: None), \
             mock.patch.object(extras.space, "_c", lambda: types.SimpleNamespace(execute=lambda *a: None, commit=lambda: None)):
            r = extras._light("crashdumps", False, "manual")
        self.assertTrue(r["ok"])
        self.assertEqual(sorted(os.listdir(d)), ["dump3.dmp", "dump4.dmp", "dump5.dmp"])


class Diskpart(unittest.TestCase):
    """البند 3: ملف الأوامر باسم عشوائي في المجلد المحمي لا في %TEMP%، ويُحذف بعد التنفيذ، والنجاح برمز الخروج."""

    def test_script_lives_in_protected_dir_with_random_name_and_is_removed(self):
        import extras
        parent = ROOT / "tests" / ".sandboxes"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="diskpart_", dir=parent) as out:
            seen = []

            def fake_run(cmd, **k):
                seen.append((cmd[2], Path(cmd[2]).read_text(encoding="ascii")))
                return types.SimpleNamespace(returncode=0, stdout="")
            with mock.patch.object(extras.secure, "OUT", out), mock.patch.object(extras.subprocess, "run", fake_run):
                extras._run_diskpart("select vdisk file=\"x\"\nexit\n")
                extras._run_diskpart("exit\n")
            (p1, body), (p2, _) = seen
            self.assertEqual(os.path.normcase(os.path.dirname(p1)), os.path.normcase(out))
            self.assertNotEqual(p1, p2)
            self.assertIn("select vdisk", body)
            self.assertFalse(os.path.exists(p1) or os.path.exists(p2))
            self.assertEqual(os.listdir(out), [])

    def test_success_is_the_exit_code_not_english_text(self):
        src = (ROOT / "extras.py").read_text(encoding="utf-8")
        self.assertNotIn("100 percent", src)
        self.assertNotIn('"TEMP", LA), "mw_compact', src)
        self.assertIn("ok = r.returncode == 0", src)


class DockerActual(unittest.TestCase):
    """البند 6: Docker يسجّل حجم ما حُذف فعلًا لا التقدير كله."""

    def test_images_and_volumes_count_only_what_was_removed(self):
        import extras
        out = {"images": "aaa|1GB\nbbb|2GB\n"}

        def docker(args, timeout=300):
            if args[0] == "images":
                return types.SimpleNamespace(returncode=0, stdout=out["images"])
            if args[0] == "rmi":
                return types.SimpleNamespace(returncode=0 if args[1] == "aaa" else 1, stdout="")
            if args[0] == "volume":
                return types.SimpleNamespace(returncode=0 if args[2] == "v1" else 1, stdout="")
            return types.SimpleNamespace(returncode=0, stdout="")
        with mock.patch.object(extras, "_docker_up", lambda: (True, "1")), mock.patch.object(extras.space, "_docker", docker), \
                mock.patch.object(extras, "_docker_volumes_list", lambda: [("v1", 0.5), ("v2", 4.0)]):
            r = extras._dockerimages(False, "test")
            self.assertEqual((r["mb"], r["files"], r["skipped"], r["expectedMB"]), (1024.0, 1, 1, 3072.0))
            r = extras._dockervolumes(False, "test", ("v1", "v2"))   # both confirmed by the owner (council 19/11 item 16)
            self.assertEqual((r["mb"], r["files"], r["skipped"], r["expectedMB"]), (512.0, 1, 1, 4608.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
