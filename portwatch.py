"""رصد استنفاد المنافذ: عند حادثة Tcpip 4231/4266 (نفاد المنافذ المؤقتة) يسجّل حدثًا يسمّي أكبر العمليات اتصالًا لحظتها.

سجل النظام لا يذكر العملية المتسببة، فتُؤخذ لقطة netstat (بلا نافذة وبصلاحية المستخدم) عند أول مرور على كل حادثة جديدة،
وتُجمَّع الاتصالات حسب العملية ثم تُسمّى أكبر TOP منها مع حالة TIME_WAIT الكلية. وقت الحدث المسجَّل هو وقت الحادثة نفسها
والجدول فريد على (ts, source, title) فالتكرار آمن. الحادثة الأقدم من اللقطة بوقت طويل تُسجَّل بلا أسماء («لقطة لاحقة» لا تُنسب إليها)
حتى لا تُنسب إلى عملية لا علاقة لها. الدوال parse_events/count_by_pid/describe/emit نقية وتُختبر معزولة.
"""
import calendar
import re
import subprocess
import time

INTERVAL = 120
FRESH_S = 600     # أقدم من هذا لا تنفع لقطة اليوم لتفسيرها
LOOKBACK_S = 3600
TOP = 3
SOURCE = "net"
_CNW = 0x08000000
_seen = set()
_cursor = [None]


def _run(args, timeout=30):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_CNW, timeout=timeout, check=False)
    return r.stdout if r.returncode == 0 else ""


def parse_events(xml):
    """أوقات حوادث 4231/4266 بثوانٍ يونكس."""
    out = []
    for m in re.finditer(r"<Event .*?</Event>", xml or "", re.DOTALL):
        t = re.search(r"SystemTime='(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)", m.group(0))
        if t:
            out.append(calendar.timegm(tuple(int(x) for x in t.groups()) + (0, 0, 0)))
    return sorted(out)


def count_by_pid(netstat_text):
    """(عدّاد الاتصالات لكل pid، عدّاد الحالات)."""
    by_pid, states = {}, {}
    for ln in (netstat_text or "").splitlines():
        c = ln.split()
        if len(c) >= 5 and c[0] == "TCP" and c[-1].isdigit():
            by_pid[int(c[-1])] = by_pid.get(int(c[-1]), 0) + 1
            states[c[3]] = states.get(c[3], 0) + 1
    return by_pid, states


def describe(by_pid, states, names, top=TOP):
    """نص بأكبر العمليات اتصالًا وTIME_WAIT الكلي."""
    ranked = sorted(((p, n) for p, n in by_pid.items() if p), key=lambda kv: (-kv[1], kv[0]))[:top]   # pid 0 = TIME_WAIT بلا مالك فلا يُنسب لأحد
    parts = ["%s (pid %d): %d" % (names.get(p, "غير معروف"), p, n) for p, n in ranked]
    return "أكبر العمليات اتصالًا لحظة الفحص: %s. TIME_WAIT الكلي: %d (منها %d بلا مالك معروف)" % ("، ".join(parts) or "لا اتصالات", states.get("TIME_WAIT", 0), by_pid.get(0, 0))


def emit(times, now, snapshot, add_event, seen, fresh=FRESH_S):
    """حدث لكل حادثة لم تُرَ؛ تُنسب الأسماء للحديثة فقط. يعيد عدد الأحداث."""
    n = 0
    for ts in times:
        if ts in seen:
            continue
        seen.add(ts)
        if now - ts <= fresh:
            add_event(ts, SOURCE, "warn", "استنفاد المنافذ المؤقتة", snapshot())
        else:
            add_event(ts, SOURCE, "warn", "استنفاد المنافذ المؤقتة", "لا لقطة للمستهلكين (الحادثة أقدم من %d دقائق عند أول مرور)" % (fresh // 60))
        n += 1
    return n


def _snapshot():
    import commitinfo
    by_pid, states = count_by_pid(_run(["netstat", "-ano"]))
    try:
        rows, _ = commitinfo.scan()
        names = {r["pid"]: r["name"] for r in rows}
    except Exception:
        names = {}
    return describe(by_pid, states, names)


def job():
    import db
    now = time.time()
    since = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - LOOKBACK_S))
    xp = "*[System[(EventID=4231 or EventID=4266) and TimeCreated[@SystemTime>='%s']]]" % since
    times = parse_events(_run(["wevtutil", "qe", "System", "/q:" + xp, "/c:100", "/rd:true", "/f:xml"]))
    n = emit(times, now, _snapshot, db.add_event, _seen)
    return "%d حادثة جديدة" % n
