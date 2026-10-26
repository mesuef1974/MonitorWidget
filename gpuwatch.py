"""GPU page backend: live nvidia-smi readout, 60 s history sampler, ranged history with statistics and plain-language notes.
Read-only toward the system: only runs nvidia-smi queries. History goes to the existing `gpu` table (same rows the health page uses)."""
import os
import time
import threading
import subprocess

import db
import health
import errlog

NVSMI = health.NVSMI
CNW = getattr(health, "CNW", 0)
INTERVAL = 60
RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400}
MAX_POINTS = 240
HOT_BITS = 0x08 | 0x20 | 0x40      # hardware slowdown, software thermal, hardware thermal
PWR_BITS = 0x04 | 0x80             # software power cap, hardware power brake

STATIC_FIELDS = ["name", "driver_version", "power.limit", "power.max_limit", "clocks.max.gr", "clocks.max.mem",
                 "pcie.link.gen.max", "pcie.link.width.max", "vbios_version", "memory.total"]
LIVE_FIELDS = ["temperature.gpu", "utilization.gpu", "utilization.memory", "memory.used", "memory.total", "power.draw",
               "clocks.gr", "clocks.mem", "fan.speed", "pstate", "pcie.link.gen.current", "pcie.link.width.current",
               "clocks_throttle_reasons.active"]

_lock = threading.Lock()
_ok_fields = {}      # field -> (answers, when probed); a failed probe is retried after RETRY_S, never cached for good
_down = {"until": 0.0, "why": None}
RETRY_S = 90
_cache = {"static": (0.0, None), "live": (0.0, None)}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _run(fields, timeout=10):
    r = subprocess.run([NVSMI, "--query-gpu=" + ",".join(fields), "--format=csv,noheader,nounits"],
                       capture_output=True, timeout=timeout, creationflags=CNW)
    if r.returncode != 0:
        _down["why"] = (r.stdout or r.stderr).decode("utf-8", "replace").strip()[:160] or ("nvidia-smi exit %s" % r.returncode)
        return None
    lines = r.stdout.decode("utf-8", "replace").strip().splitlines()
    if not lines:
        return None
    parts = [x.strip() for x in lines[0].split(",")]
    return parts if len(parts) == len(fields) else None


def _supported(fields):
    """Fields this driver understands. Each unknown field fails the whole query, so unknown ones are probed alone.
    A probe that failed (timeout under load, transient driver error) is retried later instead of disabling the field for the process lifetime."""
    now = time.time()
    out = []
    for f in fields:
        v = _ok_fields.get(f)
        if v is None or (not v[0] and now - v[1] > RETRY_S):
            try:
                ok = _run([f], 15) is not None
            except subprocess.TimeoutExpired:
                _down["why"] = "nvidia-smi timeout"
                ok = False
            except Exception:
                ok = False
            v = _ok_fields[f] = (ok, now)
            if not ok and not out and f == fields[0]:
                break          # the very first field fails: the tool itself is unavailable, do not spend a probe per field
        if v[0]:
            out.append(f)
    return out


def _query(fields):
    if not os.path.exists(NVSMI):
        return {}
    if time.time() < _down["until"]:
        return {}
    fs = _supported(fields)
    if not fs:
        _down["until"] = time.time() + 45      # tool unavailable: stop spawning it for every request
        return {}
    _down["why"] = None
    try:
        parts = _run(fs)
    except Exception:
        errlog.swallowed()
        return {}
    if not parts:
        return {}
    return dict(zip(fs, parts))


def static_info():
    now = time.time()
    with _lock:
        ts, v = _cache["static"]
        if v is not None and now - ts < 600:
            return v
    raw = _query(STATIC_FIELDS)
    v = {"name": raw.get("name") or "NVIDIA GPU", "driver": raw.get("driver_version"), "vbios": raw.get("vbios_version"),
         "powerLimit": _num(raw.get("power.limit")), "powerMax": _num(raw.get("power.max_limit")),
         "clkMaxGr": _num(raw.get("clocks.max.gr")), "clkMaxMem": _num(raw.get("clocks.max.mem")),
         "pcieGenMax": _num(raw.get("pcie.link.gen.max")), "pcieWidthMax": _num(raw.get("pcie.link.width.max")),
         "vramTotal": _num(raw.get("memory.total")), "available": bool(raw)}
    with _lock:
        _cache["static"] = (now, v)
    return v


