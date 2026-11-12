"""سجل الإقلاعات (boothist): الحفظ المكرَّر الآمن وربط المسبّب بإقلاعه، والاتجاه (يتعلّم، ضمن المعتاد، أبطأ، أسرع) والمرحلة
التي زادت، والمسبّب الجديد، والتنبيه مرة لكل إقلاع، وتصحيح نوع الخدمة المسجَّلة كتطبيق. معزول: قاعدة في الذاكرة ولا سجل ويندوز.

Run: python -X utf8 -B tests/boothist_check.py
"""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import boothist as bh

T0 = 1794000000
DAY = 86400


def boot(i, sec, main=20.0, culprits=()):
    return {"ts": T0 + i * DAY, "bootSec": sec, "mainSec": main, "postSec": round(sec - main, 1), "culprits": list(culprits)}


class Record(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        self.now = T0 + 30 * DAY

    def probe(self, boots, culprits):
        return {"boots": [{"ts": T0 + i * DAY, "bootSec": s, "mainSec": 20.0, "postSec": s - 20.0, "explorerSec": 3.0} for i, s in boots], "bootCulprits": culprits}

    def test_saves_boots_links_culprits_within_5_minutes_and_is_idempotent(self):
        cu = [{"ts": T0 + 40, "id": 101, "name": "Claude", "totalSec": 41.6, "degSec": 36.6}, {"ts": T0 + 301, "id": 101, "name": "بعيد", "degSec": 9.0},
              {"ts": T0 + DAY, "id": 103, "name": "Svc", "degSec": 4.0, "kind": "service"}, {"ts": T0, "id": 101, "name": "", "degSec": 3.0}, {"ts": T0, "id": 101, "name": "بلا زمن"}]
        p = self.probe([(0, 46.0), (1, 36.3)], cu)
        self.assertEqual(bh.record(self.c, p, self.now), 2)
        self.assertEqual(bh.record(self.c, p, self.now), 2)
        rows = bh.rows(self.c)
        self.assertEqual([(r["ts"], r["bootSec"], [x["name"] for x in r["culprits"]]) for r in rows], [(T0, 46.0, ["Claude"]), (T0 + DAY, 36.3, ["Svc"])])
        self.assertEqual(rows[1]["culprits"][0]["kind"], "service")

    def test_old_probe_without_culprits_keeps_saved_ones_and_bad_rows_are_skipped(self):
        bh.record(self.c, self.probe([(0, 46.0)], [{"ts": T0, "id": 101, "name": "Claude", "degSec": 36.6}]), self.now)
        old = self.probe([(0, 46.0), (1, 30.0)], None)
        old["boots"] += [{"ts": None, "bootSec": 9}, {"ts": T0 + 5 * DAY}, "x"]
        self.assertEqual(bh.record(self.c, old, self.now), 2)
        rows = bh.rows(self.c)
        self.assertEqual([len(r["culprits"]) for r in rows], [1, 0])
        self.assertEqual(bh.record(self.c, None, self.now), 0)

    def test_prunes_boots_older_than_keep_days(self):
        bh.record(self.c, self.probe([(0, 40.0), (1, 41.0)], []), T0 + bh.KEEP_DAYS * DAY + DAY // 2)
        self.assertEqual([r["ts"] for r in bh.rows(self.c)], [T0 + DAY])


class Trend(unittest.TestCase):
    def test_learning_until_enough_boots(self):
        self.assertEqual(bh.trend([])["state"], "learning")
        self.assertIsNone(bh.trend([])["lastTs"])
        t = bh.trend([boot(i, 30 + i) for i in range(bh.RECENT + bh.BASE_MIN - 1)])
        self.assertEqual((t["state"], t["baseSec"], t["n"], t["need"]), ("learning", None, 6, 7))
        self.assertEqual(t["recentSec"], 34)

    def test_steady_slower_faster_use_medians_so_one_outlier_does_not_alarm(self):
        base = [boot(i, s) for i, s in enumerate([32, 31, 56, 33, 32])]   # قفزة واحدة قديمة لا تغيّر الوسيط
        steady = bh.trend(base + [boot(5, 35), boot(6, 36), boot(7, 60)])   # قفزة واحدة حديثة لا تنبّه
        self.assertEqual((steady["state"], steady["baseSec"], steady["recentSec"], steady["deltaSec"]), ("steady", 32, 36, 4))
        slower = bh.trend(base + [boot(5, 44, 20), boot(6, 46, 21), boot(7, 60, 20)])
        self.assertEqual((slower["state"], slower["deltaSec"], slower["deltaPct"], slower["phase"]), ("slower", 14, 44, "post"))
        main = bh.trend(base + [boot(5, 44, 33), boot(6, 46, 34), boot(7, 60, 35)])
        self.assertEqual(main["phase"], "main")
        faster = bh.trend([boot(i, 60) for i in range(5)] + [boot(5, 40), boot(6, 41), boot(7, 42)])
        self.assertEqual((faster["state"], faster["phase"]), ("faster", None))

    def test_slow_gate_needs_both_8_seconds_and_a_quarter_of_baseline(self):
        t = bh.trend([boot(i, 100) for i in range(4)] + [boot(4, 112), boot(5, 112), boot(6, 112)])   # +12 ث لكنها 12% فقط
        self.assertEqual(t["state"], "steady")
        t = bh.trend([boot(i, 20) for i in range(4)] + [boot(4, 26), boot(5, 26), boot(6, 26)])       # +30% لكنها 6 ث فقط
        self.assertEqual(t["state"], "steady")

    def test_baseline_is_the_last_20_before_the_recent_ones_and_order_does_not_matter(self):
        rows = [boot(i, 90) for i in range(10)] + [boot(10 + i, 30) for i in range(20)] + [boot(30 + i, 31) for i in range(3)]
        t = bh.trend(rows[::-1])
        self.assertEqual((t["baseSec"], t["state"], t["lastTs"], len(t["points"])), (30, "steady", T0 + 32 * DAY, 33))

    def test_new_culprit_is_one_never_seen_before_and_slow_enough(self):
        claude = {"id": 101, "name": "Claude", "degSec": 30.0}
        rows = [boot(i, 32, culprits=[claude]) for i in range(6)]
        last = boot(6, 34, culprits=[claude, {"id": 103, "name": "NewSvc", "degSec": 12.0, "kind": "service"}, {"id": 101, "name": "Tiny", "degSec": 4.9}, {"id": 101, "name": "CLAUDE", "degSec": 9.0}])
        t = bh.trend(rows + [last])
        self.assertEqual([(x["name"], x["degSec"], x["kind"]) for x in t["newCulprits"]], [("NewSvc", 12.0, "service")])
        self.assertEqual(bh.trend(rows[:3] + [last])["newCulprits"], [])   # قبل اكتمال التعلّم لا مقارنة


class Alert(unittest.TestCase):
    def test_events_carry_boot_time_and_only_fire_when_needed(self):
        got = []
        add = lambda *a: got.append(a)
        self.assertEqual(bh.alert(bh.trend([]), add), 0)
        self.assertEqual(bh.alert(bh.trend([boot(i, 32) for i in range(8)]), add), 0)
        base = [boot(i, 32) for i in range(5)]
        t = bh.trend(base + [boot(5, 44), boot(6, 46), boot(7, 60, culprits=[{"id": 101, "name": "X", "degSec": 20.0}])])
        self.assertEqual(bh.alert(t, add), 2)
        self.assertEqual([(a[0], a[1], a[2], a[3]) for a in got], [(T0 + 7 * DAY, "health", "warn", "الإقلاع أبطأ من معتاده"), (T0 + 7 * DAY, "health", "warn", "مسبّب جديد لبطء الإقلاع")])
        self.assertIn("46", got[0][4]); self.assertIn("32", got[0][4]); self.assertIn("بعد ظهور سطح المكتب", got[0][4])
        self.assertEqual(got[1][4], "X (20.0 ث)")


class Kinds(unittest.TestCase):
    def test_annotate_fixes_only_app_events_whose_name_is_a_service_or_driver(self):
        kinds = {"web management service": "service", "wmsvc": "service", "nvlddmkm": "driver"}
        cs = [{"id": 101, "name": "Web Management Service"}, {"id": 101, "name": "WMSvc.exe"}, {"id": 101, "name": "nvlddmkm.sys"}, {"id": 101, "name": "Claude"},
              {"id": 109, "name": "wmsvc"}, "x", {"id": 101}]
        bh.annotate(cs, kinds)
        self.assertEqual([c.get("kind") if isinstance(c, dict) else None for c in cs], ["service", "service", "driver", None, None, None, None])
        self.assertIsNone(bh.annotate(None, kinds))

    def test_service_kinds_reads_the_registry_without_raising(self):
        k = bh.service_kinds()
        self.assertIsInstance(k, dict)
        if os.name == "nt":
            self.assertEqual(k.get("eventlog"), "service")
            self.assertTrue(all(v in ("service", "driver") for v in k.values()))


if __name__ == "__main__":
    unittest.main(verbosity=1)
