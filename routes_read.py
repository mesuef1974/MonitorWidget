# -*- coding: utf-8 -*-
"""المسارات القرائية (البند 6/ح من الخارطة): جدول GET /api/* المصادَق عليها بمعالجة أخطاء موحّدة. كل مسار دالة تأخذ
معاملات الاستعلام (قاموس نصّي) وتعيد الحمولة؛ الرموز والردود كما كانت في server.py حرفيًا، والجديد الوحيد أنّ كل 500
يُسجَّل بسببه الكامل في infra.log (كان 500 /api/advice يضيع سببه) وأنّ مسارًا كان يرفع استثناءً بلا ردّ صار يردّ 500 internal error.

    ROUTES            المسارات القرائية التي تجيب عنها هذه الوحدة (يفحصها check_all مع ROUTES في بقية الوحدات)
    get(path, q)      (رمز الحالة، الردّ) لمسار من ROUTES، أو None إن لم يكن المسار من هنا؛ q = {اسم: أول قيمة}

لا يُعالج هنا: /api/healthz (بلا مصادقة، في الخادم) والملفات الثابتة والصفحة.
"""
import secrets
import sqlite3
import time

import commitinfo
import cpuwatch
import db
import dbview
import gpupurge
import gpuwatch
import health
import infra
import insight
import memtrim
import pageuse
import routes_policy
import routes_settings
import sched
import shellwin
import snapshot
import space
import startup_mgr
import widgetctl
import localai
import winguard

try:
    import diskio_api
except Exception:
    diskio_api = None
try:
    import homeapi
except Exception:
    homeapi = None
try:
    import reliab
except Exception:
    reliab = None


def _ival(q, k, d, lo, hi):
    return max(lo, min(hi, int(q.get(k, d))))


def _db_rows(q):
    return dbview.rows(q.get("table", ""), q.get("limit", "25"), q.get("offset", "0"))


def _actions(q):
    with db._lock:
        rows = [dict(r) for r in db.conn().execute("SELECT * FROM actions ORDER BY ts DESC LIMIT 100")]
    return {"rows": rows}


def _space(q):
    infra.log.info("space: request received")
    t0 = time.time()
    d = space.overview()
    infra.log.info("space: overview built in %.2fs" % (time.time() - t0))
    d["quarantine"] = space.quarantine_list()
    return d


def _infra(q):
    with db.ro() as rc:
        return infra.overview(rc)


def _health(q):
    out = health.overview(snapshot.native_metrics(), snapshot.tasks())
    out["shell"] = shellwin.shell_status()
    return out


def _events(q):
    return db.events(_ival(q, "limit", 10, 1, 100), _ival(q, "offset", 0, 0, 10**7), q.get("sev"), (q.get("q") or "")[:60] or None, q.get("range"))


def _home(q):
    if homeapi is None:   # module failed to import: answer clearly instead of hanging the request
        return 503, {"error": "home module unavailable"}
    return homeapi.payload(db, q.get("range", "1h"))


