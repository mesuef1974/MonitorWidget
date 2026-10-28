# -*- coding: utf-8 -*-
"""المسارات الهدّامة (البند 6/أ من الخارطة): كل POST قد يحذف ملفات أو ينهي عمليات أو يشغّل محرّك التنظيف فعليًا يمرّ من هنا،
مع تأكيد من جانب الخادم: أول طلب يعيد 428 ورمزًا أحادي الاستعمال مربوطًا بالمسار والمعاملات نفسها، ولا يُنفَّذ الفعل إلا حين
يعود الطلب نفسه بالرمز خلال CONFIRM_TTL. العقود (الرموز 200/400/409/428/503 وأجسام الردود) كما كانت في server.py حرفيًا.

    ROUTES                        المسارات التي تعالجها الوحدة
    post(route, body, invalidate) -> (رمز الحالة، الردّ) أو None إن لم يكن المسار من هنا
    confirm_required(route, body) -> ردّ 428 أو None حين استُهلك رمز صالح
    manual_status()               حالة التنظيف اليدوي الجاري (لصفحة المهام والويدجت)
    resolve_engine()              (مسار المحرّك، الخطأ): النسخة المحمية أولًا

invalidate: دالة يستدعيها الخادم بعد بدء تنظيف يدوي حتى يعيد جمع حالة المهام (كانت _cache["t"] = 0 في do_POST).
الاستثناءات تُترك للخادم: do_POST يحوّل ValueError/TypeError إلى 400 وسائرها إلى 500 مع معرّف طلب، كما كان.
"""
import hashlib
import json
import math
import os
import secrets
import subprocess
import threading
import time

import insight
import secure
import space
from paths import CNW, ENGINE, MCCFG, POLICY, PROT_ENGINE_DIR, PWSH, TIERS

ROUTES = ("/api/space", "/api/reaper-run", "/api/run-cleanup")
CONFIRM_TTL = 120     # ثوانٍ يبقى فيها رمز التأكيد صالحًا
MAX_CONFIRMS = 256    # سقف الرموز الحية؛ ما زاد يُسقط الأقدم


class ConfirmStore:
    """رموز التأكيد أحادية الاستعمال: مربوطة بالمسار والمعاملات (بلا confirm)، تنتهي بعد ttl، وبسقف للعدد.
    clock قابلة للحقن في الاختبارات."""

    def __init__(self, ttl=CONFIRM_TTL, limit=MAX_CONFIRMS, clock=time.time):
        self._items, self._lock = {}, threading.Lock()
        self.ttl, self.limit, self._clock = ttl, limit, clock

    @staticmethod
    def key(route, body):
        return route + "|" + json.dumps({k: v for k, v in body.items() if k != "confirm"}, sort_keys=True, ensure_ascii=False, allow_nan=False)

    def issue(self, key):
        t = secrets.token_urlsafe(18)
        with self._lock:
            now = self._clock()
            for k in [k for k, (_, exp) in self._items.items() if exp < now]:
                del self._items[k]
            if len(self._items) >= self.limit:
                del self._items[next(iter(self._items))]
            self._items[t] = (key, now + self.ttl)
        return t

    def take(self, key, tok):
        """يستهلك الرمز (مرة واحدة) ويعيد True فقط إن طابق المفتاح ولم ينتهِ."""
        with self._lock:
            got = self._items.pop(tok, None)
        return bool(got and got[0] == key and got[1] >= self._clock())


CONFIRMS = ConfirmStore()


def confirm_required(route, body):
    """ردّ 428 مع رمز جديد حين لا يحمل الطلب رمزًا صالحًا لهذا المسار وهذه المعاملات؛ None حين استُهلك رمز صالح."""
    key = ConfirmStore.key(route, body)
    tok = body.get("confirm")
    if isinstance(tok, str) and CONFIRMS.take(key, tok):
        return None
    return 428, {"error": "confirm_required", "confirm": CONFIRMS.issue(key), "ttl": CONFIRMS.ttl}


def _space_confirmed(route, body, act, kind):
    """تنظيف المساحة: الرمز مربوط أيضًا بالمعاينة التي رآها المستخدم (seen_mb جزء من المفتاح)، وتُعاد التجربة بعد قبول الرمز؛
    مجموعة مرشّحين كبرت فوق التسامح تُرفض. يعيد None حين يجوز التنفيذ، وإلا ردّ الخطأ."""
    seen = body.get("seen_mb")
    if isinstance(seen, bool) or not isinstance(seen, (int, float)) or not 0 <= seen <= 1e12 or not math.isfinite(seen):
        return 400, {"error": "seen_mb required"}
    r = confirm_required(route, body)
    if r:
        return r
    cur = sum(space.dry_mb(k) for k in (space.BULK if act == "cleanall" else [kind]))   # معاينة حديثة (5 دقائق) تغني عن قياس متزامن قد يطول دقائق
    if cur > seen * 1.25 + 50:
        return 409, {"error": "candidates_changed", "seen_mb": seen, "now_mb": round(cur, 1)}
    return None


