"""Reliability history: the single reader of the Windows event logs (read-only: wevtutil qe) for the whole app.
The agent job `relia` (every 30 minutes) reads only what is new since its last scan and keeps it in rel_events;
GET /api/reliab builds the panel from rel_events alone, so the server never runs wevtutil.
Before 20/11/2026 two readers did the same job: relia.py (pwsh + relia_scan.ps1, hourly) and this module (wevtutil, 30 days every 30 min).
CLI: python reliab.py --scan (one scan into monitor.db, prints a summary) | --selftest"""
import calendar, json, subprocess, sys, threading, time
import xml.etree.ElementTree as ET

NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
DAYS = 30
OVERLAP = 86400            # each scan re-reads one day before the last one: late-written events and small clock steps
TTL = 60                   # seconds the panel result is reused (it is a database read now)
_CF = 0x08000000           # CREATE_NO_WINDOW

KINDS = {  # kind: (Arabic label, severity weight)
    "bsod": ("شاشة زرقاء", 3), "unexpected": ("إغلاق أو إعادة تشغيل مفاجئة", 3), "freeze": ("تجمّد النظام", 3),
    "disk": ("خطأ في القرص", 3), "hardware": ("خطأ عتاد", 3), "tdr": ("إعادة ضبط برنامج تشغيل الشاشة", 2),
    "hang": ("تعليق تطبيق", 1), "crash": ("انهيار تطبيق", 1),
}
DISK_PROV = ("disk", "stornvme", "storahci", "storport", "microsoft-windows-storport", "iastora", "nvme")
CAP = 3000                 # events per query; reaching it means the window is cut short (reported as partial)
# Queries name their providers: by EventID alone, 7/17-20/129/153 from Windows Update, TPM, Hyper-V and Kernel-Boot
# filled the old 400-event cap in about 8 days (19/11/2026: 3 unexpected shutdowns shown for 15 real ones).
QUERIES = (
    ("System", ("Microsoft-Windows-Kernel-Power",), (41,)),
    ("System", ("EventLog",), (6008,)),
    ("System", ("Microsoft-Windows-WER-SystemErrorReporting", "BugCheck"), (1001,)),
    ("System", ("disk", "stornvme", "storahci", "Microsoft-Windows-StorPort", "iaStorAVC", "iaStorV", "iaStorA", "nvmedisk"),
     (7, 11, 51, 153, 129, 157)),
    ("System", ("Microsoft-Windows-WHEA-Logger",), (17, 18, 19, 20, 1)),
    ("System", ("Display",), (4101,)),
    ("Application", ("Application Error", "Application Hang"), (1000, 1002)),
)
META = "rel_scan"          # meta row: {"ts": last scan, "failed": [...], "liveFrom": oldest ts the live log still covered}

_lock = threading.Lock()
_cache = {"data": None, "at": 0.0}


def _q(log, providers, ids, window_s=DAYS * 86400):
    prov = " or ".join("@Name='%s'" % p for p in providers)
    cond = " or ".join("EventID=%d" % i for i in ids)
    return ["wevtutil", "qe", log, "/q:*[System[Provider[%s] and (%s) and TimeCreated[timediff(@SystemTime) <= %d]]]" % (prov, cond, int(window_s) * 1000),
            "/f:xml", "/c:%d" % CAP, "/rd:true"]