# المسار: (الدالة، نصّ خطأ 500). النصّ إمّا ثابت أو دالة تأخذ الاستثناء (المسارات التي كانت تعيد str(e)[:200] أو collect failed)؛
# None = لم يكن للمسار معالج أخطاء (كان الاستثناء يسقط بلا ردّ) فيردّ الآن 500 internal error مع request_id كما في do_POST.
_E200 = lambda e: str(e)[:200]   # noqa: E731
_TABLE = {
    "/api/db": (lambda q: dbview.overview(), None),
    "/api/db/rows": (_db_rows, None),   # ValueError/sqlite3.Error → 400 bad params (أدناه)
    "/api/jobs": (lambda q: {"jobs": sched.listing(), "agent": space._agent_status(), "quarantine": space.quarantine_list()}, None),
    "/api/startup": (lambda q: startup_mgr.list_items(), None),
    "/api/actions": (_actions, None),
    "/api/space": (_space, None),
    "/api/space-preview": (lambda q: space.preview_status(q.get("kind", "")), None),   # تقدّم المعاينة الخلفية ونتيجتها
    "/api/space-clean": (lambda q: space.clean_status(q.get("kind", "")), None),     # تقدّم التنظيف الفعلي ونتيجته (kind=all للتنظيف الجماعي)
    "/api/widget": (lambda q: widgetctl.status(), None),
    "/api/infra": (_infra, _E200),
    "/api/state": (lambda q: snapshot.state(), lambda e: "collect failed: %s" % e),
    "/api/history": (lambda q: db.history(q.get("range", "24h")), _E200),
    "/api/events": (_events, _E200),
    "/api/runs": (lambda q: db.runs(_ival(q, "limit", 10, 1, 100), _ival(q, "offset", 0, 0, 10**7)), _E200),
    "/api/theme": (lambda q: {"theme": routes_settings.get_theme()}, _E200),
    "/api/appearance": (lambda q: routes_settings.APPEARANCE.get(), _E200),
    "/api/health": (_health, _E200),
    "/api/page-use": (lambda q: pageuse.stats(db.conn()), _E200),   # عدّاد فتح الصفحات (البند 5): آخر أسبوعين لصفحة الإعدادات والتحكم
    "/api/db-info": (lambda q: db.info(), _E200),
    "/api/maintenance": (lambda q: routes_policy.maintenance_state(), None),
    "/api/cpu": (lambda q: cpuwatch.api_payload(), "cpu data unavailable"),
    "/api/gpu": (lambda q: gpuwatch.api_payload(q.get("range", "24h")), "gpu data unavailable"),
    "/api/gpu-purge": (lambda q: gpupurge.view(), None),   # حالة آخر تفريغ لذاكرة الشاشة (الزر في صفحة الرسوميات)
    "/api/mem": (lambda q: insight.mem_payload(snapshot.native_metrics(), snapshot.metrics_extra(), q.get("range", "24h")), "data unavailable"),
    "/api/commit": (lambda q: commitinfo.payload(_ival(q, "n", 10, 1, 50)), "commit data unavailable"),   # التزام الذاكرة: الحالة الكلية وأعلى العمليات بـ PrivateUsage؛ التنظيف لا يخفّضه
    "/api/procs": (lambda q: insight.procs_payload(), "data unavailable"),
    "/api/wsl": (lambda q: insight.wsl_payload(q.get("range", "6h")), "data unavailable"),
    "/api/power": (lambda q: insight.power_payload(q.get("range", "24h")), "data unavailable"),
    "/api/alerts": (lambda q: insight.alerts_payload(), "data unavailable"),
    "/api/advice": (lambda q: insight.advice_payload(snapshot.native_metrics(), snapshot.metrics_extra(), routes_policy.maintenance_state(), memtrim.view()), "data unavailable"),
    "/api/io": (lambda q: diskio_api.payload(db, q.get("range", "24h"), q.get("dev", "disk0")), "io data unavailable"),
    "/api/home": (_home, "home data unavailable"),
    "/api/reliab": (lambda q: reliab.snapshot(db), "reliability data unavailable"),
    "/api/mini": (lambda q: snapshot.mini(), None),
    "/api/winguard": (lambda q: winguard.payload(), "window guard unavailable"),
    "/api/local-ai": (lambda q: localai.payload(), "local ai status unavailable"),
}
ROUTES = tuple(_TABLE)


def get(path, q):
    """(رمز الحالة، الردّ) لمسار قرائي، أو None. الدالة قد تعيد (رمز، ردّ) بنفسها (مثل 503 home) وإلا فالرمز 200."""
    entry = _TABLE.get(path)
    if entry is None:
        return None
    fn, err = entry
    try:
        out = fn(q)
    except (ValueError, sqlite3.Error) if path == "/api/db/rows" else ():
        return 400, {"error": "bad params"}
    except Exception as e:
        request_id = secrets.token_hex(6)
        infra.log.exception("GET failed request_id=%s route=%s", request_id, path)   # السبب الكامل في السجل (كان يضيع)
        if err is None:
            return 500, {"error": "internal error", "request_id": request_id}
        return 500, {"error": err(e) if callable(err) else err, "request_id": request_id}
    if isinstance(out, tuple) and len(out) == 2 and isinstance(out[0], int):
        return out
    return 200, out
