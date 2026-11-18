# -*- coding: utf-8 -*-
"""مسارات التطبيق (البند 8 من الخارطة: آخر ما كان مضمّنًا في do_POST): شرح الصحة والأخطاء بالنموذج المحلي وتفريغه من الذاكرة،
وعدّاد فتح الصفحات، وإيقاف الخادم. العقود (الرموز 200/400/503 وأجسام الردود) كما كانت في server.py حرفيًا.

    ROUTES                        المسارات التي تعالجها الوحدة
    post(route, body, invalidate) -> (رمز الحالة، الردّ) أو None إن لم يكن المسار من هنا

بقي في الخادم /api/ticket و/api/session/logout لأنهما يمسّان حالة التذاكر والجلسات وترويسة الكعكة في المعالج نفسه.
invalidate لا تُستدعى هنا (لا مسار يغيّر حالة المهام). الاستثناءات تُترك للخادم كما كان: do_POST يحوّل ValueError/TypeError
إلى 400 وسائرها إلى 500 مع معرّف طلب. explain يُستورد عند أول طلب كما كان (لا يُحمَّل عند الإقلاع).
"""
import json
import os
import threading
import time

import db
import errlog
import health
import health_score
import infra
import pageuse
import snapshot
from paths import ROOT

ROUTES = ("/api/explain-errors", "/api/llm-unload", "/api/explain", "/api/page-open", "/api/shutdown", "/api/ask")
EXIT = os._exit      # الخروج بعد ردّ /api/shutdown؛ يُستبدل في الاختبارات
EXIT_DELAY = 0.4     # ثوانٍ بين إرسال الردّ والخروج (كما كان في الخادم)


def _log_explain(what, res, ms):
    """صف لكل شرح: عدد الاستخدام وزمنه ونسبة النص الاحتياطي تحكم على بقاء الميزة."""
    detail = {"what": what, "model": res.get("model"), "ms": ms, "fallback": res.get("fallback"), "total": res.get("total")}
    try:
        with db._lock:
            c = db.conn()
            c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)",
                      (int(time.time()), "explain", "page", 1 if res.get("ok") else 0, json.dumps(detail, ensure_ascii=False)))
            c.commit()
    except Exception:
        errlog.swallowed()


def _explain_errors(body):
    import explain
    t0 = time.time()
    r = explain.errors(snapshot.native_metrics(), ROOT)
    _log_explain("errors", r, int((time.time() - t0) * 1000))
    return 200, r


def _llm_unload(body):
    import explain
    r = explain.unload()
    return (200, r) if r.get("ok") else (503, {"error": r.get("err", "failed")})


def _explain(body):
    import explain
    tasks = snapshot.tasks()
    m = snapshot.native_metrics()
    extra = ["الذاكرة: التزام %s%% ومتاح %s GB" % (m.get("commitPct"), m.get("availGB"))]
    try:
        extra += ["أكبر العمليات: %s ×%s بذاكرة خاصة %s MB" % (x["name"], x["n"], round(x["private_mb"] or 0)) for x in health.top_procs(5)]
        extra += ["تسرّب محتمل: %s من %s إلى %s MB" % (x["name"], x["fromMB"], x["toMB"]) for x in health.leaks()[:3]]
        extra += ["مهمة فاشلة: %s (النتيجة %s)" % (x.get("label"), x.get("result"))
                  for x in tasks if x.get("enabled") and not x.get("missing") and x.get("result") is not None and health_score.task_failed(x, time.time())][:4]
    except Exception:
        errlog.swallowed()
    t0 = time.time()
    r = explain.health(health.score(m, tasks)["parts"], m, extra)
    _log_explain("health", r, int((time.time() - t0) * 1000))
    return (200, r) if r.get("ok") else (503, {"error": r.get("err", "failed")})


def _ask(body):
    """يبدأ سؤالًا في الخلفية، والواجهة تتابعه عبر GET /api/ask. لا يغيّر شيئًا في الجهاز."""
    import assistant
    body = body if isinstance(body, dict) else {}
    job_id, why = assistant.start(body.get("q"), body.get("history"))
    if why == "busy":
        return 409, {"error": "سؤال آخر قيد الإجابة"}
    return (200, {"id": job_id}) if job_id else (400, {"error": "bad params"})


def _page_open(body):
    """الواجهة تبلّغ كل انتقال إلى صفحة (البند 5): صفّ يومي في SQLite، غير هدّام."""
    return pageuse.record(db.conn(), body)


def _shutdown(body):
    infra.log.info("shutdown requested")
    try:
        db.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        errlog.swallowed()
    threading.Timer(EXIT_DELAY, lambda: EXIT(0)).start()   # الردّ يُرسل أولًا ثم يخرج الخادم
    return 200, {"ok": True}


_TABLE = {"/api/explain-errors": _explain_errors, "/api/llm-unload": _llm_unload, "/api/explain": _explain, "/api/ask": _ask,
          "/api/page-open": _page_open, "/api/shutdown": _shutdown}


def post(route, body, invalidate=lambda: None):
    """(رمز الحالة، الردّ) لمسار من ROUTES، أو None إن لم يكن المسار من هذه الوحدة."""
    fn = _TABLE.get(route)
    return None if fn is None else fn(body)
