"""لوحة شرح الأخطاء (explain.errors) تعرض ما ظهر خلال آخر 3 أيام فقط: خطأ لم يتكرر منذ أكثر من ذلك يُخفى
ويُذكر عدده، ويبقى في monitor.log. معزول: مجلد مؤقت، ولا يُستدعى النموذج (التزام مرتفع يحجبه).

Run: python -X utf8 -B tests/explain_errors_fresh_check.py
"""
import atexit
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import explain

NOW = datetime(2026, 11, 18, 23, 0)
HIGH = {"commitPct": 99}  # يحجب تشغيل النموذج


def _entry(stamp, exc, func):
    return (f"{stamp}:00,000 swallowed exception:\nTraceback (most recent call last):\n"
            f'  File "C:\\x\\mod.py", line 1, in {func}\n    boom()\n{exc}\n')


class FreshErrors(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mw_expl_")
        atexit.register(shutil.rmtree, self.dir, True)
        open(os.path.join(self.dir, "mod.py"), "w").close()

    def _log(self, *entries):
        with open(os.path.join(self.dir, "monitor.log"), "w", encoding="utf-8") as fh:
            fh.write("".join(entries))

    def test_old_only_hidden(self):
        self._log(_entry("2026-11-14 07:39", "RuntimeError: old", "a"), _entry("2026-11-05 20:28", "OSError: older", "b"))
        t = explain.errors(HIGH, self.dir, now=NOW)["text"]
        self.assertIn("لا أخطاء خلال آخر 3 أيام", t)
        self.assertIn("أُخفي 2 نوعًا", t)
        self.assertNotIn("RuntimeError", t)

    def test_recent_shown_old_hidden(self):
        self._log(_entry("2026-11-10 10:00", "OSError: old", "b"), _entry("2026-11-18 20:30", "RuntimeError: new", "a"))
        t = explain.errors(HIGH, self.dir, now=NOW)["text"]
        self.assertIn("RuntimeError — 1 مرة", t)
        self.assertIn("قبل 2 ساعة", t)
        self.assertNotIn("OSError", t)
        self.assertIn("أُخفي 1 نوعًا", t)

    def test_recurrence_keeps_type(self):
        # آخر ظهور حديث يُبقي النوع ظاهرًا بعدده الكامل حتى لو بدأ قديمًا
        self._log(_entry("2026-11-01 10:00", "OSError: x", "b"), _entry("2026-11-18 22:30", "OSError: x", "b"))
        t = explain.errors(HIGH, self.dir, now=NOW)["text"]
        self.assertIn("OSError — 2 مرة", t)
        self.assertNotIn("أُخفي", t)

    def test_boundary_72h(self):
        self._log(_entry("2026-11-15 23:00", "OSError: edge", "b"))
        self.assertIn("أُخفي 1 نوعًا", explain.errors(HIGH, self.dir, now=NOW)["text"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
