"""عدّاد فتح الصفحات (البند 5 من الخارطة): كل انتقال إلى صفحة في web/index.html يبلّغ POST /api/page-open فيُسجَّل صفًّا يوميًا
في SQLite، وGET /api/page-use يعيد مجموع آخر أسبوعين لصفحة الإعدادات والتحكم حتى يُقصّ غير المستخدم بعد اكتمال المدة.
لا localStorage: العدّ في الخادم يصمد عبر الغلاف وEdge والمتصفح ويبقى بعد مسح بيانات المتصفح.

    record(c, body, now=None) -> (رمز الحالة، الردّ)   لـ {"page": "<مفتاح PAGES>"}
    stats(c, now=None)        -> ملخص النافذة والصفحات

الاتصال c هو db.conn() في الخادم أو اتصال sqlite3 مستقل في الاختبارات؛ الجدول يُنشأ عند الحاجة (DDL متكرر آمن).
"""
import datetime
import re
import time

WINDOW_DAYS = 14   # مدة القياس المتفق عليها قبل قرار القصّ؛ النافذة متحركة: اليوم و13 يومًا قبله
KEEP_DAYS = 60     # الصفوف الأقدم تُحذف عند أول تسجيل بعدها (أرشيف قصير يكفي لمراجعة القرار)
MAX_PAGES = 64     # سقف المفاتيح المميزة: الواجهة فيها نحو 20 صفحة؛ ما زاد يُرفض ولا يُخزَّن
KEY_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")   # مفاتيح PAGES في index.html: أحرف لاتينية صغيرة وأرقام وشرطة
SINCE_KEY = "pageuse.since"                      # أول يوم سُجّل فيه فتح (في جدول meta)
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


def record(c, body, now=None):
    """تسجيل فتح صفحة: (200, {ok, page, day}) أو (400, {error}). المفتاح من الواجهة يُقبل بالنمط فقط، وبسقف للمفاتيح المميزة."""
    page = body.get("page") if isinstance(body, dict) else None
    if not isinstance(page, str) or not KEY_RE.match(page):
        return 400, {"error": "bad page key"}
    now = int(time.time() if now is None else now)
    day = _day(now)
    ensure(c)
    if not c.execute("SELECT 1 FROM page_opens WHERE page=? LIMIT 1", (page,)).fetchone() \
            and c.execute("SELECT COUNT(DISTINCT page) FROM page_opens").fetchone()[0] >= MAX_PAGES:
        return 400, {"error": "too many pages"}
    c.execute("INSERT INTO page_opens(day, page, n, last_ts) VALUES(?,?,1,?) "
              "ON CONFLICT(day, page) DO UPDATE SET n=n+1, last_ts=excluded.last_ts", (day, page, now))
    c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES(?,?)", (SINCE_KEY, day))
    c.execute("DELETE FROM page_opens WHERE day < ?", (_day(now - KEEP_DAYS * 86400),))
    c.commit()
    return 200, {"ok": True, "page": page, "day": day}


def stats(c, now=None):
    """ملخص الاستخدام: نافذة آخر WINDOW_DAYS يومًا (from..today)، أول يوم قياس since وعدد الأيام المنقضية منه، وcomplete حين تكتمل المدة.
    الصفحات التي لم تُفتح لا تظهر هنا: الواجهة تعرف قائمة PAGES وتعرضها بصفر."""
    now = int(time.time() if now is None else now)
    ensure(c)
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
