"""بنود سلامة البيانات من المجلس 19/11 (13 و16 و17) في شيفرة المستودع، بلا Docker ولا قرص ولا صلاحيات:
ملف swap.vhdx لـ WSL لا يدخل الحجر، ووحدات Docker المجهولة لا يُحذف منها إلا ما أكّده المالك واحدةً واحدة،
وتنظيف swdist يُرفض أثناء تحديث جارٍ أو قبل إعادة تشغيل معلّقة."""
import ast
import json
import os
import re
import threading
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def extract(fname, names, assigns=(), **ns):
    tree = ast.parse((ROOT / fname).read_text(encoding="utf-8-sig"))
    body = [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") in assigns for t in n.targets)]
    body += [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    env = {"os": os, "re": re, "json": json, "time": time, "threading": threading, **ns}
    exec(compile(ast.Module(body=body, type_ignores=[]), "isolated-" + fname, "exec"), env)
    return env


class WslSwap(unittest.TestCase):
    def test_only_the_wsl_swap_file_under_a_guid_folder_of_temp(self):
        temp = r"C:\Users\u\AppData\Local\Temp"
        ns = extract("space.py", {"_wsl_swap"}, ("_SWAP_RE",), TEMP=temp)
        f = ns["_wsl_swap"]
        self.assertTrue(f(temp + r"\8E0B5F2A-1C3D-4E5F-A6B7-C8D9E0F1A2B3\swap.vhdx"))
        self.assertTrue(f(temp + r"\{8e0b5f2a-1c3d-4e5f-a6b7-c8d9e0f1a2b3}\SWAP.VHDX"))
        self.assertFalse(f(temp + r"\swap.vhdx"))                                          # not in a GUID folder
        self.assertFalse(f(temp + r"\installer\swap.vhdx"))
        self.assertFalse(f(temp + r"\8E0B5F2A-1C3D-4E5F-A6B7-C8D9E0F1A2B3\ext4.vhdx"))     # a distro disk is never this
        self.assertFalse(f(temp + r"\x\8E0B5F2A-1C3D-4E5F-A6B7-C8D9E0F1A2B3\swap.vhdx"))   # deeper: not WSL's layout

    def test_temp_clean_skips_the_quarantine_for_the_swap_file(self):
        src = (ROOT / "space.py").read_text(encoding="utf-8-sig")
        self.assertIn("sz >= Q_MIN_BYTES and not _wsl_swap(p)", src)


class DockerVolumes(unittest.TestCase):
    NAMES = ("a" * 64, "b" * 64, "c" * 64)

    def ns(self):
        calls = []

        def docker(args, timeout=300):
            calls.append(args)
            if args[:2] == ["volume", "inspect"]:
                out = "\n".join(json.dumps({"Name": n, "CreatedAt": "2026-11-01T10:20:30Z", "Labels": {"com.docker.compose.project": "p"}}) for n in args[3:])
                return types.SimpleNamespace(returncode=0, stdout=out)
            return types.SimpleNamespace(returncode=0, stdout="")

        res = lambda dry, mb, files=0, skipped=0, **kw: {"ok": True, "dry": dry, "mb": round(mb, 1), "files": files, "skipped": skipped, **kw}
        ns = extract("extras.py", {"_dockervolumes", "_docker_volume_info"},
                     space=types.SimpleNamespace(_docker=docker), _res=res, _docker_up=lambda: (True, "27"),
                     _docker_volumes_list=lambda: [(n, 1.0) for n in self.NAMES])
        return ns, calls

    def test_preview_lists_each_volume_with_its_details_and_removes_nothing(self):
        ns, calls = self.ns()
        r = ns["_dockervolumes"](True, "manual")
        self.assertEqual([v["name"] for v in r["volumes"]], list(self.NAMES))
        self.assertEqual(r["volumes"][0]["created"], "2026-11-01 10:20")
        self.assertEqual(r["volumes"][0]["project"], "p")
        self.assertFalse(any(a[:2] == ["volume", "rm"] for a in calls))

    def test_real_run_without_confirmation_removes_nothing(self):
        ns, calls = self.ns()
        for sel in (None, (), []):
            r = ns["_dockervolumes"](False, "manual", sel)
            self.assertFalse(r["ok"])
        self.assertFalse(any(a[:2] == ["volume", "rm"] for a in calls))

    def test_only_confirmed_names_still_anonymous_are_removed(self):
        ns, calls = self.ns()
        r = ns["_dockervolumes"](False, "manual", (self.NAMES[1], "d" * 64))   # d: confirmed but no longer a candidate
        self.assertEqual([a for a in calls if a[:2] == ["volume", "rm"]], [["volume", "rm", self.NAMES[1]]])
        self.assertEqual(r["files"], 1)

    def test_clean_start_refuses_dockervolumes_without_a_selection(self):
        ns = extract("space.py", {"clean_start", "clean_status"}, _previewable=lambda k: True, _cj={}, _cj_lock=threading.Lock(),
                     BULK=("temp",), _clean_run=lambda *a: None)
        r = ns["clean_start"]("dockervolumes", 100)
        self.assertFalse(r["ok"])
        self.assertEqual(ns["_cj"], {})
        ok = ns["clean_start"]("dockervolumes", 100, volumes=[self.NAMES[0]])
        self.assertTrue(ok["ok"])
        self.assertEqual(ns["_cj"]["dockervolumes"]["volumes"], (self.NAMES[0],))

    def test_route_accepts_only_anonymous_volume_names(self):
        src = (ROOT / "routes_destructive.py").read_text(encoding="utf-8-sig")
        self.assertIn('VOL_RE = re.compile(r"[0-9a-f]{64}")', src)
        self.assertIn('kind == "dockervolumes"', src)
        self.assertIn("VOL_RE.fullmatch(v)", src)

    def test_page_confirms_each_volume_on_its_own(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('k === "dockervolumes"', html)
        self.assertIn("for (const [i, v] of vols.entries())", html)
        self.assertIn("volumes: pick", html)


class SwDistGuard(unittest.TestCase):
    def test_swdist_is_refused_while_an_update_runs_or_a_reboot_is_pending(self):
        ps = (ROOT / "elevated_clean.ps1").read_text(encoding="utf-8-sig")
        self.assertIn(r"WindowsUpdate\Auto Update\RebootRequired", ps)
        self.assertIn(r"Component Based Servicing\RebootPending", ps)
        self.assertIn("'TiWorker', 'TrustedInstaller', 'MoUsoCoreWorker', 'wuauclt'", ps)
        # the check comes before the deletion branch and only for swdist; wintemp is unaffected
        i_busy, i_clean = ps.index("$Kind -eq 'swdist' -and ($busy = UpdateBusy)"), ps.index("[MwSafeDel]::Clean(")
        self.assertLess(i_busy, i_clean)


if __name__ == "__main__":
    unittest.main()