# ---------------------------------------------------------------- التنظيف اليدوي (محرّك CleanupEngine.ps1)
MANUAL = {"proc": None, "tier": None, "dry": None, "started": None}
IS_ADMIN = secure.is_admin()


def manual_status():
    """حالة التنظيف اليدوي: يعمل الآن؟ وأي مستوى وهل تجربة ومتى بدأ."""
    p = MANUAL["proc"]
    return {"running": bool(p and p.poll() is None), "tier": MANUAL["tier"], "dry": MANUAL["dry"], "started": MANUAL["started"]}


def resolve_engine():
    """(path, error). النسخة المحمية في ProgramData مع تحقق SHA-256؛ نسخة المستخدم قابلة للكتابة فلا تُشغَّل بصلاحية عالية."""
    prot = os.path.join(PROT_ENGINE_DIR, "CleanupEngine.ps1")
    shaf = os.path.join(PROT_ENGINE_DIR, "engine.sha256")
    if os.path.isfile(prot) and os.path.isfile(shaf):
        try:
            want = open(shaf, encoding="ascii").read().split()[0].lower()
            got = hashlib.sha256(open(prot, "rb").read()).hexdigest()
        except Exception:
            return None, "تعذّر التحقق من نسخة المحرّك المحمية"
        if want != got:
            return None, "بصمة المحرّك المحمي لا تطابق engine.sha256؛ أعد تشغيل Install-ProtectedEngine.ps1 كمسؤول"
        return prot, None
    if IS_ADMIN or os.path.isdir(PROT_ENGINE_DIR):
        return None, "الخادم يعمل بصلاحية مسؤول أو سبق تفعيل النسخة المحمية ولا توجد نسخة محمية من المحرّك؛ شغّل Install-ProtectedEngine.ps1 كمسؤول"
    return ENGINE, None


# ---------------------------------------------------------------- المعالجات
def _space(route, body):
    act, kind = body.get("action"), body.get("kind")
    if act == "scan":
        threading.Thread(target=space.scan, daemon=True).start()
        if not space.state.get("big"):
            space.state["big"] = True
            threading.Thread(target=lambda: (space.scan_big(), space.state.update(big=False)), daemon=True).start()
        return 200, {"ok": True}
    if act == "qrestore":
        return 200, space.quarantine_restore(int(body.get("id", 0)))
    if act == "auto":
        return 200, {"ok": space.set_auto(kind, bool(body.get("on")))}
    if act == "report":
        import extras
        return 200, {"ok": extras.report_start()}
    if act == "dismmonthly":
        space._setting_set("space.dism_monthly", bool(body.get("on")))
        return 200, {"ok": True}
    if act == "elevated":
        return 200, {"ok": space.set_elevated(kind, bool(body.get("on")))}
    if act in ("cleanall", "clean"):
        r = _space_confirmed(route, body, act, kind)
        if r:
            return r
    if act == "cleanall":
        out = [dict(kind=k, **space.clean(k, dry=False, src="manual")) for k in space.BULK]
        return 200, {"ok": True, "mb": round(sum(o.get("mb", 0) for o in out), 1), "parts": out}
    if act == "preview":   # القياس في الخلفية؛ الصفحة تستطلع GET /api/space-preview
        return 200, space.preview_start(kind)
    if act == "clean":
        return 200, space.clean(kind, dry=False, early=(kind == "winold" and body.get("early") is True))
    return 400, {"error": "bad action"}


def _reaper_run(route, body):
    dry = body.get("dry")
    if not isinstance(dry, bool):
        return 400, {"error": "dry must be boolean"}
    if not dry:
        r = confirm_required(route, body)
        if r:
            return r
    return 200, insight.reaper_run(dry, os.path.dirname(MCCFG))


def _run_cleanup(route, body, invalidate):
    tier, dry = body.get("tier"), body.get("dry", True)
    if not isinstance(dry, bool):
        return 400, {"error": "dry must be boolean"}
    if tier not in TIERS:
        return 400, {"error": "bad tier"}
    if not dry:
        r = confirm_required(route, body)
        if r:
            return r
    p = MANUAL["proc"]
    if p and p.poll() is None:
        return 409, {"error": "a manual cleanup is already running"}
    engine, err = resolve_engine()
    if err:
        return 503, {"error": err}
    args = [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", engine, "-Tier", tier, "-Force", "-PolicyPath", POLICY]
    if dry:
        args.append("-DryRun")
    MANUAL.update(proc=subprocess.Popen(args, creationflags=CNW), tier=tier, dry=dry, started=time.strftime("%Y-%m-%dT%H:%M:%S"))
    invalidate()
    return 200, {"ok": True}


def post(route, body, invalidate=lambda: None):
    """(رمز الحالة، الردّ) لمسار من ROUTES، أو None إن لم يكن المسار من هذه الوحدة."""
    if route == "/api/space":
        return _space(route, body)
    if route == "/api/reaper-run":
        return _reaper_run(route, body)
    if route == "/api/run-cleanup":
        return _run_cleanup(route, body, invalidate)
    return None
