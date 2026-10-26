"""Isolated shared-appearance/CAS and HTTP contracts; no live server imports."""
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from urllib.parse import urlparse, parse_qs
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import appearance
import atomic_json
from backend_regression import extracted


class AppearancePreferences(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = appearance.AppearanceStore(self.root)

    def test_read_defaults_has_no_filesystem_side_effects(self):
        self.assertEqual(self.store.get(), dict(schemaVersion=1, revision=0, mode="auto", palette="teal"))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_legacy_import_is_read_only_and_migration_keeps_window_settings(self):
        legacy = self.root / "widget.settings.json"
        original = json.dumps(dict(theme="light", left=17, expanded=True))
        legacy.write_text(original, encoding="utf-8")
        self.assertEqual(self.store.get()["mode"], "light")
        self.assertFalse(Path(self.store.path).exists())
        result = self.store.update(dict(mode="light", palette="graphite", revision=0))
        self.assertEqual(result["revision"], 1)
        self.assertEqual(legacy.read_text(encoding="utf-8"), original)
        self.assertEqual(appearance.AppearanceStore(self.root).get(), result)

    def test_stale_change_rejected_without_overwriting_newer_selection(self):
        first = self.store.update(dict(mode="dark", palette="violet", revision=0))
        with self.assertRaises(appearance.AppearanceConflict) as cm:
            self.store.update(dict(mode="light", palette="blue", revision=0))
        self.assertEqual(cm.exception.current, first)
        self.assertEqual(self.store.get(), first)

    def test_invalid_fields_are_rejected_before_lock_or_write(self):
        for values in ({}, dict(mode="dark", palette="blue", revision=True),
                       dict(mode="dark", palette="blue", revision=0.0),
                       dict(mode="dark", palette="unknown", revision=0),
                       dict(mode="system", palette="blue", revision=0),
                       dict(mode="dark", palette="blue", revision=0, schemaVersion=True),
                       dict(mode="dark", palette="blue", revision=0, extra=1)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.store.update(values)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_parallel_edit_has_exactly_one_winner(self):
        barrier = threading.Barrier(2)
        successes, conflicts = [], []
        def change(palette):
            barrier.wait()
            try:
                successes.append(self.store.update(dict(mode="dark", palette=palette, revision=0)))
            except appearance.AppearanceConflict as exc:
                conflicts.append(exc.current)
        workers = [threading.Thread(target=change, args=(p,)) for p in ("blue", "violet")]
        for worker in workers: worker.start()
        for worker in workers: worker.join(5)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(len(successes), 1)
        self.assertEqual(conflicts, successes)
        self.assertEqual(self.store.get(), successes[0])

    def test_compatibility_theme_change_preserves_palette_and_extra_fields(self):
        state = self.store.update(dict(mode="light", palette="graphite", revision=0))
        raw = dict(state, futurePreference={"keep": True})
        atomic_json.write(self.store.path, False, raw)
        changed = self.store.set_mode("dark")
        self.assertEqual(changed["palette"], "graphite")
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(atomic_json.read(self.store.path)[1]["futurePreference"], {"keep": True})

    def test_invalid_or_future_store_not_silently_overwritten(self):
        for raw in ("{", "[]", '{"schemaVersion":2,"revision":5}', '{"schemaVersion":1,"revision":true}'):
            with self.subTest(raw=raw):
                Path(self.store.path).write_text(raw, encoding="utf-8")
                self.assertTrue(self.store.get()["recoveryRequired"])
                with self.assertRaises(appearance.AppearanceUnavailable):
                    self.store.update(dict(mode="dark", palette="blue", revision=0))
                self.assertEqual(Path(self.store.path).read_text(encoding="utf-8"), raw)

    def test_unknown_values_fall_back_without_deleting_other_keys(self):
        raw = dict(schemaVersion=1, revision=4, mode="unknown", palette="unknown", other=7)
        atomic_json.write(self.store.path, False, raw)
        current = self.store.get()
        self.assertEqual((current["mode"], current["palette"]), ("auto", "teal"))
        self.store.update(dict(mode="light", palette="blue", revision=4))
        self.assertEqual(atomic_json.read(self.store.path)[1]["other"], 7)

    def test_failed_atomic_write_does_not_claim_save(self):
        original = self.store.update(dict(mode="light", palette="teal", revision=0))
        with patch.object(atomic_json, "write", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                self.store.update(dict(mode="dark", palette="violet", revision=1))
        self.assertEqual(self.store.get(), original)

    def test_noop_does_not_increment_revision(self):
        original = self.store.update(dict(mode="auto", palette="teal", revision=0))
        self.assertEqual(self.store.update(dict(mode="auto", palette="teal", revision=1)), original)

    def test_http_auth_validation_conflict_and_recovery(self):
        ns = extracted()
        ns.update(appearance=appearance, APPEARANCE=self.store, _ASSETS={}, urlparse=urlparse, parse_qs=parse_qs)
        sends = []
        def req(method, body=None, authenticated=True):
            wire = json.dumps(body or {}).encode()
            request = types.SimpleNamespace(path="/api/appearance", _guard=lambda:True,
                _authed=lambda:authenticated, _send=lambda *a:sends.append(a),
                headers={"Content-Type":"application/json","Content-Length":str(len(wire))}, rfile=io.BytesIO(wire))
            ns["Handler"].__dict__["do_"+method](request)
        req("GET", authenticated=False)
        req("POST", dict(mode="dark", palette="blue", revision=0), authenticated=False)
        self.assertEqual(sends, [])
        req("GET")
        self.assertEqual(sends[-1][0], 200)
        req("POST", dict(mode="light", palette="graphite", revision=0))
        self.assertEqual(sends[-1][0], 200)
        self.assertEqual(sends[-1][1]["palette"], "graphite")
        req("POST", dict(mode="dark", palette="blue", revision=0))
        self.assertEqual(sends[-1][0], 409)
        self.assertEqual(sends[-1][1]["appearance"]["mode"], "light")
        req("POST", dict(mode="dark", palette="blue", revision=True))
        self.assertEqual(sends[-1][0], 400)
        Path(self.store.path).write_text("{", encoding="utf-8")
        req("POST", dict(mode="dark", palette="blue", revision=1))
        self.assertEqual(sends[-1], (409, {"error": "appearance recovery required"}))

    def test_widget_show_waits_for_visible_loaded_window_after_mutex(self):
        ns = extracted()
        sends, checks = [], []
        visibility = iter((False, False, True, True))
        ns.update(widget_running=lambda:True,
                  widget_visible=lambda:checks.append("visible") or next(visibility),
                  widget_stop=lambda:None, widget_fit_on_screen=lambda:None,
                  widget_start=lambda:None,
                  infra=types.SimpleNamespace(log=types.SimpleNamespace(info=lambda *a:None)),
                  time=types.SimpleNamespace(sleep=lambda n:None))
        wire = b'{"show":true}'
        request = types.SimpleNamespace(path="/api/widget", _guard=lambda:True,
            _authed=lambda:True, _send=lambda *a:sends.append(a),
            headers={"Content-Type":"application/json","Content-Length":str(len(wire))},
            rfile=io.BytesIO(wire))
        ns["Handler"].do_POST(request)
        self.assertEqual(sends, [(200, {"running": True})])
        self.assertEqual(len(checks), 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
