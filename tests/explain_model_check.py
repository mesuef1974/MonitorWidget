"""اختيار النموذج المحلي: الموحّد أولًا ثم أول نموذج غير تضمين، بلا اعتماد على ترتيب القائمة.

Run: python -X utf8 -B tests/explain_model_check.py
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import explain   # noqa: E402


def listing(*ids):
    return {"data": [{"id": i} for i in ids]}


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
