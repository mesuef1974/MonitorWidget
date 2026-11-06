"""اختيار النموذج المحلي: المحمّل أولًا كي لا يُحمَّل ثانٍ، ثم المفضّل، ثم أول نموذج غير تضمين.

Run: python -X utf8 -B tests/explain_model_check.py
"""
import io
import os
import sys
import unittest
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
        with patch.object(explain, "_loaded", lambda: ["qwen/qwen3-8b", explain.PREFERRED_MODEL]):
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
        with patch.object(explain, "_req", lambda path: listing("qwen/qwen3-8b", explain.PREFERRED_MODEL, "lfm3-1.7b")):
            self.assertEqual(explain._model(), explain.PREFERRED_MODEL)

    def test_falls_back_to_first_non_embedding(self):
        with patch.object(explain, "_req", lambda path: listing("text-embedding-x", "lfm3-1.7b")):
            self.assertEqual(explain._model(), "lfm3-1.7b")

    def test_no_model(self):
        with patch.object(explain, "_req", lambda path: listing("text-embedding-x")), self.assertRaises(RuntimeError):
            explain._model()


if __name__ == "__main__":
    unittest.main(verbosity=1)
