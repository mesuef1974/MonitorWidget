# -*- coding: utf-8 -*-
"""procprio: عملية وُلدت بأولوية مجدول المهام (أقل من العادي) ترفع نفسها إلى العادي، ولا تُخفَض عملية أعلى.
كل حالة في عملية ابنة حقيقية بالأولوية المطلوبة؛ لا شيء يمسّ عملية الاختبار نفسها."""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHILD = "import json, sys; sys.path.insert(0, sys.argv[1]); import procprio; print(json.dumps(procprio.restore_normal(io=sys.argv[2] == '1')))"
BELOW, IDLE, NORMAL, ABOVE = 0x4000, 0x40, 0x20, 0x8000


def run(flags, io=True):
    out = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", CHILD, ROOT, "1" if io else "0"], capture_output=True, text=True,
                         timeout=60, creationflags=flags | 0x08000000)
    return json.loads(out.stdout)


@unittest.skipUnless(os.name == "nt", "ويندوز فقط")
class ProcPrio(unittest.TestCase):
    def test_below_normal_and_idle_children_raise_themselves_to_normal(self):
        for flags in (BELOW, IDLE):
            r = run(flags)
            self.assertEqual(r["before"]["cpu"], flags)
            self.assertEqual(r["after"]["cpu"], NORMAL)

    def test_io_and_page_priority_reach_normal_and_never_drop(self):
        r = run(IDLE)   # صنف الخمول يولد بأولوية ذاكرة منخفضة
        self.assertGreaterEqual(r["after"]["io"], 2)
        self.assertGreaterEqual(r["after"]["page"], 5)
        self.assertGreaterEqual(r["after"]["page"], r["before"]["page"])

    def test_io_false_changes_the_cpu_class_only(self):
        r = run(IDLE, io=False)
        self.assertEqual(r["after"]["cpu"], NORMAL)
        self.assertEqual((r["after"]["io"], r["after"]["page"]), (r["before"]["io"], r["before"]["page"]))

    def test_normal_and_higher_are_left_alone(self):
        for flags in (NORMAL, ABOVE):
            r = run(flags)
            self.assertEqual((r["before"]["cpu"], r["after"]["cpu"]), (flags, flags))
            # الإدخال/الإخراج والذاكرة قد يولدان منخفضين مع صنف عادي (مشغّل CI يورّثهما 1 و2): يُرفعان ولا يُخفضان أبدًا
            self.assertGreaterEqual(r["after"]["io"], max(2, r["before"]["io"]))
            self.assertGreaterEqual(r["after"]["page"], max(5, r["before"]["page"]))
            off = run(flags, io=False)
            self.assertEqual(off["after"], off["before"])

    def test_entry_points_call_it_before_any_work(self):
        with open(os.path.join(ROOT, "agent.py"), encoding="utf-8") as f:
            agent = f.read()
        with open(os.path.join(ROOT, "webboot.py"), encoding="utf-8") as f:
            boot = f.read()
        self.assertLess(agent.index("procprio.restore_normal(io=False)"), agent.index("db.bootstrap()"))
        self.assertLess(boot.index("procprio.restore_normal()"), boot.index("claim_single_server()", boot.index("def run(")))


BG_CHILD = r"""
import ctypes, json, sys; sys.path.insert(0, sys.argv[1]); import procprio
k, nt = ctypes.WinDLL("kernel32"), ctypes.WinDLL("ntdll")
k.GetCurrentThread.restype = ctypes.c_void_p; k.GetThreadPriority.argtypes = [ctypes.c_void_p]
nt.NtQueryInformationThread.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
def io():
    v = ctypes.c_ulong(99); nt.NtQueryInformationThread(k.GetCurrentThread(), 22, ctypes.byref(v), 4, None); return v.value
r = {"io0": io(), "prio0": k.GetThreadPriority(k.GetCurrentThread())}
with procprio.background_thread() as on:
    r["on"], r["io_in"] = on, io()
    with procprio.background_thread() as again:
        r["again"] = again
    r["io_still"] = io()
r["io1"], r["prio1"] = io(), k.GetThreadPriority(k.GetCurrentThread())
print(json.dumps(r))
"""


@unittest.skipUnless(os.name == "nt", "Windows only")
class BackgroundThread(unittest.TestCase):
    """البند 36 من المجلس 19/11: scan_big يعمل بأولوية قرص منخفضة جدًا للخيط وحده، وتعود الأولوية بعده."""
    def test_lowers_io_inside_and_restores_after(self):
        out = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", BG_CHILD, ROOT], capture_output=True, text=True, timeout=60)
        r = json.loads(out.stdout)
        self.assertTrue(r["on"]); self.assertEqual(r["io_in"], 0)          # IoPriorityVeryLow
        self.assertFalse(r["again"]); self.assertEqual(r["io_still"], 0)   # المتداخل لا ينهي وضع الخارجي
        self.assertEqual((r["io1"], r["prio1"]), (r["io0"], r["prio0"]))

    def test_scan_big_runs_inside_it(self):
        with open(os.path.join(ROOT, "space.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("def scan_big("):src.index("def _disk_peak(")]
        self.assertLess(body.index("procprio.background_thread()"), body.index("_scan_big(budget)"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
