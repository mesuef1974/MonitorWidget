"""لوحة الذكاء المحلي: قراءة النماذج والسرعة، والحكم، وحدث اجتماع نموذجين، واستخدام الشرح.

Run: python -X utf8 -B tests/localai_check.py
"""
import io
import json
import os
import sqlite3
import sys
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import localai   # noqa: E402

A = {"id": "a", "context": 8192}
B = {"id": "b", "context": 24576}
LOG = """[2026-11-06 08:01:22][DEBUG] 0.1 I slot print_timing: id  0 | task 0 | prompt eval time =    6750.90 ms / 14020 tokens (    0.48 ms per token,  2076.76 tokens per second)
0.2 I slot print_timing: id  0 | task 0 |        eval time =    3821.73 ms /   160 tokens (   24.04 ms per token,    41.60 tokens per second)
0.3 I slot print_timing: id  0 | task 0 |       total time =   10572.64 ms / 14180 tokens
[2026-11-06 08:05:55][DEBUG] 1.1 I slot print_timing: id  0 | task 1 | prompt eval time =    13.00 ms / 13 tokens (    1.00 ms per token,  1003.44 tokens per second)
1.2 I slot print_timing: id  0 | task 1 |        eval time =    50.00 ms /     5 tokens (   10.00 ms per token,   100.00 tokens per second)
"""


class Models(unittest.TestCase):
    def test_only_loaded_language_models(self):
        body = json.dumps({"data": [
            {"id": "x", "type": "llm", "state": "not-loaded"},
            {"id": "e", "type": "embeddings", "state": "loaded"},
            {"id": "v", "type": "vlm", "state": "loaded", "loaded_context_length": 24576, "max_context_length": 262144, "quantization": "Q4_K_M"},
        ]}).encode()
        with patch.object(localai.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body)):
            self.assertEqual(localai.models(), [{"id": "v", "context": 24576, "maxContext": 262144, "quant": "Q4_K_M"}])

    def test_server_off_is_none(self):
        def off(*a, **k):
            raise OSError("refused")
        with patch.object(localai.urllib.request, "urlopen", off):
            self.assertIsNone(localai.models())


class Speed(unittest.TestCase):
    def test_reads_generation_not_prompt_and_skips_short_replies(self):
        s = localai.parse_speed(LOG)
        self.assertEqual((s["tps"], s["tokens"]), (41.60, 160))
        self.assertEqual(s["ts"], int(time.mktime((2026, 11, 6, 8, 1, 22, 0, 0, -1))))

    def test_no_timing(self):
        self.assertIsNone(localai.parse_speed("[2026-11-06 08:01:22][INFO] nothing"))


class Verdict(unittest.TestCase):
    def test_states(self):
        fast, slow = {"tps": 42, "ts": 1000}, {"tps": 2, "ts": 1000}
        self.assertEqual(localai.verdict(None, None, 1000), "off")
        self.assertEqual(localai.verdict([], fast, 1000), "idle")
        self.assertEqual(localai.verdict([A, B], fast, 1000), "crowded")
        self.assertEqual(localai.verdict([A], slow, 1000), "slow")
        self.assertEqual(localai.verdict([A], fast, 1000), "ok")
        self.assertEqual(localai.verdict([A], None, 1000), "ok")

    def test_old_slowness_does_not_count(self):
        self.assertEqual(localai.verdict([A], {"tps": 2, "ts": 0}, localai.RECENT_SECONDS + 1), "ok")


class Event(unittest.TestCase):
    def setUp(self):
        self.events = []
        localai.STATE.update(ready=False, verdict=None, models=[], speed=None, scannedAt=None)
        for p in (patch.object(localai, "_log_tail", lambda: ""),
                  patch.object(localai.db, "add_event", lambda *a: self.events.append(a))):
            p.start()
            self.addCleanup(p.stop)

    def run_with(self, loaded):
        with patch.object(localai, "models", lambda: loaded):
            return localai.refresh(1000)

    def test_one_event_per_entry_into_crowding(self):
        self.run_with([A])
        self.assertEqual(self.events, [])
        self.run_with([A, B])
        self.run_with([A, B])
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][1:3], ("localai", "warn"))
        self.assertEqual(self.events[0][4], "a + b")
        self.run_with([A])
        self.run_with([A, B])
        self.assertEqual(len(self.events), 2)

    def test_refresh_fills_state(self):
        s = self.run_with(None)
        self.assertEqual((s["ready"], s["verdict"], s["models"], s["scannedAt"]), (True, "off", [], 1000))


class Usage(unittest.TestCase):
    def test_counts_window_and_tolerates_bad_detail(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("CREATE TABLE actions(ts INT,kind TEXT,ok INT,detail TEXT)")
        rows = [(1000000, "explain", 1, json.dumps({"ms": 8000, "fallback": 1, "total": 3})),
                (1000000, "explain", 0, json.dumps({"ms": 4000})),
                (1000000, "explain", 1, "not json"),
                (1000000, "gpupurge", 1, "{}"),
                (1, "explain", 1, "{}")]
        c.executemany("INSERT INTO actions VALUES(?,?,?,?)", rows)

        @contextmanager
        def ro():
            yield c
        with patch.object(localai.db, "ro", ro), patch.object(localai.errlog, "swallowed", lambda: None):
            u = localai.usage(2000000)
        self.assertEqual(u, {"days": 14, "runs": 3, "failed": 1, "avgMs": 6000, "fallback": 1, "items": 3})


if __name__ == "__main__":
    unittest.main(verbosity=1)