def _run(cmd, timeout=60):
    """XML text, or None when wevtutil failed (a failure must not read as «no incidents»)."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_CF if sys.platform == "win32" else 0)
        return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None
    except Exception:
        return None


def _ts(s):
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def parse(xml_text):
    """-> [(ts, event_id, provider, [data...])]"""
    out = []
    if not xml_text.strip():
        return out
    try:
        root = ET.fromstring("<r>" + xml_text + "</r>")
    except ET.ParseError:
        return out
    for ev in root.iter(NS + "Event"):
        s = ev.find(NS + "System")
        if s is None:
            continue
        try:
            eid = int((s.find(NS + "EventID").text or "0").strip())
            prov = s.find(NS + "Provider").get("Name") or ""
            ts = _ts(s.find(NS + "TimeCreated").get("SystemTime") or "")
        except Exception:
            continue
        if ts is None:
            continue
        out.append((ts, eid, prov, [(d.text or "") for d in ev.iter(NS + "Data")]))
    return out


def classify(eid, prov, data):
    p = prov.lower()
    if eid == 41 and "kernel-power" in p:
        return "unexpected", "Kernel-Power 41"
    if eid == 6008 and "eventlog" in p:
        return "unexpected", "EventLog 6008"
    if eid == 1001 and "systemerrorreporting" in p:
        code = (data[0] if data else "")[:40]
        return "bsod", ("رمز التوقف " + code) if code else "BugCheck"
    if eid in (7, 11, 51, 153, 129, 157) and any(x in p for x in DISK_PROV):
        return "disk", "%s %d" % (prov, eid)
    if "whea" in p and eid in (17, 18, 19, 20, 1):
        return "hardware", "WHEA %d" % eid
    if eid == 4101 and p == "display":
        return "tdr", "Display 4101"
    if eid == 1002 and "application hang" in p:
        return "hang", (data[0] if data else "")
    if eid == 1000 and "application error" in p:
        return "crash", (data[0] if data else "")
    return None, None


def _ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS rel_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, kind TEXT NOT NULL, code INTEGER, "
              "src TEXT, app TEXT, extra TEXT, msg TEXT, UNIQUE(ts,kind,code,src))")
    c.execute("CREATE INDEX IF NOT EXISTS i_rel_ts ON rel_events(ts)")


def _log_start(run):
    """Oldest record still in the System log (wevtutil reads oldest first without /rd)."""
    rows = parse(run(["wevtutil", "qe", "System", "/c:1", "/f:xml"], 30) or "")
    return rows[0][0] if rows else None


def _window(last, now):
    """Seconds to read back: everything since the last scan plus OVERLAP, never more than DAYS (first scan, or a clock that went back)."""
    if not last or last > now:
        return DAYS * 86400
    return max(OVERLAP, min(DAYS * 86400, now - int(last) + OVERLAP))


def scan(db, run=None):
    """Read what is new in the event logs into rel_events. -> number of new rows.
    A row is new when no stored row has the same (ts, event id, provider): rows written by the old relia.py used other kind names."""
    run = run or _run
    now = int(time.time())
    prev = db.get_json(META) or {}
    window = _window(prev.get("ts"), now)
    failed, live_from, rows = [], _log_start(run), []
    for log, providers, ids in QUERIES:
        text = run(_q(log, providers, ids, window), 60)
        if text is None:
            failed.append("%s:%s" % (log, providers[0]))
            continue
        got = parse(text)
        if len(got) >= CAP:                        # newest first: this query really starts at its oldest row
            oldest = min(r[0] for r in got)
            live_from = oldest if live_from is None else max(live_from, oldest)
        rows.extend(got)
    n = 0
    with db._lock:
        c = db.conn()
        _ensure(c)
        for ts, eid, prov, data in rows:
            k, _d = classify(eid, prov, data)
            if not k:
                continue
            d0 = (data[0] if data else "")[:80]
            cur = c.execute("INSERT OR IGNORE INTO rel_events(ts,kind,code,src,app,extra,msg) SELECT ?,?,?,?,?,?,'' "
                            "WHERE NOT EXISTS (SELECT 1 FROM rel_events WHERE ts=? AND code=? AND src=?)",
                            (ts, k, eid, prov[:80], d0 if k in ("hang", "crash") else "", "" if k in ("hang", "crash") else d0, ts, eid, prov[:80]))
            n += cur.rowcount
        c.commit()
    if failed and len(failed) == len(QUERIES):
        db.set_json(META, dict(prev, failed=failed, failedAt=now))    # keep the last good scan time: the next one reads the gap again
    else:
        db.set_json(META, {"ts": now, "failed": failed, "liveFrom": live_from})
    with _lock:
        _cache["at"] = 0.0                         # the panel shows the new rows on its next request
    return n


def job():
    import db
    n = scan(db)
    st = db.get_json(META) or {}
    return "%d new reliability events%s" % (n, " (failed: %s)" % ", ".join(st["failed"]) if st.get("failed") else "")


def _history(db, since):
    """-> ([(ts, event_id, provider, [data])], earliest ts ever stored or None)"""
    with db._lock:
        c = db.conn()
        _ensure(c)
        first = c.execute("SELECT MIN(ts) FROM rel_events").fetchone()[0]
        rows = c.execute("SELECT ts, code, src, app, extra FROM rel_events WHERE ts>=?", (since,)).fetchall()
    return [(int(r[0]), int(r[1] or 0), r[2] or "", [r[4] or r[3] or ""]) for r in rows], first


def collect(db):
    """The panel, from rel_events (kept by scan) and io_stalls. Never touches the event log."""
    now = int(time.time())
    since = now - DAYS * 86400
    st = db.get_json(META) or {}
    hist, hist_first = _history(db, since)
    if not st.get("ts") and not hist:
        return {"ok": False, "pending": True, "at": now} if not st.get("failed") else \
            {"ok": False, "error": "wevtutil failed for every query", "at": now}
    failed = st.get("failed") or []
    events = []
    for ts, eid, prov, data in hist:
        k, d = classify(eid, prov, data)
        if k:
            events.append({"ts": ts, "k": k, "d": d})
    starts = [t for t in (hist_first, None if failed else st.get("liveFrom")) if t]
    covered_from = min(starts) if starts else None
    short = covered_from is not None and covered_from > since + 2 * 86400     # two days of slack before saying so
    try:
        with db._lock:
            for r in db.conn().execute("SELECT ts_start,note FROM io_stalls WHERE ts_start>=? AND note LIKE 'monitor thread frozen%'", (since,)):
                events.append({"ts": r[0], "k": "freeze", "d": r[1]})
    except Exception:
        pass
    # merge duplicates: same kind within 10 minutes (41 + 6008 + 1001 describe one incident)
    events.sort(key=lambda e: e["ts"])
    incidents, lastk, lasti = [], {}, {}
    for e in events:
        key = e["k"] if e["k"] not in ("bsod", "unexpected") else "crashy"
        if key in ("crashy", "freeze", "hardware") and key in lastk and e["ts"] - lastk[key] < 600:
            if e["k"] == "bsod":       # keep the more specific record — of this incident, not whatever came last
                incidents[lasti[key]].update(e)
            lastk[key] = e["ts"]
            continue
        lastk[key] = e["ts"]
        lasti[key] = len(incidents)
        incidents.append(dict(e))
    counts = {k: {"d7": 0, "d30": 0, "last": None} for k in KINDS}
    apps = {}
    for e in incidents:
        c = counts[e["k"]]
        c["d30"] += 1
        if e["ts"] >= now - 7 * 86400:
            c["d7"] += 1
        c["last"] = max(c["last"] or 0, e["ts"])
        if e["k"] in ("hang", "crash") and e["d"]:
            apps[e["d"]] = apps.get(e["d"], 0) + 1
    recent = [e for e in incidents if KINDS[e["k"]][1] >= 2][-12:][::-1]
    top_apps = sorted(apps.items(), key=lambda kv: -kv[1])[:5]
    serious7 = sum(counts[k]["d7"] for k, v in KINDS.items() if v[1] >= 3)
    serious30 = sum(counts[k]["d30"] for k, v in KINDS.items() if v[1] >= 3)
    last_serious = max([counts[k]["last"] or 0 for k, v in KINDS.items() if v[1] >= 3] + [0]) or None
    return {"ok": True, "at": int(st.get("ts") or now), "days": DAYS, "counts": counts, "recent": recent,
            "topApps": [{"name": a, "n": n} for a, n in top_apps],
            "serious7": serious7, "serious30": serious30, "lastSerious": last_serious,
            "partial": bool(failed or short), "coveredFrom": covered_from if short else None, "failed": failed,
            "labels": {k: v[0] for k, v in KINDS.items()}}


def snapshot(db):
    """GET /api/reliab: a database read, reused for TTL seconds."""
    with _lock:
        if _cache["data"] is not None and time.time() - _cache["at"] < TTL:
            return _cache["data"]
    d = collect(db)
    with _lock:
        _cache["data"], _cache["at"] = d, time.time()
    return d


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import sqlite3
        sample = ('<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="Microsoft-Windows-Kernel-Power" Guid="{x}"/><EventID Qualifiers="0">41</EventID>'
                  '<TimeCreated SystemTime="2026-10-20T10:00:00.123456700Z"/></System><EventData><Data Name="BugcheckCode">0</Data></EventData></Event>'
                  '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="EventLog"/><EventID>6008</EventID><TimeCreated SystemTime="2026-10-20T10:03:00.0000000Z"/></System><EventData><Data>x</Data></EventData></Event>'
                  '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="disk"/><EventID>153</EventID><TimeCreated SystemTime="2026-10-21T10:03:00.0000000Z"/></System></Event>'
                  '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="Application Hang"/><EventID>1002</EventID><TimeCreated SystemTime="2026-10-21T11:03:00.0000000Z"/></System><EventData><Data>chrome.exe</Data></EventData></Event>')
        ev = parse(sample)
        assert len(ev) == 4, ev
        ks = [classify(e, p, d)[0] for _, e, p, d in ev]
        assert ks == ["unexpected", "unexpected", "disk", "hang"], ks
        assert parse("") == [] and parse("<garbage") == []
        q = " ".join(_q("System", ("EventLog",), (6008,)))
        assert "Provider[@Name='EventLog']" in q and "/c:%d" % CAP in q and "<= %d]" % (DAYS * 86400 * 1000) in q, q
        now = int(time.time())
        assert _window(None, now) == DAYS * 86400 and _window(now + 99, now) == DAYS * 86400, "first scan / clock went back: full window"
        assert _window(now - 1800, now) == 1800 + OVERLAP and _window(now - 90 * 86400, now) == DAYS * 86400

        class _DB:                                     # monitor.db stand-in: meta + rel_events + io_stalls
            def __init__(self):
                self._lock = threading.RLock()
                self.c = sqlite3.connect(":memory:", check_same_thread=False)
                self.c.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
                self.c.execute("CREATE TABLE io_stalls(ts_start INT, note TEXT)")
                _ensure(self.c)

            def conn(self):
                return self.c

            def get_json(self, k, default=None):
                r = self.c.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
                return json.loads(r[0]) if r else default

            def set_json(self, k, v):
                self.c.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))

        db = _DB()
        assert collect(db) == {"ok": False, "pending": True, "at": collect(db)["at"]}, "nothing scanned yet: pending, not «no incidents»"
        assert scan(db, run=lambda cmd, t: None) == 0
        assert collect(db)["ok"] is False and "error" in collect(db), "wevtutil down and no history: an error, not «no incidents»"
        # history written by the old relia.py (other kind names, msg column) beyond the live log's reach
        old = now - 25 * 86400
        db.c.executemany("INSERT INTO rel_events(ts,kind,code,src,app,extra,msg) VALUES(?,?,?,?,?,?,'')", [
            (old, "crash", 41, "Microsoft-Windows-Kernel-Power", "", "0x0"), (old + 14, "unexpected", 6008, "EventLog", "", ""),
            (old + 14, "bsod", 1001, "Microsoft-Windows-WER-SystemErrorReporting", "", "0x9f"),
            (now - 40 * 86400, "crash", 41, "Microsoft-Windows-Kernel-Power", "", "")])
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S.0000000Z", time.gmtime(t))
        bsod = ('<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="Microsoft-Windows-WER-SystemErrorReporting"/>'
                '<EventID>1001</EventID><TimeCreated SystemTime="%s"/></System><EventData><Data>0x0000009f</Data></EventData></Event>')
        crash = ('<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="Application Error"/>'
                 '<EventID>1000</EventID><TimeCreated SystemTime="%s"/></System><EventData><Data>x.exe</Data></EventData></Event>')
        kp = sample.split("</Event>")[0] + "</Event>"
        base = now - 86400
        texts = {"Kernel-Power": kp.replace("2026-10-20T10:00:00.123456700Z", iso(base)) + kp.replace("2026-10-20T10:00:00.123456700Z", iso(old)),
                 "WER-SystemErrorReporting": bsod % iso(base + 120), "Application Error": crash % iso(base + 60)}
        seen = []

        def fake(cmd, t):
            seen.append(" ".join(cmd))
            if "/c:1" in cmd:
                return kp.replace("2026-10-20T10:00:00.123456700Z", iso(now - 13 * 86400))
            return next((v for k, v in texts.items() if k in " ".join(cmd)), "")
        assert scan(db, run=fake) == 3, "the 41 already stored by relia.py (other kind name) is not stored twice"
        assert scan(db, run=fake) == 0, "a second scan adds nothing"
        assert "<= %d]" % (OVERLAP * 1000 + 1000) in seen[-1] or "<= %d]" % (OVERLAP * 1000) in seen[-1], seen[-1]
        db.c.execute("INSERT INTO io_stalls VALUES(?, 'monitor thread frozen 40 s')", (now - 3600,))
        _cache["at"] = 0.0
        r = snapshot(db)
        assert r["ok"] and not r["partial"] and r["failed"] == [], r
        assert r["counts"]["bsod"]["d30"] == 2 and r["counts"]["unexpected"]["d30"] == 0, r["counts"]   # 41+6008+1001 = one incident, twice
        assert r["counts"]["crash"]["d30"] == 1 and r["counts"]["freeze"]["d30"] == 1 and r["topApps"] == [{"name": "x.exe", "n": 1}], r
        assert snapshot(db) is r, "reused within TTL"
        # one query failing: data still shown, flagged partial
        assert scan(db, run=lambda cmd, t: None if "Display" in " ".join(cmd) else fake(cmd, t)) == 0
        _cache["at"] = 0.0
        r = snapshot(db)
        assert r["ok"] and r["partial"] and r["failed"] == ["System:Display"], r
        print("selftest ok")
    elif "--scan" in sys.argv:
        import os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import db
        t0 = time.time()
        print(job(), "in %.1f s" % (time.time() - t0))
        print(json.dumps(collect(db), ensure_ascii=False, indent=1)[:2500])
