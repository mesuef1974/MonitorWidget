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

import commitmap
import db
import errlog
import insight
import procclose
import secure
import space
import winguard
from paths import CNW, ENGINE, MCCFG, POLICY, PROT_ENGINE_DIR, PWSH, TIER_TASK, TIERS

ROUTES = ("/api/space", "/api/reaper-run", "/api/run-cleanup", "/api/proc-close", "/api/winguard-silence", "/api/docker-stop")
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
    """تنظيف المساحة: الرمز مربوط أيضًا بالمعاينة التي رآها المستخدم (seen_mb جزء من المفتاح). إعادة القياس بعد قبول الرمز
    ورفضُ مجموعة مرشّحين كبرت فوق التسامح انتقلا إلى مهمة التنظيف الخلفية (space.clean_start) قبل أي حذف.
    يعيد None حين يجوز البدء، وإلا ردّ الخطأ."""
    seen = body.get("seen_mb")
    if isinstance(seen, bool) or not isinstance(seen, (int, float)) or not 0 <= seen <= 1e12 or not math.isfinite(seen):
        return 400, {"error": "seen_mb required"}
    return confirm_required(route, body)


# ---------------------------------------------------------------- التنظيف اليدوي (محرّك CleanupEngine.ps1)
MANUAL = {"proc": None, "tier": None, "dry": None, "started": None}
IS_ADMIN = secure.is_admin()


TASK_RUNNING = "267009"          # SCHED_S_TASK_RUNNING: رقم لا يتبدّل بلغة النظام، بخلاف عمود الحالة
TASK_GRACE_S, TASK_MAX_S = 3, 3600


def _task_running(task):
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", task, "/fo", "csv", "/v", "/nh"], capture_output=True, text=True,
                           errors="replace", timeout=10, creationflags=CNW)
    except (OSError, subprocess.TimeoutExpired):
        return True   # يُعاد الفحص، وTASK_MAX_S حدّ أعلى
    return r.stdout.split('","')[6:7] == [TASK_RUNNING]


class _TaskRun:
    """تشغيل فعلي عبر مهمة Cleanup_v2_* المرتفعة. واجهته poll() كـ Popen فيبقى منع التشغيل المتزامن وحالة الويدجت كما هما."""

    def __init__(self, task):
        self.task, self.started, self.checked, self.code = task, time.time(), 0.0, None

    def poll(self):
        now = time.time()
        if self.code is None and now - self.started >= TASK_GRACE_S and now - self.checked >= 2:
            self.checked = now
            if now - self.started > TASK_MAX_S or not _task_running(self.task):
                self.code = 0
        return self.code


def _tier_paused(tier):
    """مهمة المستوى لا تحمل -Force فتخرج بلا عمل إن كان موقوفًا؛ عند تعذّر القراءة يُعدّ موقوفًا فيبقى التشغيل المباشر."""
    try:
        with open(POLICY, encoding="utf-8-sig") as fh:
            return tier in json.load(fh).get("pausedTiers", [])
    except (OSError, ValueError, AttributeError):
        errlog.swallowed()
        return True


