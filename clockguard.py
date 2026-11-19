"""حارس قفزة الساعة لكل حذف بالعمر (prune).

في 14/11/2026 قفزت ساعة الجدار إلى 31/12/2026 فحذف prune نحو 23 يومًا من بيانات القرص
الدقيقية في جولة واحدة. الحارس يعطي «الآن» الآمن: لا يتقدم أسرع من الزمن الفعلي المنقضي
مقيسًا بـ GetTickCount64 (مدة التشغيل منذ الإقلاع، تشمل السكون، واحدة لكل العمليات)،
مع هامش لحاق صغير. قفزة الساعة للأمام لا تحذف إلا بقدر الهامش؛ القفزة للخلف تحذف أقل.

المرساة لكل مفتاح في جدول meta: "<الآن المعتمد>|<ثواني التشغيل>". لا يستورد db.py.
"""
import ctypes
import sys
import time

SAME_BOOT_SLACK_S = 600         # هامش لكل جولة في الإقلاع نفسه: يلحق تأخّرًا لم يُقَس (إقلاع لم يُكشف)
SAME_BOOT_SLACK_FRAC = 0.01
REBOOT_CATCHUP_S = 12 * 3600    # بعد إعادة إقلاع لا يُعرف الزمن المنقضي: يلحق 12 ساعة لكل جولة على الأكثر
DISTRUST_LEAD_S = 2 * 86400     # مرساة متقدمة على الساعة بأكثر من يومين = مرساة كُتبت أثناء قفزة: تُترك


def uptime_s():
    """ثواني منذ الإقلاع، تشمل السكون والإسبات؛ تُصفَّر عند الإقلاع فقط."""
    if sys.platform == "win32":
        f = ctypes.windll.kernel32.GetTickCount64
        f.restype = ctypes.c_ulonglong
        return f() / 1000.0
    return time.monotonic()


def safe_now(conn, key, now=None, up=None):
    """«الآن» الذي يُحسب منه حدّ الحذف. conn اتصال sqlite3 يملك المستدعي معاملته (يُودِعها هو)."""
    now = float(time.time() if now is None else now)
    up = float(uptime_s() if up is None else up)
    conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    anchor = None
    if row:
        try:
            a_now, a_up = (float(x) for x in str(row[0]).split("|"))
            anchor = (a_now, a_up)
        except ValueError:
            anchor = None
    if anchor is None:
        used = keep = now
    else:
        a_now, a_up = anchor
        if up >= a_up:
            elapsed = up - a_up
            projected = a_now + elapsed
            limit = projected + SAME_BOOT_SLACK_S + SAME_BOOT_SLACK_FRAC * elapsed
        else:                                   # أُعيد الإقلاع بعد المرساة
            projected = a_now
            limit = a_now + REBOOT_CATCHUP_S
        used = min(now, limit)
        keep = max(used, projected) if projected - now <= DISTRUST_LEAD_S else used
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, f"{int(keep)}|{up:.3f}"))
    return int(used)
