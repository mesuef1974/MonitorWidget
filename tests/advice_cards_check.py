"""بطاقات التوصيات (insight.advice_payload): بطاقة Claude واحدة تضمّ العدد إلى الحجم، ونسخ التنظيف القديمة الصغيرة
(أقل من OLDBAK_MIN_GB بحجم مقاس كامل) لا تُعرض، وعنوان النسخة الخارجية بلا «لـ D:» (حالة 18/11/2026).
معزول: قاعدة ومجلد مؤقتان، والمسابير الحية مستبدلة.

Run: python -X utf8 -B tests/advice_cards_check.py
"""
import atexit
import os
import re
import shutil
import sys
import tempfile
import time
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

    def test_stale_reboot_and_updates_are_hidden(self):
        # البطاقتان تُبنيان على المسبار وخيط التحديثات؛ القياس الأقدم من حدّ درجة الصحة لا يُعرض
        now = time.time()
        old = now - insight.health_score.UPDATES_MAX_AGE - 3600
        hw = {"probe": {"pendingReboot": True}, "updates": {"n": 3}}
        try:
            insight.health.get_hw = lambda k: (now - 60, hw[k])
            it = _ids()
            self.assertIn("reboot", it)
            self.assertIn("updates", it)
            insight.health.get_hw = lambda k: (old, hw[k])
            it = _ids()
            self.assertNotIn("reboot", it)
            self.assertNotIn("updates", it)
        finally:
            insight.health.get_hw = lambda k: (None, None)


class AlertsLog(unittest.TestCase):
    """انحدار: سجل التنبيهات كان يرشّح cpu/health/guard/fix فقط فلا تظهر تنبيهات الالتزام والقرص والإقلاع والذكاء المحلي والشبكة."""

    def test_all_alert_sources_appear(self):
        now = int(time.time())
        srcs = ("cpu", "commit", "disk", "boot", "localai", "net")
        for i, src in enumerate(srcs):
            db.add_event(now - i, src, "warn" if src != "boot" else "error", "تنبيه " + src)
        db.add_event(now - 100, "boot", "info", "إقلاع الجهاز")   # المعلومة ليست تنبيهًا
        log = insight.alerts_payload()["log"]
        self.assertEqual({x["source"] for x in log}, set(srcs))

    def test_frontend_labels_every_alert_source(self):
        src = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
        labels = re.search(r"const SRC = \{([^}]*)\}", src).group(1)
        for name in insight.ALERT_SOURCES:
            self.assertIn(name + ": ", labels)


if __name__ == "__main__":
    unittest.main(verbosity=2)
