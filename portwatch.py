"""رصد استنفاد المنافذ: عند حادثة Tcpip 4231/4266 (نفاد المنافذ المؤقتة) يسجّل حدثًا يسمّي أكبر المستهلكين لحظة الذروة.

سجل النظام لا يذكر العملية المتسببة، والنفاد اندفاعة قصيرة: لقطة netstat بعد دقيقتين لا ترى إلا الحمل العادي (قياس 19/11:
نحو 700 اتصال من 16384 منفذًا بعد كل حادثة). لذا تأخذ sample_job كل SAMPLE_S ثانية لقطة netstat خفيفة (نحو 30 ms، بلا نافذة
وبصلاحية المستخدم) وتحفظ ملخّصها في حلقة بالذاكرة مدتها RING_S، وحين تظهر حادثة تُنسب إلى أعلى لقطة حولها في الحلقة.
الملخّص: اتصالات TCP ومنافذ UDP لكل عملية، وحالات TCP، وTIME_WAIT مجمّعًا حسب طرف الخدمة (المنفذ غير المؤقت) لأن
TIME_WAIT بلا مالك (pid 0)، ومالك منفذ الخدمة من سطر LISTENING. الأسماء تُحلّ عند اللقطة العالية فقط (فوق NAME_AT) لأن
العملية المندفعة قد تموت قبل الحادثة. إن لم تغطِّ الحلقة الحادثة (بعد إعادة تشغيل الوكيل) فاللقطة الحالية كما كان، والأقدم
من FRESH_S بلا أسماء حتى لا تُنسب إلى عملية لا علاقة لها. الجدول فريد على (ts, source, title) فالتكرار آمن.
الدوال parse_netstat/summarize/peak_near/describe/emit نقية وتُختبر معزولة.
"""
import calendar
import collections
import re
import subprocess
import threading
import time

INTERVAL = 120
SAMPLE_S = 10
RING_S = 900
NAME_AT = 3000    # لقطة فيها اتصالات أكثر من هذا تُحلّ أسماء عملياتها فورًا
PEAK_BEFORE = 180
PEAK_AFTER = 30
EPHEMERAL_MIN = 49152
FRESH_S = 600     # أقدم من هذا لا تنفع لقطة اليوم لتفسيرها
LOOKBACK_S = 3600
TOP = 3
SOURCE = "net"
_CNW = 0x08000000
_seen = set()
_ring = collections.deque()
_ring_lock = threading.Lock()


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


def _port(addr):
    p = addr.rsplit(":", 1)[-1]
    return int(p) if p.isdigit() else 0


def parse_netstat(text):
    """ملخّص netstat -ano: tcp لكل pid، udp لكل pid، حالات TCP، TIME_WAIT لكل طرف خدمة، ومالك كل منفذ مستمع."""
    tcp, udp, states, tw, listen = {}, {}, {}, {}, {}
    for ln in (text or "").splitlines():
        c = ln.split()
        if len(c) >= 5 and c[0] == "TCP" and c[-1].isdigit():
            pid, st = int(c[-1]), c[3]
            tcp[pid] = tcp.get(pid, 0) + 1
            states[st] = states.get(st, 0) + 1
            if st == "LISTENING":
                listen[_port(c[1])] = pid
            elif st == "TIME_WAIT":
                svc = c[1] if 0 < _port(c[1]) < EPHEMERAL_MIN else c[2]
                tw[svc] = tw.get(svc, 0) + 1
        elif len(c) >= 4 and c[0] == "UDP" and c[-1].isdigit():
            udp[int(c[-1])] = udp.get(int(c[-1]), 0) + 1
    return {"tcp": tcp, "udp": udp, "states": states, "tw": tw, "listen": listen}


def count_by_pid(netstat_text):
    """(عدّاد اتصالات TCP لكل pid، عدّاد الحالات)."""
    p = parse_netstat(netstat_text)
    return p["tcp"], p["states"]


def _top(d, n=TOP, skip_zero=True):
    return sorted(((k, v) for k, v in d.items() if not (skip_zero and k == 0)), key=lambda kv: (-kv[1], str(kv[0])))[:n]


def summarize(ts, parsed, names=None):
    """لقطة صغيرة تُحفظ في الحلقة: المجاميع وأعلى TOP في كل بُعد فقط."""
    tcp, udp = parsed["tcp"], parsed["udp"]
    return {
        "ts": ts,
        "tcp": sum(tcp.values()), "udp": sum(udp.values()),
        "tw": parsed["states"].get("TIME_WAIT", 0),
        "top_tcp": _top(tcp), "top_udp": _top(udp),
        "top_tw": [(svc, n, parsed["listen"].get(_port(svc))) for svc, n in _top(parsed["tw"], skip_zero=False)],
        "names": dict(names or {}),
    }


