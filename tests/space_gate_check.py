"""بوابة المخابئ في التنظيف الساعي (البند 25 من المجلس 19/11): مخبأ Edge وnpm وpip لا يُمسح تلقائيًا والمساحة الحرة في C
عند عتبة تحذير المساحة (alertcfg disk.freeWarnGB) أو فوقها؛ تحتها يُمسح؛ والأنواع الأخرى المفعّلة لا تتأثر؛ وتعذّر القياس لا يمنع.

Run: python -X utf8 -B tests/space_gate_check.py
"""
import os
import sys
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import space

GB = 2**30
AUTO = {"temp": True, "edgecache": True, "npmcache": True, "pipcache": True, "crashdumps": False, "wintemp": True}


class Gate(unittest.TestCase):
    def _run(self, free_gb, threshold=40, usage_error=False):
        cleaned = []
        cfg = types.SimpleNamespace(get=lambda: {"disk": {"freeWarnGB": threshold, "freeBadGB": 20}})

        def usage(_):
            if usage_error:
                raise OSError("no volume")
            return types.SimpleNamespace(free=free_gb * GB)
        fake_extras = types.SimpleNamespace(dism_monthly=lambda: None)
        with patch.dict(sys.modules, {"alertcfg": cfg, "extras": fake_extras}), \
             patch.object(space.shutil, "disk_usage", usage), \
             patch.object(space, "get_auto", lambda: dict(AUTO)), \
             patch.object(space, "clean", lambda k, dry=True, src="manual", **kw: cleaned.append((k, dry, src))):
            gate = space.auto_gate()
            space.auto_run()
        return gate, [k for k, _, _ in cleaned]

    def test_plenty_of_space_skips_caches_only(self):
        gate, done = self._run(216)
        self.assertEqual((gate["gated"], gate["freeGB"], gate["thresholdGB"]), (True, 216.0, 40))
        self.assertEqual(done, ["temp", "wintemp"])

    def test_below_threshold_cleans_caches_too(self):
        gate, done = self._run(39.5)
        self.assertFalse(gate["gated"])
        self.assertEqual(done, ["temp", "edgecache", "npmcache", "pipcache", "wintemp"])

    def test_threshold_follows_alert_setting(self):
        self.assertFalse(self._run(80, threshold=100)[0]["gated"])
        self.assertTrue(self._run(100, threshold=100)[0]["gated"])     # عند العتبة نفسها: مكتفٍ

    def test_unmeasurable_disk_does_not_block(self):
        gate, done = self._run(0, usage_error=True)
        self.assertEqual((gate["gated"], gate["freeGB"]), (False, None))
        self.assertIn("edgecache", done)

    def test_cache_kinds_are_known_auto_kinds(self):
        self.assertTrue(set(space.CACHE_KINDS) <= set(space.SAFE + space.AUTO_EXTRA))


if __name__ == "__main__":
    unittest.main(verbosity=1)
