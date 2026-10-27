# -*- coding: utf-8 -*-
"""الإعدادات (البند 6/د من الخارطة): المظهر المشترك للتطبيق والويدجت، الوضع (theme)، عتبات التنبيهات، خطة الطاقة،
وإعدادات حاصد الأصداف (reaper) في machinecare.config.json. العقود كما كانت في server.py حرفيًا.

    ROUTES / post(route, body, invalidate)   المسارات POST؛ post يعيد (رمز الحالة، الردّ) أو None إن لم يكن المسار من هنا
    APPEARANCE                               مخزن المظهر (appearance.AppearanceStore) الذي تقرأه mini_payload وGET /api/appearance
    get_theme() / set_theme(mode)            الوضع الحالي وتبديله (يرفع appearance.AppearanceUnavailable عند تعذّر الاسترداد)

invalidate تُستدعى بعد نجاح /api/reaper فقط (كان يسقط إلى _cache["t"] = 0 في do_POST)؛ بقية المسارات تعيد ردّها مباشرة.
"""
import re

import appearance
import cfgjson
import insight
from paths import MCCFG, ROOT

ROUTES = ("/api/appearance", "/api/theme", "/api/alerts", "/api/power-plan", "/api/reaper-set", "/api/reaper-protect", "/api/reaper")
APPEARANCE = appearance.AppearanceStore(ROOT)
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\- ]{1,64}$")


def get_theme():
    return APPEARANCE.get()["mode"]


def set_theme(t):
    return APPEARANCE.set_mode(t)


def _appearance(body):
    try:
        result = APPEARANCE.update(body)
    except appearance.AppearanceConflict as exc:
        return 409, {"error": "appearance changed", "appearance": exc.current}
    except appearance.AppearanceUnavailable:
        return 409, {"error": "appearance recovery required"}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    return 200, {"ok": True, **result}


def _theme(body):
    t = body.get("theme")
    if t not in ("auto", "light", "dark"):
        return 400, {"error": "bad theme"}
    try:
        result = set_theme(t)
    except appearance.AppearanceUnavailable:
        return 409, {"error": "appearance recovery required"}
    return 200, {"ok": True, "theme": result["mode"], "appearance": result}


def _alerts(body):
    cfg, err = insight.alerts_set(body)
    if err:
        return 400, {"error": err}
    return 200, {"cfg": cfg}


def _power_plan(body):
    plans, err = insight.power_set(body.get("guid"))
    if err:
        return 400, {"error": err}
    return 200, {"plans": plans}


def _reaper_set(body):
    key, val = body.get("key"), body.get("value")
    if key != "bareClaudeShellMinutes" or isinstance(val, bool) or not isinstance(val, int) or not 60 <= val <= 2880:
        return 400, {"error": "not allowed"}
    with cfgjson.edit_json(MCCFG) as (bom, cfg):
        cfg.setdefault("reaper", {})[key] = val
    return 200, {"bareMin": val}


def _reaper_protect(body):
    nm, op = body.get("name"), body.get("op")
    if op not in ("add", "remove") or not isinstance(nm, str) or not _NAME_RE.match(nm.strip()):
        return 400, {"error": "bad name"}
    nm = nm.strip().replace(".", "\\.")  # entries are regex fragments joined with | by the reaper
    with cfgjson.edit_json(MCCFG) as (bom, cfg):
        lst = cfg.setdefault("reaper", {}).setdefault("protect", [])
        if op == "add" and nm.lower() not in [x.lower() for x in lst]:
            lst.append(nm)
        elif op == "remove":
            lst[:] = [x for x in lst if x.lower() != nm.lower()]
    return 200, {"protect": lst}


def _reaper(body):
    key, val = body.get("key"), body.get("value")
    if key != "killBareClaudeShells" or not isinstance(val, bool):
        return 400, {"error": "not allowed"}
    with cfgjson.edit_json(MCCFG) as (bom, cfg):
        cfg["reaper"][key] = val
    return None


_DIRECT = {"/api/appearance": _appearance, "/api/theme": _theme, "/api/alerts": _alerts, "/api/power-plan": _power_plan,
           "/api/reaper-set": _reaper_set, "/api/reaper-protect": _reaper_protect}


def post(route, body, invalidate=lambda: None):
    """(رمز الحالة، الردّ) لمسار من ROUTES، أو None إن لم يكن المسار من هذه الوحدة."""
    handler = _DIRECT.get(route)
    if handler is not None:
        return handler(body)
    if route == "/api/reaper":
        reply = _reaper(body)
        if reply is not None:
            return reply
        invalidate()
        return 200, {"ok": True}
    return None
