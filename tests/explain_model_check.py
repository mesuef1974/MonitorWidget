"""اختيار النموذج المحلي: المحمّل أولًا كي لا يُحمَّل ثانٍ، ثم المفضّل، ثم أول نموذج غير تضمين.

Run: python -X utf8 -B tests/explain_model_check.py
"""
import io
import os
import sys
import unittest
import unittest.mock
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import explain   # noqa: E402


def listing(*ids):
    return {"data": [{"id": i} for i in ids]}


class Loaded(unittest.TestCase):
    def test_follows_the_loaded_model(self):
        with patch.object(explain, "_loaded", lambda: ["qwen/qwen3-8b"]), patch.object(explain, "_req", lambda path: self.fail("listing not needed")):
            self.assertEqual(explain._model(), "qwen/qwen3-8b")

    def test_preferred_wins_when_both_are_loaded(self):
        with patch.object(explain, "_loaded", lambda: ["qwen3-vl-4b-instruct", explain.PREFERRED_MODEL]):
            self.assertEqual(explain._model(), explain.PREFERRED_MODEL)

    def test_loaded_reads_state_and_skips_embeddings(self):
        body = b'{"data":[{"id":"a","type":"llm","state":"not-loaded"},{"id":"e","type":"embeddings","state":"loaded"},{"id":"b","type":"vlm","state":"loaded"}]}'
        with patch.object(explain.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body)):
            self.assertEqual(explain._loaded(), ["b"])

    def test_loaded_is_empty_when_server_is_off(self):
        def off(*a, **k):
            raise OSError("refused")
        with patch.object(explain.urllib.request, "urlopen", off):
            self.assertEqual(explain._loaded(), [])


@patch.object(explain, "_loaded", lambda: [])
class Model(unittest.TestCase):
    def test_prefers_unified_model_wherever_it_is(self):
        with patch.object(explain, "_req", lambda path: listing("qwen3-vl-4b-instruct", explain.PREFERRED_MODEL, "lfm3-1.7b")):
            self.assertEqual(explain._model(), explain.PREFERRED_MODEL)

    def test_falls_back_to_first_non_embedding(self):
        with patch.object(explain, "_req", lambda path: listing("text-embedding-x", "lfm3-1.7b")):
            self.assertEqual(explain._model(), "lfm3-1.7b")

    def test_no_model(self):
        with patch.object(explain, "_req", lambda path: listing("text-embedding-x")), self.assertRaises(RuntimeError):
            explain._model()


def part(key, pts, mx=10):
    return {"key": key, "label": "بند " + key, "pts": pts, "max": mx, "why": "حقائق " + key}


class Health(unittest.TestCase):
    """انحدار: البند غير المقاس درجته None وكانت مقارنته بالحد ترمي TypeError فيفشل «اشرح الصحة» 503."""

    def test_unmeasured_only_needs_no_model(self):
        with patch.object(explain, "_model", lambda: self.fail("no model for unmeasured parts")):
            r = explain.health([part("mem", None), part("disk", 10)], {"commitPct": None})
        self.assertTrue(r["ok"], r)
        self.assertIn("بند mem: غير مقاس", r["text"])
        self.assertNotIn("None", r["text"])

    def test_weak_and_unmeasured_together(self):
        reply = {"choices": [{"message": {"content": "disk: المساحة قليلة"}}]}
        with patch.object(explain, "_model", lambda: "m"), patch.object(explain, "_req", lambda path, body=None: reply), \
                patch("builtins.open", unittest.mock.mock_open()):
            r = explain.health([part("mem", None), part("disk", 4)], {"commitPct": 50})
        self.assertTrue(r["ok"], r)
        self.assertIn("بند disk: المساحة قليلة", r["text"])
        self.assertIn("بند mem: غير مقاس", r["text"])
        self.assertEqual(r["total"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
