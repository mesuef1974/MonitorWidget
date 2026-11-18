"""Coverage and freshness tests; pure inputs, no live database or probes."""
import copy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from health_score import build_score


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.now = 1792920000
        self.args = dict(metrics={"commitPct": 60, "availGB": 5, "diskFreeGB": 120},
                         tasks=[{"enabled": True, "result": 0, "group": "backup", "last": datetime.fromtimestamp(self.now, timezone.utc).isoformat()}],
                         probe_ts=self.now, hardware={"disks": [{"health": "Healthy"}],
                                                    "defender": {"realtime": True, "sigAgeDays": 1}},
                         updates_ts=self.now, updates={"n": 0}, gpu={"ts": self.now, "temp": 65}, now=self.now)

    def run_score(self, **changes):
        args = copy.deepcopy(self.args); args.update(changes)
        return build_score(**args)

    def test_complete(self):
        r = self.run_score()
        self.assertEqual((r["score"], r["coveragePct"], r["status"]), (100, 100, "complete"))

    def test_unknowns_not_healthy(self):
        r = self.run_score(hardware={}, updates={}, gpu={})
        self.assertEqual(r["coveragePct"], 75)
        self.assertEqual(r["status"], "partial")
        for p in r["parts"]:
            if p["key"] in ("hw", "sec", "therm"):
                self.assertIsNone(p["pts"]); self.assertEqual(p["status"], "missing")

    def test_stale_sources_excluded(self):
        r = self.run_score(probe_ts=self.now - 8 * 3600 - 1, gpu={"ts": self.now - 181, "temp": 65})
        self.assertEqual(r["coveragePct"], 75)
        self.assertTrue(all(p["status"] == "stale" for p in r["parts"] if p["key"] in ("hw", "sec", "therm")))

    def test_cleanup_lock_skip_is_not_failure(self):
        last = datetime.fromtimestamp(self.now, timezone.utc).isoformat()
        clean = {"enabled": True, "result": 3, "group": "clean", "label": "تنظيف ربع سنوي", "last": last}
        r = self.run_score(tasks=self.args["tasks"] + [clean])
        tasks = next(p for p in r["parts"] if p["key"] == "tasks")
        self.assertEqual((tasks["pts"], tasks["why"]), (15, "كلها سليمة"))

    def test_future_last_run_failure_not_counted(self):
        # نتيجة سُجّلت بساعة قافزة إلى المستقبل (14/11/2026) لا تُخصم، وتُذكر في السبب؛ وفشل حقيقي حديث يبقى محسوبًا
        future = datetime.fromtimestamp(self.now + 40 * 86400, timezone.utc).isoformat()
        jump = {"enabled": True, "result": 1, "group": "clean", "label": "تنظيف شهري", "last": future}
        r = self.run_score(tasks=self.args["tasks"] + [jump])
        tasks = next(p for p in r["parts"] if p["key"] == "tasks")
        self.assertEqual(tasks["pts"], 15)
        self.assertIn("كلها سليمة", tasks["why"]); self.assertIn("تنظيف شهري", tasks["why"])
        last = datetime.fromtimestamp(self.now - 3600, timezone.utc).isoformat()
        real = {"enabled": True, "result": 1, "group": "clean", "label": "تنظيف يومي", "last": last}
        r = self.run_score(tasks=self.args["tasks"] + [jump, real])
        tasks = next(p for p in r["parts"] if p["key"] == "tasks")
        self.assertEqual(tasks["pts"], 10)
        self.assertTrue(tasks["why"].startswith("فشلت: تنظيف يومي"))
        # ضمن هامش الساعة (أقل من 10 دقائق) يبقى فشلًا عاديًا
        near = dict(jump, last=datetime.fromtimestamp(self.now + 300, timezone.utc).isoformat())
        tasks = next(p for p in self.run_score(tasks=self.args["tasks"] + [near])["parts"] if p["key"] == "tasks")
        self.assertEqual(tasks["pts"], 10)

    def test_exit_3_outside_cleanup_still_fails(self):
        last = datetime.fromtimestamp(self.now, timezone.utc).isoformat()
        other = {"enabled": True, "result": 3, "group": "backup", "label": "نسخ", "last": last}
        r = self.run_score(tasks=[other])
        tasks = next(p for p in r["parts"] if p["key"] == "tasks")
        self.assertLess(tasks["pts"], 15)

    def test_no_measurements(self):
        r = self.run_score(metrics={}, tasks=[], hardware={}, updates={}, gpu={})
        self.assertIsNone(r["score"])
        self.assertEqual((r["status"], r["coveragePct"]), ("insufficient", 0))

    def test_invalid_numeric_values(self):
        r = self.run_score(metrics={"commitPct": float("nan"), "availGB": float("inf"), "diskFreeGB": True})
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["coveragePct"], 50)  # other fully observed sources retain their weights
        self.assertTrue(all(p["pts"] is None for p in r["parts"] if p["key"] in ("mem", "disk")))

    def test_security_needs_updates(self):
        r = self.run_score(updates={})
        self.assertIsNone(next(p for p in r["parts"] if p["key"] == "sec")["pts"])

    def test_old_backup_reduces_score(self):
        tasks = copy.deepcopy(self.args["tasks"]); tasks[0]["last"] = "2020-01-01T00:00:00+00:00"
        self.assertEqual(self.run_score(tasks=tasks)["score"], 90)

    def test_future_probe_not_fresh(self):
        r = self.run_score(probe_ts=self.now + 3600)
        self.assertEqual(next(p for p in r["parts"] if p["key"] == "hw")["status"], "stale")


if __name__ == "__main__":
    unittest.main()
