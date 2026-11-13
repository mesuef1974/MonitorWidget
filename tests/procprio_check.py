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


if __name__ == "__main__":
    unittest.main(verbosity=1)