def live_info():
    now = time.time()
    with _lock:
        ts, v = _cache["live"]
        if v is not None and now - ts < 3:
            return v
    raw = _query(LIVE_FIELDS)
    thr = raw.get("clocks_throttle_reasons.active")
    v = None
    if raw:
        v = {"ts": int(now), "temp": _num(raw.get("temperature.gpu")), "util": _num(raw.get("utilization.gpu")),
             "memUtil": _num(raw.get("utilization.memory")), "vram": _num(raw.get("memory.used")),
             "vramTotal": _num(raw.get("memory.total")), "power": _num(raw.get("power.draw")),
             "clkGr": _num(raw.get("clocks.gr")), "clkMem": _num(raw.get("clocks.mem")), "fan": _num(raw.get("fan.speed")),
             "pstate": raw.get("pstate"), "pcieGen": _num(raw.get("pcie.link.gen.current")),
             "pcieWidth": _num(raw.get("pcie.link.width.current")), "throttle": thr}
    with _lock:
        _cache["live"] = (now, v)
    return v


def job():
    """Agent job: one light nvidia-smi sample per minute into the gpu table (the 5-minute collector keeps adding per-process memory)."""
    if not os.path.exists(NVSMI):
        return "no nvidia-smi"
    r = _query(["temperature.gpu", "utilization.gpu", "memory.used", "memory.total", "power.draw", "pstate", "clocks_throttle_reasons.active"])
    if not r:
        return "no reading"
    g = {"temp": _num(r.get("temperature.gpu")), "util": _num(r.get("utilization.gpu")), "vram": _num(r.get("memory.used")),
         "vramTotal": _num(r.get("memory.total")), "power": _num(r.get("power.draw")), "pstate": r.get("pstate"),
         "throttle": r.get("clocks_throttle_reasons.active")}
    health.add_gpu(time.time(), g, [])
    return "t=%s u=%s" % (g["temp"], g["util"])


def _thr_int(s):
    try:
        return int(str(s), 16)
    except (TypeError, ValueError):
        return 0


