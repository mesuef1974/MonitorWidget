# -*- coding: utf-8 -*-
"""سياسة التنظيف والمهام (البند 6/ج من الخارطة): قواعد cleanup-policy.json ومستوياتها ووضع الصيانة المؤقت، المهام المجدولة
بقائمة tasks.json البيضاء، عناصر بدء التشغيل، ومهام الوكيل. العقود كما كانت في server.py حرفيًا.

    ROUTES / post(route, body, invalidate)   المسارات POST؛ post يعيد (رمز الحالة، الردّ) أو None إن لم يكن المسار من هنا
    maintenance_state() / maintenance_set(on)  وضع الصيانة: يطفئ قواعد Docker/Claude مؤقتًا ويعيدها كما كانت
    whitelist()                              المهام المسموح تبديلها أو تشغيلها (tasks.json؛ تُقرأ عند أول حاجة)
    task_op(op, path, name)                  تنفيذ enable/disable/run عبر ops.ps1

invalidate تُستدعى بعد كل تعديل ناجح في /api/task و/api/rule و/api/tier-pause حتى يعيد الخادم جمع حالة المهام والمستويات
الموقوفة (كانت _cache["t"] = 0 و_extra["paused"] = ... في do_POST).
"""
import json
import os
import subprocess
import time

import cfgjson
import db
import errlog
import sched
import startup_mgr
from paths import CNW, POLICY, PWSH, ROOT, TIERS

ROUTES = ("/api/rule", "/api/tier-pause", "/api/maintenance", "/api/task", "/api/startup", "/api/jobs/run")
MAINT_RULES = ("docker-dangling", "claude-file-history")   # docker-build-cache حُذفت من السياسة (23/10/2026) فلا تُعرض
_whitelist = None


def whitelist():
    """{(path, name): entry} من tasks.json؛ تُقرأ مرة عند أول حاجة (لا عند الاستيراد حتى تبقى الاختبارات بلا ملفات)."""
    global _whitelist
    if _whitelist is None:
        with open(os.path.join(ROOT, "tasks.json"), encoding="utf-8") as f:
            _whitelist = {(t["path"], t["name"]): t for t in json.load(f)}
    return _whitelist


def task_op(op, path, name):
    r = subprocess.run(
        [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(ROOT, "ops.ps1"), "-Op", op, "-Path", path, "-Name", name],
        capture_output=True, timeout=sched.remaining_timeout(40), creationflags=CNW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr.decode("utf-8", "replace") or "failed").strip()[:300])


# ---------------------------------------------------------------- وضع الصيانة
def maintenance_state():
    try:
        _b, pol = cfgjson.read_json(POLICY)
        m = pol.get("maintenance") or {}
        return {"on": bool(m.get("on")), "rules": list(MAINT_RULES), "since": m.get("since")}
    except Exception:
        errlog.swallowed()
        return {"on": False, "rules": list(MAINT_RULES), "since": None}


def maintenance_set(on):
    """Maintenance mode: switch the Docker/Claude rules off (remembering their state) and restore them exactly when it ends."""
    with cfgjson.edit_json(POLICY) as (bom, pol):
        m = pol.get("maintenance") or {}
        if on and not m.get("on"):
            saved = {}
            for r in pol["rules"]:
                if r["id"] in MAINT_RULES:
                    saved[r["id"]] = bool(r.get("enabled"))
                    r["enabled"] = False
            pol["maintenance"] = {"on": True, "since": int(time.time()), "saved": saved}
        elif (not on) and m.get("on"):
            saved = m.get("saved") or {}
            for r in pol["rules"]:
                if r["id"] in saved:
                    r["enabled"] = bool(saved[r["id"]])
            pol["maintenance"] = {"on": False, "since": None, "saved": {}}
        else:
            return maintenance_state()

    with db._lock:
        c = db.conn()
        c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)", (int(time.time()), "maintenance", "mode", 1, "on=" + str(bool(on)).lower()))
        c.commit()
    return maintenance_state()


# ---------------------------------------------------------------- المعالجات التي تعيد جمع الحالة بعد النجاح (None = نجاح)
def _task(body):
    op, path, name = body.get("op"), body.get("path"), body.get("name")
    entry = whitelist().get((path, name))
    if not entry or op not in ("enable", "disable", "run"):
        return 400, {"error": "not allowed"}
    if entry.get("needsAdmin"):
        return 400, {"error": "هذه المهمة تحتاج صلاحية مدير لتغييرها"}
    if op == "run" and not entry.get("canRun"):
        return 400, {"error": "run not allowed for this task"}
    task_op(op, path, name)
    return None


def _rule(body):
    rid, en = body.get("id"), body.get("enabled")
    if not isinstance(en, bool):
        return 400, {"error": "enabled must be boolean"}
    with cfgjson.edit_json(POLICY) as (bom, pol):
        hit = [r for r in pol["rules"] if r["id"] == rid]
        if not hit:
            return 400, {"error": "unknown rule"}
        hit[0]["enabled"] = en
    with db._lock:  # every rule toggle goes to the action log
        c = db.conn()
        c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)", (int(time.time()), "rule", str(rid), 1, "enabled=" + str(en).lower()))
        c.commit()
    return None


def _tier_pause(body):
    tier, paused = body.get("tier"), body.get("paused")
    if tier not in TIERS or not isinstance(paused, bool):
        return 400, {"error": "bad request"}
    with cfgjson.edit_json(POLICY) as (bom, pol):
        cur = [t for t in pol.get("pausedTiers", []) if t in TIERS and t != tier]
        if paused:
            cur.append(tier)
        pol["pausedTiers"] = cur
    return None


_MUTATING = {"/api/task": _task, "/api/rule": _rule, "/api/tier-pause": _tier_pause}


def post(route, body, invalidate=lambda: None):
    """(رمز الحالة، الردّ) لمسار من ROUTES، أو None إن لم يكن المسار من هذه الوحدة."""
    if route == "/api/jobs/run":
        name = str(body.get("name", ""))
        row = sched.next_run(name)
        if not row:
            return 404, {"error": "unknown job"}
        if sched.running_now(row):   # الطلب أثناء التشغيل كان يضيع بصمت؛ الردّ يقول ذلك صراحة
            return 409, {"error": "job running", "running": True}
        sched.set_next(name, time.time())
        return 200, {"ok": True}
    if route == "/api/maintenance":
        on = body.get("on")
        if not isinstance(on, bool):
            return 400, {"error": "on must be boolean"}
        return 200, maintenance_set(on)
    if route == "/api/startup":
        return startup_mgr.toggle(body)
    handler = _MUTATING.get(route)
    if handler is None:
        return None
    reply = handler(body)
    if reply is not None:
        return reply
    invalidate()
    return 200, {"ok": True}
