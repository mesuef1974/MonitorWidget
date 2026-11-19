"""عدّاد فتح الصفحات (البند 5 من الخارطة): كل انتقال إلى صفحة في web/index.html يبلّغ POST /api/page-open فيُسجَّل صفًّا يوميًا
في SQLite، وGET /api/page-use يعيد مجموع آخر أسبوعين لصفحة الإعدادات والتحكم حتى يُقصّ غير المستخدم بعد اكتمال المدة.
لا localStorage: العدّ في الخادم يصمد عبر الغلاف وEdge والمتصفح ويبقى بعد مسح بيانات المتصفح.

    record(c, body, now=None) -> (رمز الحالة، الردّ)   لـ {"page": "<مفتاح PAGES>"}
    stats(c, now=None)        -> ملخص النافذة والصفحات

الاتصال c هو db.conn() في الخادم أو اتصال sqlite3 مستقل في الاختبارات؛ الجدول يُنشأ عند الحاجة (DDL متكرر آمن).
على اتصال الخادم المشترك يُستدعى عبر record_shared/stats_shared تحت db._lock، وكل كتابة معاملة خاصة تُودَع أو يُتراجع عنها
قبل تحرير القفل: لا يُسقط تراجعها دفعة كاتب آخر (import_all)، ولا يترك ردّ 400 معاملة مفتوحة (مراجعة 19/11/2026).
"""
import datetime
import json
import re
import time

WINDOW_DAYS = 14   # مدة القياس المتفق عليها قبل قرار القصّ؛ النافذة متحركة: اليوم و13 يومًا قبله
KEEP_DAYS = 60     # الصفوف الأقدم تُحذف عند أول تسجيل بعدها (أرشيف قصير يكفي لمراجعة القرار)
MAX_PAGES = 64     # سقف المفاتيح المميزة: الواجهة فيها نحو 20 صفحة؛ ما زاد يُرفض ولا يُخزَّن
KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")   # مفاتيح PAGES في index.html: أحرف لاتينية صغيرة وأرقام وشرطة
SINCE_KEY = "pageuse.since"                      # أول يوم سُجّل فيه فتح (في جدول meta)
DWELL_S = 5        # فتحٌ تلاه انتقال إلى صفحة أخرى قبل 5 ث مرورٌ عابر (جولة آلية أو تقليب القائمة): يُسحب من العدّ
LAST_KEY = "pageuse.last"                        # آخر فتح محسوب {page, day, ts, prev} ليُسحب إن تبيّن أنه عابر
CLEAN_KEY = "pageuse.clean"                      # يوم بدء النافذة النظيفة: ما قبله كان ملوّثًا بجولات آلية فحُذف مرة واحدة
DDL = (
    "CREATE TABLE IF NOT EXISTS page_opens("
    "day TEXT NOT NULL, page TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, last_ts INTEGER NOT NULL, "
    "PRIMARY KEY(day, page)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)",   # نفس تعريف db.py؛ لا يغيّره إن وُجد
)


def _day(ts):
    """اليوم المحلي بصيغة YYYY-MM-DD (كما تحسب v_daily في db.py بـ localtime)."""
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def ensure(c):
    """ينشئ الجدولين إن لم يوجدا؛ execute لا executescript حتى لا يُغلق معاملة مفتوحة على اتصال الخادم المشترك."""
    for stmt in DDL:
        c.execute(stmt)


def _clean_once(c, day):
    """مرة واحدة بعد إضافة سحب العابر: يحذف العدّ القديم الملوّث ويبدأ القياس من اليوم. يعيد True إن نظّف."""
    if c.execute("SELECT 1 FROM meta WHERE key=?", (CLEAN_KEY,)).fetchone():
        return False
    c.execute("DELETE FROM page_opens")
    c.execute("DELETE FROM meta WHERE key IN (?,?)", (SINCE_KEY, LAST_KEY))
    c.execute("INSERT INTO meta(key, value) VALUES(?,?)", (CLEAN_KEY, day))
    return True


