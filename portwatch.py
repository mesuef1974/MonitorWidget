"""رصد استنفاد المنافذ: عند حادثة Tcpip 4231/4266 (نفاد المنافذ المؤقتة) يسجّل حدثًا يسمّي أكبر المستهلكين لحظة الذروة.

سجل النظام لا يذكر العملية المتسببة، والنفاد اندفاعة قصيرة: لقطة netstat بعد دقيقتين لا ترى إلا الحمل العادي (قياس 19/11:
نحو 700 اتصال من 16384 منفذًا بعد كل حادثة). لذا تأخذ sample_job كل SAMPLE_S ثانية لقطة اتصالات خفيفة (نحو 2 ms، بلا عملية
خارجية ولا صلاحية مسؤول) وتحفظ ملخّصها في حلقة بالذاكرة مدتها RING_S، وحين تظهر حادثة تُنسب إلى أعلى لقطة حولها في الحلقة.
الملخّص: اتصالات TCP ومنافذ UDP لكل عملية، وحالات TCP، وTIME_WAIT مجمّعًا حسب طرف الخدمة (المنفذ غير المؤقت) لأن
TIME_WAIT بلا مالك (pid 0)، ومالك منفذ الخدمة من سطر LISTENING. الأسماء تُحلّ عند اللقطة العالية فقط (فوق NAME_AT) لأن
العملية المندفعة قد تموت قبل الحادثة. إن لم تغطِّ الحلقة الحادثة (بعد إعادة تشغيل الوكيل) فاللقطة الحالية كما كان، والأقدم
من FRESH_S بلا أسماء حتى لا تُنسب إلى عملية لا علاقة لها. الجدول فريد على (ts, source, title) فالتكرار آمن.
الدوال parse_rows/parse_netstat/summarize/peak_near/describe/emit نقية وتُختبر معزولة.

اللقطة تُقرأ من جداول IP Helper مباشرة (GetExtendedTcpTable/GetExtendedUdpTable عبر ctypes، IPv4 وIPv6، بلا صلاحية
مسؤول) بدل تشغيل netstat -ano كل 10 ث (8,640 عملية يوميًا؛ البند 34 من المجلس 19/11). netstat احتياط إن فشلت الواجهة.
"""
import calendar
import collections
import ctypes
import re
import socket
import struct
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


def parse_rows(rows):
    """صفوف (proto، المحلي، البعيد، الحالة، pid) ← tcp لكل pid، udp لكل pid، حالات TCP، TIME_WAIT لكل طرف خدمة،
    ومالك كل منفذ مستمع. مصدر الصفوف netstat أو جداول IP Helper، بأسماء حالات netstat نفسها."""
    tcp, udp, states, tw, listen = {}, {}, {}, {}, {}
    for proto, local, remote, st, pid in rows:
        if proto == "TCP":
            tcp[pid] = tcp.get(pid, 0) + 1
            states[st] = states.get(st, 0) + 1
            if st == "LISTENING":
                listen[_port(local)] = pid
            elif st == "TIME_WAIT":
                svc = local if 0 < _port(local) < EPHEMERAL_MIN else remote
                tw[svc] = tw.get(svc, 0) + 1
        elif proto == "UDP":
            udp[pid] = udp.get(pid, 0) + 1
    return {"tcp": tcp, "udp": udp, "states": states, "tw": tw, "listen": listen}


def netstat_rows(text):
    for ln in (text or "").splitlines():
        c = ln.split()
        if len(c) >= 5 and c[0] == "TCP" and c[-1].isdigit():
            yield "TCP", c[1], c[2], c[3], int(c[-1])
        elif len(c) >= 4 and c[0] == "UDP" and c[-1].isdigit():
            yield "UDP", c[1], "*:*", None, int(c[-1])


def parse_netstat(text):
    """ملخّص netstat -ano (الاحتياط)."""
    return parse_rows(netstat_rows(text))


# ------------------------------------------------------------------ IP Helper (بديل netstat)
AF_INET, AF_INET6 = 2, 23
TCP_TABLE_OWNER_PID_ALL, UDP_TABLE_OWNER_PID = 5, 1
_TCP_STATES = {1: "CLOSED", 2: "LISTENING", 3: "SYN_SENT", 4: "SYN_RECEIVED", 5: "ESTABLISHED", 6: "FIN_WAIT_1", 7: "FIN_WAIT_2",
               8: "CLOSE_WAIT", 9: "CLOSING", 10: "LAST_ACK", 11: "TIME_WAIT", 12: "DELETE_TCB"}
