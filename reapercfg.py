"""إعدادات الكانس التي تعدّلها الواجهة (البند 7 من المجلس 19/11).

الأصل machinecare.config.json في مجلد MachineCare داخل Documents يكتبه المستخدم. الخادم المرتفع لا يكتب فيه أبدًا: الكتابة
بصلاحية مسؤول في مجلد يكتبه المستخدم تتبع وصلة يزرعها برنامج عادي إلى أي مجلد. فيكتب المرتفع المفاتيح التي تعدّلها الواجهة
وحدها في OVERRIDE داخل ProgramData (للمستخدمين قراءة فقط)، والكانس (مهمة محدودة) يدمجها فوق إعداده في Common.ps1، والعرض
يدمجها بالترتيب نفسه. الخادم غير المرتفع (التطوير) يكتب في الأصل كما كان.
"""
import contextlib
import os

import atomic_json
import cfgjson
import secure
from paths import MCCFG

KEYS = ("bareClaudeShellMinutes", "killBareClaudeShells", "protect")
OVERRIDE = os.path.join(secure.SECURE_DIR, "config", "reaper.json")


def _read(path):
    try:
        obj = atomic_json.read(path)[1]
    except (OSError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _valid(key, val):
    if key == "bareClaudeShellMinutes":
        return isinstance(val, int) and not isinstance(val, bool) and 60 <= val <= 2880
    if key == "killBareClaudeShells":
        return isinstance(val, bool)
    return isinstance(val, list) and all(isinstance(x, str) for x in val)


def merged(base, override_path=None):
    """قسم reaper من الأصل وفوقه مفاتيح OVERRIDE الصالحة (كما يدمجها Common.ps1)."""
    out = dict(base or {})
    ovr = _read(override_path or OVERRIDE)
    out.update({k: ovr[k] for k in KEYS if k in ovr and _valid(k, ovr[k])})
    return out


@contextlib.contextmanager
def edit():
    """`with edit() as r:` قاموس reaper الفعّال للتعديل. المرتفع: يُحفظ في OVERRIDE (يُبذر من الأصل أول مرة)؛ وإلا في الأصل."""
    if not secure.is_admin():
        with cfgjson.edit_json(MCCFG) as (_bom, cfg):
            yield cfg.setdefault("reaper", {})
        return
    os.makedirs(os.path.dirname(OVERRIDE), exist_ok=True)
    if not os.path.isfile(OVERRIDE):
        base = (_read(MCCFG).get("reaper") or {})
        atomic_json.write(OVERRIDE, False, {k: base[k] for k in KEYS if k in base and _valid(k, base[k])})
    with cfgjson.edit_json(OVERRIDE) as (_bom, ovr):
        view = merged((_read(MCCFG).get("reaper") or {}), OVERRIDE)
        yield view
        ovr.clear()
        ovr.update({k: view[k] for k in KEYS if k in view and _valid(k, view[k])})
