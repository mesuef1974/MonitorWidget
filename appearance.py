"""Shared appearance preferences. Importing/reading never creates app state.

All writers use the same atomic-json lock. The compatibility theme endpoint
changes only the mode; full clients use a revision to reject stale edits.
"""
import os

import atomic_json

MODES = ("auto", "light", "dark")
PALETTES = ("teal", "blue", "violet", "graphite")
SCHEMA_VERSION = 1


class AppearanceConflict(Exception):
    def __init__(self, current):
        super().__init__("appearance changed")
        self.current = current


class AppearanceUnavailable(Exception):
    pass


class AppearanceStore:
    def __init__(self, root):
        self.path = os.path.join(os.fspath(root), "appearance.json")
        self.legacy = os.path.join(os.fspath(root), "widget.settings.json")

    def _read(self):
        """Return public state and original mapping (extra keys are preserved)."""
        try:
            _, raw = atomic_json.read(self.path)
        except FileNotFoundError:
            mode = "auto"
            try:
                _, legacy = atomic_json.read(self.legacy)
                if isinstance(legacy, dict) and legacy.get("theme") in MODES:
                    mode = legacy["theme"]
            except (OSError, ValueError, TypeError):
                pass
            return {"schemaVersion": 1, "revision": 0, "mode": mode, "palette": "teal"}, {}
        except (OSError, ValueError, TypeError):
            return self._unavailable(), None
        if not isinstance(raw, dict):
            return self._unavailable(), None
        version, revision = raw.get("schemaVersion"), raw.get("revision")
        if type(version) is not int or version != 1 or type(revision) is not int or revision < 0:
            return self._unavailable(), None
        return {
            "schemaVersion": 1,
            "revision": revision,
            "mode": raw.get("mode") if raw.get("mode") in MODES else "auto",
            "palette": raw.get("palette") if raw.get("palette") in PALETTES else "teal",
        }, raw

    @staticmethod
    def _unavailable():
        return {"schemaVersion": 1, "revision": 0, "mode": "auto", "palette": "teal", "recoveryRequired": True}

    def get(self):
        return self._read()[0]

    @staticmethod
    def validate(body):
        if not isinstance(body, dict) or set(body) - {"mode", "palette", "revision", "schemaVersion"}:
            raise ValueError("bad appearance")
        if body.get("mode") not in MODES or body.get("palette") not in PALETTES:
            raise ValueError("bad appearance")
        if type(body.get("revision")) is not int or body["revision"] < 0:
            raise ValueError("bad appearance revision")
        if "schemaVersion" in body and (type(body["schemaVersion"]) is not int or body["schemaVersion"] != 1):
            raise ValueError("bad appearance schema")

    def _commit(self, current, raw, mode, palette):
        if raw is None:
            # Never overwrite corrupt/newer documents during a cosmetic change.
            raise AppearanceUnavailable("appearance recovery required")
        if raw and current["mode"] == mode and current["palette"] == palette:
            return current
        result = dict(schemaVersion=1, revision=current["revision"] + 1, mode=mode, palette=palette)
        raw.update(result)
        atomic_json.write(self.path, False, raw)
        return result

    def update(self, body):
        self.validate(body)
        with atomic_json.locked(self.path):
            current, raw = self._read()
            if raw is None:
                raise AppearanceUnavailable("appearance recovery required")
            if current["revision"] != body["revision"]:
                raise AppearanceConflict(current)
            return self._commit(current, raw, body["mode"], body["palette"])

    def set_mode(self, mode):
        """Compatibility update: preserve palette and latest revision atomically."""
        if mode not in MODES:
            raise ValueError("bad theme")
        with atomic_json.locked(self.path):
            current, raw = self._read()
            return self._commit(current, raw, mode, current["palette"])