def peak_near(ring, ts, before=PEAK_BEFORE, after=PEAK_AFTER):
    """أعلى لقطة (tcp+udp) في [ts-before, ts+after]، أو None."""
    near = [s for s in ring if ts - before <= s["ts"] <= ts + after]
    return max(near, key=lambda s: (s["tcp"] + s["udp"], -abs(s["ts"] - ts))) if near else None


def describe_peak(s, names, ts):
    """نص الذروة: الأكبر في TCP وUDP وأطراف TIME_WAIT مع مالكها."""
    nm = dict(names)
    nm.update(s["names"])

    def who(pid):
        return f"{nm.get(pid, 'غير معروف')} (pid {pid})"
    lag = int(ts - s["ts"])
    parts = [f"ذروة المنافذ قبل الحادثة بـ {lag} ث" if lag >= 0 else f"ذروة المنافذ بعد الحادثة بـ {-lag} ث",
             f"TCP {s['tcp']} (TIME_WAIT {s['tw']})، UDP {s['udp']}"]
    if s["top_tcp"]:
        parts.append("أكبر TCP: " + "، ".join(f"{who(p)}: {n}" for p, n in s["top_tcp"]))
    if s["top_udp"]:
        parts.append("أكبر UDP: " + "، ".join(f"{who(p)}: {n}" for p, n in s["top_udp"]))
    if s["top_tw"]:
        parts.append("TIME_WAIT حسب الخدمة: " + "، ".join(f"{svc}{' ← ' + who(o) if o else ''}: {n}" for svc, n, o in s["top_tw"]))
    return ". ".join(parts)


def describe(by_pid, states, names, top=TOP):
    """نص بأكبر العمليات اتصالًا وTIME_WAIT الكلي."""
    ranked = sorted(((p, n) for p, n in by_pid.items() if p), key=lambda kv: (-kv[1], kv[0]))[:top]   # pid 0 = TIME_WAIT بلا مالك فلا يُنسب لأحد
    parts = [f"{names.get(p, 'غير معروف')} (pid {p}): {n}" for p, n in ranked]
    return (f"أكبر العمليات اتصالًا لحظة الفحص: {'، '.join(parts) or 'لا اتصالات'}. "
            f"TIME_WAIT الكلي: {states.get('TIME_WAIT', 0)} (منها {by_pid.get(0, 0)} بلا مالك معروف)")


def emit(times, now, snapshot, add_event, seen, fresh=FRESH_S, peak=None):
    """حدث لكل حادثة لم تُرَ: الذروة من الحلقة إن غطّتها، وإلا لقطة الآن للحديثة فقط. يعيد عدد الأحداث."""
    n = 0
    for ts in times:
        if ts in seen:
            continue
        seen.add(ts)
        p = peak(ts) if peak else None
        if p:
            add_event(ts, SOURCE, "warn", "استنفاد المنافذ المؤقتة", p)
        elif now - ts <= fresh:
            add_event(ts, SOURCE, "warn", "استنفاد المنافذ المؤقتة", snapshot())
        else:
            add_event(ts, SOURCE, "warn", "استنفاد المنافذ المؤقتة", f"لا لقطة للمستهلكين (الحادثة أقدم من {fresh // 60} دقائق عند أول مرور)")
        n += 1
    return n


def _names():
    import commitinfo
    try:
        rows, _ = commitinfo.scan()
        return {r["pid"]: r["name"] for r in rows}
    except Exception:   # noqa: BLE001 — فشل تسمية العمليات لا يمنع تسجيل الحادثة ولا لقطة الحلقة
        return {}


def _snapshot():
    by_pid, states = count_by_pid(_run(["netstat", "-ano"]))
    return describe(by_pid, states, _names())


def sample_job():
    """لقطة خفيفة كل SAMPLE_S ثانية في الحلقة؛ تُحلّ الأسماء فقط حين تعلو الاتصالات."""
    now = time.time()
    parsed = parse_netstat(_run(["netstat", "-ano"], timeout=8))
    total = sum(parsed["tcp"].values()) + sum(parsed["udp"].values())
    s = summarize(now, parsed, _names() if total >= NAME_AT else None)
    with _ring_lock:
        _ring.append(s)
        while _ring and _ring[0]["ts"] < now - RING_S:
            _ring.popleft()
    return f"{total} اتصال"


def _peak(ts):
    with _ring_lock:
        s = peak_near(list(_ring), ts)
    return describe_peak(s, _names() if not s["names"] else {}, ts) if s else None


def job():
    import db
    now = time.time()
    since = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - LOOKBACK_S))
    xp = f"*[System[(EventID=4231 or EventID=4266) and TimeCreated[@SystemTime>='{since}']]]"
    times = parse_events(_run(["wevtutil", "qe", "System", "/q:" + xp, "/c:100", "/rd:true", "/f:xml"]))
    n = emit(times, now, _snapshot, db.add_event, _seen, peak=_peak)
    return f"{n} حادثة جديدة"