# (صيغة struct لصف واحد، الوظيفة) لكل (بروتوكول، عائلة). الحقول DWORD ومصفوفات بايت فالمحاذاة 4 بلا حشو.
_LAYOUT = {("TCP", AF_INET): ("<6I", "GetExtendedTcpTable", TCP_TABLE_OWNER_PID_ALL),        # state, laddr, lport, raddr, rport, pid
           ("TCP", AF_INET6): ("<16sII16sIIII", "GetExtendedTcpTable", TCP_TABLE_OWNER_PID_ALL),  # laddr, lscope, lport, raddr, rscope, rport, state, pid
           ("UDP", AF_INET): ("<3I", "GetExtendedUdpTable", UDP_TABLE_OWNER_PID),          # laddr, lport, pid
           ("UDP", AF_INET6): ("<16sIII", "GetExtendedUdpTable", UDP_TABLE_OWNER_PID)}     # laddr, lscope, lport, pid


def _nport(dw):
    """المنفذ في البايتين الأدنيين بترتيب الشبكة."""
    return socket.ntohs(dw & 0xFFFF)


def _addr(af, a, scope, port):
    """بصيغة netstat: 1.2.3.4:80 و[::1]:80 و[fe80::1%12]:80."""
    if af == AF_INET:
        return f"{socket.inet_ntoa(struct.pack('<I', a))}:{port}"
    ip = socket.inet_ntop(socket.AF_INET6, a)
    return f"[{ip}{f'%{scope}' if scope else ''}]:{port}"


def decode_table(proto, af, buf):
    """بايتات الجدول (DWORD عدد الصفوف ثم الصفوف) ← صفوف parse_rows. دالة نقية تُختبر ببايتات مصنوعة."""
    fmt = _LAYOUT[(proto, af)][0]
    size = struct.calcsize(fmt)
    n = struct.unpack_from("<I", buf, 0)[0]
    out = []
    for i in range(n):
        f = struct.unpack_from(fmt, buf, 4 + i * size)
        if proto == "TCP" and af == AF_INET:
            st, la, lp, ra, rp, pid = f
            out.append(("TCP", _addr(af, la, 0, _nport(lp)), _addr(af, ra, 0, _nport(rp) if st != 2 else 0), _TCP_STATES.get(st, str(st)), pid))
        elif proto == "TCP":
            la, ls, lp, ra, rs, rp, st, pid = f
            out.append(("TCP", _addr(af, la, ls, _nport(lp)), _addr(af, ra, rs, _nport(rp) if st != 2 else 0), _TCP_STATES.get(st, str(st)), pid))
        elif af == AF_INET:
            la, lp, pid = f
            out.append(("UDP", _addr(af, la, 0, _nport(lp)), "*:*", None, pid))
        else:
            la, ls, lp, pid = f
            out.append(("UDP", _addr(af, la, ls, _nport(lp)), "*:*", None, pid))
    return out


def _fetch(proto, af):
    fmt, fn, cls = _LAYOUT[(proto, af)]
    call = getattr(ctypes.windll.iphlpapi, fn)
    call.restype = ctypes.c_ulong
    call.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.c_int, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    size = ctypes.c_ulong(0)
    for _ in range(4):   # الجدول قد يكبر بين طلب الحجم والقراءة (ERROR_INSUFFICIENT_BUFFER = 122)
        buf = ctypes.create_string_buffer(size.value + 64 * struct.calcsize(fmt)) if size.value else None
        r = call(buf, ctypes.byref(size), False, af, cls, 0)
        if r == 0 and buf is not None:
            return decode_table(proto, af, buf.raw)
        if r != 122:
            raise OSError(r, fn)
    raise OSError(122, fn)


def native_rows():
    """صفوف TCP وUDP لـ IPv4 وIPv6 كما يعرضها netstat -ano، بلا عملية خارجية (نحو 1 ms)."""
    rows = []
    for proto in ("TCP", "UDP"):
        for af in (AF_INET, AF_INET6):
            rows += _fetch(proto, af)
    return rows


def read_table(timeout=8):
    """لقطة الاتصالات: جداول IP Helper، وnetstat -ano إن فشلت الواجهة."""
    try:
        return parse_rows(native_rows())
    except (OSError, AttributeError, ValueError, struct.error):
        return parse_netstat(_run(["netstat", "-ano"], timeout=timeout))


def count_by_pid(parsed):
    """(عدّاد اتصالات TCP لكل pid، عدّاد الحالات) من ملخّص parse_rows، أو من نص netstat."""
    p = parse_netstat(parsed) if isinstance(parsed, str) else parsed
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
    by_pid, states = count_by_pid(read_table(timeout=30))
    return describe(by_pid, states, _names())


def sample_job():
    """لقطة خفيفة كل SAMPLE_S ثانية في الحلقة؛ تُحلّ الأسماء فقط حين تعلو الاتصالات."""
    now = time.time()
    parsed = read_table()
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