def _start_task(tier):
    """الخادم عادي فالمحرّك المباشر لا يصل إلى مجلدات النظام (font-cache يفشل، وwindows-temp لا يحذف شيئًا)؛ المهمة تعمل بأعلى صلاحية.
    يعيد _TaskRun، أو None ليعمل التشغيل المباشر."""
    if IS_ADMIN or _tier_paused(tier):
        return None
    try:
        r = subprocess.run(["schtasks", "/run", "/tn", TIER_TASK[tier]], capture_output=True, timeout=15, creationflags=CNW)
    except (OSError, subprocess.TimeoutExpired):
        errlog.swallowed()
        return None
    return _TaskRun(TIER_TASK[tier]) if r.returncode == 0 else None


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
    if act in ("cleanall", "clean"):   # الحذف في الخلفية؛ الصفحة تستطلع GET /api/space-clean
        early = act == "clean" and kind == "winold" and body.get("early") is True
        r = space.clean_start("all" if act == "cleanall" else kind, body["seen_mb"], early)
        return (200 if r["ok"] else 409 if r.get("running") else 400), r
    if act == "preview":   # القياس في الخلفية؛ الصفحة تستطلع GET /api/space-preview
        return 200, space.preview_start(kind)
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
    proc = None if dry or os.path.dirname(engine) != PROT_ENGINE_DIR else _start_task(tier)   # المهام تشير إلى النسخة المحمية وحدها
    if proc is None:
        args = [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", engine, "-Tier", tier, "-Force", "-PolicyPath", POLICY]
        if dry:
            args.append("-DryRun")
        proc = subprocess.Popen(args, creationflags=CNW)
    MANUAL.update(proc=proc, tier=tier, dry=dry, started=time.strftime("%Y-%m-%dT%H:%M:%S"))
    invalidate()
    return 200, {"ok": True}


def _proc_close(route, body, invalidate):
    """إغلاق عملية واحدة بطلب صريح (زر «إغلاق» في جدول الالتزام): فحص السياسة قبل طلب التأكيد (حتى لا يُطلب تأكيد لما سيُرفض)،
    ثم 428 بالرمز المربوط بالمعرّف والاسم وforce، ثم الإغلاق والتسجيل في actions."""
    pid, name, force = body.get("pid"), body.get("name"), body.get("force", False)
    if isinstance(pid, bool) or not isinstance(pid, int) or not isinstance(name, str) or not isinstance(force, bool):
        return 400, {"error": "bad params"}
    ok, reason = procclose.check(pid, name)
    if not ok:
        return (400 if reason == "bad params" else 409), {"error": reason, "pid": pid, "name": name}
    r = confirm_required(route, body)
    if r:
        return r
    res = procclose.close(pid, name, force=force)
    try:
        with db._lock:
            c = db.conn()
            c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)",
                      (int(time.time()), "procclose", "page", 1 if res.get("ok") and res.get("exited") else 0,
                       ("pid=%s name=%s force=%s method=%s exited=%s commitMB=%s err=%s" % (pid, name, force, res.get("method"), res.get("exited"), res.get("commitMB"), res.get("error")))[:200]))
            c.commit()
    except Exception:
        errlog.swallowed()
    invalidate()
    if not res.get("ok"):
        return 409, {"error": res.get("error", "failed"), "pid": pid, "name": name}
    return 200, res


def _win_silence(route, body, invalidate):
    """إسكات مهمة مجدولة بطلب صريح: السياسة، ثم 428، ثم تعديل إجرائها والتسجيل في actions."""
    name = body.get("name")
    ok, reason = winguard.check(name)
    if not ok:
        return (400 if reason == "bad params" else 409), {"error": reason, "name": name}
    r = confirm_required(route, body)
    if r:
        return r
    res = winguard.silence(name)
    try:
        with db._lock:
            c = db.conn()
            c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)",
                      (int(time.time()), "winguard", "page", 1 if res.get("ok") else 0, ("task=%s err=%s" % (name, res.get("error")))[:500]))
            c.commit()
    except Exception:
        errlog.swallowed()
    invalidate()
    if not res.get("ok"):
        return 409, {"error": res.get("error", "failed"), "name": name}
    return 200, res


def _docker_stop(route, body, invalidate):
    """إيقاف حاويات مشروع compose واحد (stop لا حذف؛ يُعاد تشغيلها بـ docker start) بطلب صريح من جدول الحاويات: المشروع يجب أن
    يكون في آخر قراءة، ثم 428، ثم الإيقاف والتسجيل في actions."""
    project = body.get("project")
    if not isinstance(project, str) or not project or len(project) > 128:
        return 400, {"error": "bad params"}
    if not commitmap.known_compose(project):
        return 409, {"error": "not_found", "project": project}
    r = confirm_required(route, body)
    if r:
        return r
    ok, detail = commitmap.compose_stop(project)
    try:
        with db._lock:
            c = db.conn()
            c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)",
                      (int(time.time()), "dockerstop", "page", 1 if ok else 0, ("project=%s containers=%s" % (project, detail))[:500]))
            c.commit()
    except Exception:
        errlog.swallowed()
    invalidate()
    if not ok:
        return 409, {"error": "failed" if detail != "not_found" else "not_found", "project": project}
    return 200, {"ok": True, "project": project, "containers": detail.split(",")}


def post(route, body, invalidate=lambda: None):
    """(رمز الحالة، الردّ) لمسار من ROUTES، أو None إن لم يكن المسار من هذه الوحدة."""
    if route == "/api/space":
        return _space(route, body)
    if route == "/api/reaper-run":
        return _reaper_run(route, body)
    if route == "/api/run-cleanup":
        return _run_cleanup(route, body, invalidate)
    if route == "/api/proc-close":
        return _proc_close(route, body, invalidate)
    if route == "/api/winguard-silence":
        return _win_silence(route, body, invalidate)
    if route == "/api/docker-stop":
        return _docker_stop(route, body, invalidate)
    return None