def _pct(vals, p):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(len(s) - 1, lo + 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _stat(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {"min": round(min(vals), 1), "avg": round(sum(vals) / len(vals), 1), "max": round(max(vals), 1), "p95": round(_pct(vals, 95), 1)}


def _rows(span):
    with db._lock:
        c = health._c()
        rows = c.execute("SELECT ts,temp,util,vram_mb,vram_total,power,pstate,throttle FROM gpu WHERE ts>=? ORDER BY ts",
                         (int(time.time()) - span,)).fetchall()
        procs = c.execute("SELECT name,vram_mb FROM gpu_procs WHERE ts=(SELECT MAX(ts) FROM gpu_procs) ORDER BY vram_mb DESC LIMIT 8").fetchall()
        pts = c.execute("SELECT MAX(ts) FROM gpu_procs").fetchone()
    st = static_info()
    cap = (st.get("powerMax") or st.get("powerLimit") or 150.0) * 1.5
    clean = []
    for r in rows:
        r = dict(r)
        if r["power"] is not None and not (0 <= r["power"] <= cap):
            r["power"] = None          # nvidia-smi occasionally returns garbage (hundreds of watts); a laptop GPU cannot draw that
        if r["temp"] is not None and not (0 < r["temp"] < 120):
            r["temp"] = None
        if r["util"] is not None and not (0 <= r["util"] <= 100):
            r["util"] = None
        clean.append(r)
    return clean, [(p["name"], p["vram_mb"]) for p in procs], (pts[0] if pts else None)


def _series(rows):
    """At most MAX_POINTS points. Temperature and load keep the bucket peak (peaks are what matter); power and memory are averaged."""
    n = len(rows)
    k = max(1, -(-n // MAX_POINTS))
    out = {"t": [], "temp": [], "util": [], "power": [], "vram": [], "hot": [], "pwr": []}
    for i in range(0, n, k):
        b = rows[i:i + k]

        def col(key):
            return [r[key] for r in b if r[key] is not None]
        tmp, utl, pw, vr = col("temp"), col("util"), col("power"), col("vram_mb")
        out["t"].append(int(sum(r["ts"] for r in b) / len(b)))
        out["temp"].append(max(tmp) if tmp else None)
        out["util"].append(max(utl) if utl else None)
        out["power"].append(round(sum(pw) / len(pw), 1) if pw else None)
        out["vram"].append(round(sum(vr) / len(vr)) if vr else None)
        out["hot"].append(1 if any(_thr_int(r["throttle"]) & HOT_BITS for r in b) else 0)
        out["pwr"].append(1 if any(_thr_int(r["throttle"]) & PWR_BITS for r in b) else 0)
    return out


def _stats(rows, span, vram_total):
    n = len(rows)
    res = {"samples": n, "temp": _stat([r["temp"] for r in rows]), "util": _stat([r["util"] for r in rows]),
           "power": _stat([r["power"] for r in rows]), "vram": _stat([r["vram_mb"] for r in rows])}
    if not n:
        return res
    # time weights: gap to the next sample, capped so a sleeping laptop does not count as GPU time
    dts = []
    for i, r in enumerate(rows):
        nxt = rows[i + 1]["ts"] if i + 1 < n else r["ts"] + INTERVAL
        dts.append(max(0, min(nxt - r["ts"], 600)))
    tot = float(sum(dts)) or 1.0
    wh = sum((r["power"] or 0) * dt for r, dt in zip(rows, dts)) / 3600.0
    hot_s = sum(dt for r, dt in zip(rows, dts) if _thr_int(r["throttle"]) & HOT_BITS)
    pwr_s = sum(dt for r, dt in zip(rows, dts) if _thr_int(r["throttle"]) & PWR_BITS)
    ep, prev = 0, False
    for r in rows:
        cur = bool(_thr_int(r["throttle"]) & (HOT_BITS | PWR_BITS))
        if cur and not prev:
            ep += 1
        prev = cur
    idle_p = [r["power"] for r in rows if r["power"] is not None and (r["util"] or 0) < 5]
    mx = max((r for r in rows if r["temp"] is not None), key=lambda r: r["temp"], default=None)
    res.update({
        "energyWh": round(wh, 1), "hotMin": round(hot_s / 60.0, 1), "pwrMin": round(pwr_s / 60.0, 1), "episodes": ep,
        "idlePower": round(_pct(idle_p, 50), 1) if idle_p else None,
        "busyPct": round(100 * sum(dt for r, dt in zip(rows, dts) if (r["util"] or 0) >= 50) / tot, 1),
        "heavyPct": round(100 * sum(dt for r, dt in zip(rows, dts) if (r["util"] or 0) >= 90) / tot, 1),
        "warmPct": round(100 * sum(dt for r, dt in zip(rows, dts) if (r["temp"] or 0) >= 80) / tot, 1),
        "idlePct": round(100 * sum(dt for r, dt in zip(rows, dts) if (r["util"] or 0) < 5) / tot, 1),
        "maxTempTs": mx["ts"] if mx else None,
        "vramPeakPct": round(100 * max((r["vram_mb"] or 0) for r in rows) / vram_total, 1) if vram_total else None,
        "pwrPowerMax": max((r["power"] for r in rows if (_thr_int(r["throttle"]) & PWR_BITS) and r["power"] is not None), default=None),
        "hotTempMax": max((r["temp"] for r in rows if (_thr_int(r["throttle"]) & HOT_BITS) and r["temp"] is not None), default=None),
        "coverage": round(min(1.0, tot / max(INTERVAL, min(span, rows[-1]["ts"] + INTERVAL - rows[0]["ts"]))), 3),
        "histDays": round((rows[-1]["ts"] - rows[0]["ts"]) / 86400.0, 1), "first": rows[0]["ts"], "last": rows[-1]["ts"],
    })
    return res


def _notes(st, stat, live, igpu, span):
    out = []
    add = lambda lvl, title, text: out.append({"lvl": lvl, "title": title, "text": text})
    if not stat.get("samples"):
        add("info", "لا قياسات بعد", "يجمّع المرصد عيّنة كل دقيقة؛ تظهر الرسوم بعد دقيقتين.")
        return out
    if stat["hotMin"] > 0:
        ht = stat.get("hotTempMax")
        if ht is not None and ht >= 78:
            add("bad", "تقييد حراري", "خُفّضت ترددات الكرت بسبب الحرارة نحو %.0f دقيقة (حتى %d°). نظّف فتحات التهوية وارفع الجهاز عن الأسطح الناعمة." % (stat["hotMin"], ht))
        else:
            add("info", "علم تقييد حراري بحرارة منخفضة", "سجّل برنامج التشغيل علم «تقييد حراري برمجي» نحو %.0f دقيقة بينما الحرارة %s° فقط؛ غالباً علم عابر لا أثر له. يستحق الانتباه فقط إن رافقته حرارة فوق 80°." % (stat["hotMin"], ("%d" % ht) if ht is not None else "—"))
    if stat["pwrMin"] > 0:
        lim = st.get("powerLimit") or st.get("powerMax")
        pp = stat.get("pwrPowerMax")
        if lim and pp is not None and pp >= 0.7 * lim:
            add("warn", "حدّ القدرة", "بلغ الكرت سقف القدرة نحو %.0f دقيقة؛ هذا طبيعي تحت الأحمال الثقيلة ولا يضرّ، لكنه يحدّ الأداء." % stat["pwrMin"])
        else:
            add("info", "علم حدّ القدرة", "سجّل برنامج التشغيل علم «سقف القدرة» نحو %.0f دقيقة بينما السحب %s واط فقط؛ علم عابر لا أثر له." % (stat["pwrMin"], ("%.0f" % pp) if pp is not None else "—"))
    t = stat["temp"]
    if t and t["max"] >= 85:
        add("bad", "حرارة مرتفعة", "بلغت الذروة %d° (الأمان المعتاد تحت 85°)." % t["max"])
    elif t and t["max"] >= 80:
        add("warn", "حرارة عالية في الذروة", "بلغت الذروة %d°؛ مقبولة تحت الحمل لكن راقب التهوية." % t["max"])
    if stat.get("vramPeakPct") is not None and stat["vramPeakPct"] >= 90:
        add("warn", "ذاكرة الشاشة شبه ممتلئة", "بلغت الذروة %.0f%% من ذاكرة الكرت؛ قد تتباطأ التطبيقات الثقيلة." % stat["vramPeakPct"])
    ip = stat.get("idlePower")
    if ip is not None:
        if ip > 15:
            add("warn", "استهلاك مرتفع في الخمول", "يسحب الكرت نحو %.0f واط وهو خامل؛ تحقق من عملية تُبقيه مشغولاً أو من وضع «الكرت المنفصل فقط»." % ip)
        else:
            add("ok", "الكرت ينام في الخمول", "يسحب نحو %.1f واط وهو خامل، وهذا سليم." % ip)
    if igpu is not None and igpu.get("present") is False:
        add("info", "الكرت المدمج معطّل", "كل الرسوم تمرّ عبر الكرت المنفصل حتى في الخمول (حرارة وطاقة أكثر). وضع «هجين» من تطبيق الشركة ثم إعادة التشغيل يُرجع النوم.")
    if stat.get("coverage", 1) < 0.5:
        add("info", "فجوات في القياس", "القياسات تغطي %.0f%% من زمن السجل (الجهاز كان مطفأً أو نائماً في الباقي)." % (100 * stat["coverage"]))
    if span > 86400 and stat.get("histDays", 99) < span / 86400.0 * 0.9:
        add("info", "السجل أقصر من الفترة", "المتوفر %.1f يوماً من السجل فقط." % stat["histDays"])
    if not any(o["lvl"] in ("bad", "warn") for o in out):
        out.insert(0, {"lvl": "ok", "title": "الكرت بخير", "text": "لا تقييد حراري ولا ذروات مقلقة في هذه الفترة."})
    return out


def api_payload(rng="24h"):
    span = RANGES.get(rng, RANGES["24h"])
    st = static_info()
    live = live_info()
    rows, procs, procs_ts = _rows(span)
    vt = (live or {}).get("vramTotal") or st.get("vramTotal") or (rows[-1]["vram_total"] if rows else None)
    stat = _stats(rows, span, vt)
    ig = None
    try:
        ig = health.gpu_info().get("igpu")
    except Exception:
        errlog.swallowed()
    verdict = "ok"
    notes = _notes(st, stat, live, ig, span)
    for n in notes:
        if n["lvl"] == "bad":
            verdict = "bad"
            break
        if n["lvl"] == "warn":
            verdict = "warn"
    return {"ok": bool(st.get("available") or rows), "range": rng if rng in RANGES else "24h", "span": span, "now": int(time.time()),
            "static": st, "live": live, "series": _series(rows), "stats": stat,
            "procs": [{"name": n, "mb": v} for n, v in procs], "procsTs": procs_ts, "igpu": ig, "notes": notes, "verdict": verdict,
            "diag": None if st.get("available") else (_down["why"] or "nvidia-smi لم يُجب")}