def _retract_transient(c, page, now):
    """يسحب الفتح السابق إن كان لصفحة أخرى ولم يمكث DWELL_S. يعيد True إن سحب."""
    r = c.execute("SELECT value FROM meta WHERE key=?", (LAST_KEY,)).fetchone()
    try:
        last = json.loads(r[0]) if r else None
    except ValueError:
        last = None
    if not isinstance(last, dict) or last.get("page") == page or not 0 <= now - int(last.get("ts") or 0) < DWELL_S:
        return False
    c.execute("UPDATE page_opens SET n=n-1, last_ts=? WHERE day=? AND page=? AND n>0", (last.get("prev") or 0, last.get("day"), last.get("page")))
    c.execute("DELETE FROM page_opens WHERE day=? AND page=? AND n<=0", (last.get("day"), last.get("page")))
    return True


def record(c, body, now=None):
    """تسجيل فتح صفحة: (200, {ok, page, day}) أو (400, {error}). المفتاح من الواجهة يُقبل بالنمط فقط، وبسقف للمفاتيح المميزة."""
    page = body.get("page") if isinstance(body, dict) else None
    if not isinstance(page, str) or not KEY_RE.match(page):
        return 400, {"error": "bad page key"}
    now = int(time.time() if now is None else now)
    try:
        code, reply = _record(c, page, now)
    except BaseException:
        c.rollback()   # لا معاملة نصف منفّذة على الاتصال المشترك
        raise
    if code == 200:
        c.commit()
    else:
        c.rollback()   # الرفض يتراجع عن التنظيف والسحب اللذين سبقاه ولا يترك معاملة مفتوحة لكاتب آخر
    return code, reply


def _record(c, page, now):
    """متن record داخل معاملة واحدة؛ المستدعي يودعها أو يتراجع عنها."""
    day = _day(now)
    ensure(c)
    _clean_once(c, day)
    _retract_transient(c, page, now)
    if not c.execute("SELECT 1 FROM page_opens WHERE page=? LIMIT 1", (page,)).fetchone() \
            and c.execute("SELECT COUNT(DISTINCT page) FROM page_opens").fetchone()[0] >= MAX_PAGES:
        return 400, {"error": "too many pages"}
    prev = c.execute("SELECT last_ts FROM page_opens WHERE day=? AND page=?", (day, page)).fetchone()
    c.execute("INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (LAST_KEY, json.dumps({"page": page, "day": day, "ts": now, "prev": prev[0] if prev else None})))
    c.execute("INSERT INTO page_opens(day, page, n, last_ts) VALUES(?,?,1,?) "
              "ON CONFLICT(day, page) DO UPDATE SET n=n+1, last_ts=excluded.last_ts", (day, page, now))
    c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES(?,?)", (SINCE_KEY, day))
    c.execute("DELETE FROM page_opens WHERE day < ?", (_day(now - KEEP_DAYS * 86400),))
    return 200, {"ok": True, "page": page, "day": day}


def record_shared(body):
    """record على اتصال الخادم المشترك تحت db._lock."""
    import db
    with db._lock:
        return record(db.conn(), body)


def stats_shared():
    """stats على اتصال الخادم المشترك تحت db._lock (قد تكتب تنظيف المرة الواحدة)."""
    import db
    with db._lock:
        return stats(db.conn())


def stats(c, now=None):
    """ملخص الاستخدام: نافذة آخر WINDOW_DAYS يومًا (from..today)، أول يوم قياس since وعدد الأيام المنقضية منه، وcomplete حين تكتمل المدة.
    الصفحات التي لم تُفتح لا تظهر هنا: الواجهة تعرف قائمة PAGES وتعرضها بصفر."""
    now = int(time.time() if now is None else now)
    try:
        ensure(c)
        if _clean_once(c, _day(now)):
            c.commit()
    except BaseException:
        c.rollback()
        raise
    today, start = _day(now), _day(now - (WINDOW_DAYS - 1) * 86400)
    rows = c.execute("SELECT page, SUM(n), COUNT(*), MAX(last_ts) FROM page_opens WHERE day >= ? GROUP BY page ORDER BY SUM(n) DESC, page",
                     (start,)).fetchall()
    r = c.execute("SELECT value FROM meta WHERE key=?", (SINCE_KEY,)).fetchone()
    since = r[0] if r else None
    elapsed = 0
    if since:
        try:
            elapsed = max(0, (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(since)).days) + 1
        except ValueError:
            elapsed = 0
    return {"windowDays": WINDOW_DAYS, "since": since, "from": start, "today": today, "elapsedDays": elapsed,
            "complete": elapsed >= WINDOW_DAYS,
            "pages": [{"page": p, "opens": int(n or 0), "days": int(d or 0), "last": last} for p, n, d, last in rows]}
