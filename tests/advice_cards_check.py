"""بطاقات التوصيات (insight.advice_payload): بطاقة Claude واحدة تضمّ العدد إلى الحجم، ونسخ التنظيف القديمة الصغيرة
(أقل من OLDBAK_MIN_GB بحجم مقاس كامل) لا تُعرض، وعنوان النسخة الخارجية بلا «لـ D:» (حالة 18/11/2026).
معزول: قاعدة ومجلد مؤقتان، والمسابير الحية مستبدلة.

Run: python -X utf8 -B tests/advice_cards_check.py
"""
import atexit
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="mw_adv_")
atexit.register(shutil.rmtree, TMP, True)
import db

db.DB_PATH = os.path.join(TMP, "monitor.db")
db._conn = None
import insight

insight.APPDIR = TMP
insight.reaper_info = lambda: None
insight.health.get_hw = lambda k: (None, None)
insight.gpuwatch.api_payload = lambda w: {"notes": []}
APPS = os.path.join(TMP, "cleanup_apps")


def _ids(m=None, extra=None):
    return {i["id"]: i for i in insight.advice_payload(m or {}, extra or {}, None, None)["items"]}


class AdviceCards(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(APPS, True)
        os.makedirs(APPS)

    def test_claude_single_card_with_count(self):
        it = _ids({"claude": 33}, {"claudeGB": 8.4})
        self.assertIn("claude", it)
        self.assertNotIn("claude_n", it)
        self.assertEqual(it["claude"]["title"], "عمليات Claude تستهلك 8.4 GB (33 عملية)")

    def test_claude_count_only_when_small_memory(self):
        it = _ids({"claude": 25}, {"claudeGB": 1.0})
        self.assertNotIn("claude", it)
        self.assertIn("claude_n", it)

    def test_oldbak_small_hidden_large_shown(self):
        for i in range(5):
            os.makedirs(os.path.join(APPS, f"x_backup_{i}"))
        insight._oldbak_gb = lambda p: 0.004
        self.assertNotIn("oldbak", _ids())
        insight._oldbak_gb = lambda p: 2.5
        self.assertIn("2.5 GB", _ids()["oldbak"]["text"])
        insight._oldbak_gb = lambda p: None  # لم يُحسب بعد: تُعرض بلا حجم
        self.assertIn("oldbak", _ids())

    def test_backup_title(self):
        with open(os.path.join(APPS, "backup_d_1.log"), "w", encoding="utf-8") as fh:
            fh.write("ABORT no external disk")
        self.assertEqual(_ids()["backup"]["title"], "لا نسخة احتياطية خارجية للقرص D")


if __name__ == "__main__":
    unittest.main(verbosity=2)
