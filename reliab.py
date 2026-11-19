# -*- coding: utf-8 -*-
"""Reliability history from the Windows event logs (read-only: wevtutil qe) plus hang records from the monitor DB.
Used by server.py: GET /api/reliab  -> cached result, refreshed in the background (the call itself never blocks on wevtutil).
CLI: python reliab.py --probe   (runs once, prints a summary)"""
import calendar, json, subprocess, sys, threading, time
import xml.etree.ElementTree as ET

NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
DAYS = 30
TTL = 1800                 # seconds between refreshes
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

_lock = threading.Lock()
_cache = {"data": None, "at": 0, "running": False}


def _q(log, providers, ids):
    prov = " or ".join("@Name='%s'" % p for p in providers)
    cond = " or ".join("EventID=%d" % i for i in ids)
    return ["wevtutil", "qe", log, "/q:*[System[Provider[%s] and (%s) and TimeCreated[timediff(@SystemTime) <= %d]]]" % (prov, cond, DAYS * 86400 * 1000),
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


def _history(db, since):
    """rel_events (relia.py, hourly) keeps what the event log has already overwritten: on this PC the System
    log reaches back about 13 days (19/11/2026), so the log alone can never fill a 30-day window.
    -> ([(ts, event_id, provider, [data])], earliest ts ever stored or None)"""
    if db is None:
        return [], None
    try:
        with db._lock:
            c = db.conn()
            first = c.execute("SELECT MIN(ts) FROM rel_events").fetchone()[0]
            rows = c.execute("SELECT ts, code, src, app, extra FROM rel_events WHERE ts>=?", (since,)).fetchall()
    except Exception:
        return [], None
    return [(int(r[0]), int(r[1] or 0), r[2] or "", [r[4] or r[3] or ""]) for r in rows], first


def _log_start(run):
    """Oldest record still in the System log (wevtutil reads oldest first without /rd)."""
    rows = parse(run(["wevtutil", "qe", "System", "/c:1", "/f:xml"], 30) or "")
    return rows[0][0] if rows else None


def collect(db=None, run=None):
    run = run or _run
    now = int(time.time())
    since = now - DAYS * 86400
    hist, hist_first = _history(db, since)
    seen = {(ts, eid, prov.lower()) for ts, eid, prov, _ in hist}
    events, failed, live_from = [], [], _log_start(run)
    for ts, eid, prov, data in hist:
        k, d = classify(eid, prov, data)
        if k:
            events.append({"ts": ts, "k": k, "d": d})
    for log, providers, ids in QUERIES:
        text = run(_q(log, providers, ids), 90)
        if text is None:
            failed.append("%s:%s" % (log, providers[0]))
            continue
        rows = parse(text)
        if len(rows) >= CAP:                       # newest first: this query really starts at its oldest row
            oldest = min(r[0] for r in rows)
            live_from = oldest if live_from is None else max(live_from, oldest)
        for ts, eid, prov, data in rows:
            if (ts, eid, prov.lower()) in seen:
                continue
            k, d = classify(eid, prov, data)
            if k:
                events.append({"ts": ts, "k": k, "d": d})
    if len(failed) == len(QUERIES) and not hist:
        return {"ok": False, "error": "wevtutil failed for every query", "at": now}
    starts = [t for t in (hist_first, None if failed else live_from) if t]
    covered_from = min(starts) if starts else None
    short = covered_from is not None and covered_from > since + 2 * 86400     # two days of slack before saying so
    if db is not None:
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
    return {"ok": True, "at": now, "days": DAYS, "counts": counts, "recent": recent, "topApps": [{"name": a, "n": n} for a, n in top_apps],
            "serious7": serious7, "serious30": serious30, "lastSerious": last_serious,
            "partial": bool(failed or short), "coveredFrom": covered_from if short else None, "failed": failed,
            "labels": {k: v[0] for k, v in KINDS.items()}}


def _worker(db):
    try:
        d = collect(db)
    except Exception:
        d = None
    with _lock:
        # A failed scan keeps the last good result (retried after TTL); it is shown only when nothing better exists.
        if d and (d.get("ok") or not (_cache["data"] or {}).get("ok")):
            _cache["data"] = d
        _cache["at"] = time.time()
        _cache["running"] = False


def snapshot(db=None):
    """Never blocks: returns the cached result (or {'ok': False, 'pending': True}) and refreshes it in the background when stale."""
    with _lock:
        stale = time.time() - _cache["at"] > TTL
        if stale and not _cache["running"]:
            _cache["running"] = True
            threading.Thread(target=_worker, args=(db,), daemon=True).start()
        d = _cache["data"]
    return d if d else {"ok": False, "pending": True}


if __name__ == "__main__":
    if "--selftest" in sys.argv:
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
        assert "Provider[@Name='EventLog']" in q and "/c:%d" % CAP in q, q
        assert collect(run=lambda cmd, t: None)["ok"] is False          # wevtutil down: not «no incidents»
        def fake(cmd, t):
            return sample if "Kernel-Power" in " ".join(cmd) else ""
        class _DB:                                     # history from rel_events beyond the log's reach
            _lock = threading.Lock()
            def __init__(self):
                import sqlite3
                self.c = sqlite3.connect(":memory:")
                self.c.execute("CREATE TABLE rel_events(ts INT, kind TEXT, code INT, src TEXT, app TEXT, extra TEXT)")
                self.c.execute("CREATE TABLE io_stalls(ts_start INT, note TEXT)")
                old = int(time.time()) - 25 * 86400
                self.c.executemany("INSERT INTO rel_events VALUES(?,?,?,?,?,?)", [
                    (old, "crash", 41, "Microsoft-Windows-Kernel-Power", "", ""), (old + 14, "unexpected", 6008, "EventLog", "", ""),
                    (old + 14, "bsod", 1001, "Microsoft-Windows-WER-SystemErrorReporting", "", "0x9f"),
                    (int(time.time()) - 40 * 86400, "crash", 41, "Microsoft-Windows-Kernel-Power", "", "")])
            def conn(self):
                return self.c
        r = collect(_DB(), run=lambda cmd, t: "")
        assert r["counts"]["bsod"]["d30"] == 1 and r["counts"]["unexpected"]["d30"] == 0 and not r["partial"], (r["counts"], r["partial"])
        r = collect(None, run=lambda cmd, t: sample if "/c:1" in cmd else "")      # log alone, starting 20/10/2026
        assert r["partial"] == (r["coveredFrom"] is not None)
        r = collect(run=fake)
        assert r["ok"] and r["counts"]["unexpected"]["d30"] == 1 and not r["partial"], r["counts"]["unexpected"]
        bsod = ('<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="Microsoft-Windows-WER-SystemErrorReporting"/>'
                '<EventID>1001</EventID><TimeCreated SystemTime="%s"/></System><EventData><Data>0x0000009f</Data></EventData></Event>')
        crash = ('<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><Provider Name="Application Error"/>'
                 '<EventID>1000</EventID><TimeCreated SystemTime="%s"/></System><EventData><Data>x.exe</Data></EventData></Event>')
        base = time.time() - 86400
        iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%S.0000000Z", time.gmtime(base + s))
        texts = {"Kernel-Power": sample.split("</Event>")[0].replace("2026-10-20T10:00:00.123456700Z", iso(0)) + "</Event>",
                 "WER-SystemErrorReporting": bsod % iso(120), "Application Error": crash % iso(60)}
        def fake2(cmd, t):
            return next((v for k, v in texts.items() if k in " ".join(cmd)), "")
        r = collect(run=fake2)
        assert r["counts"]["bsod"]["d30"] == 1 and r["counts"]["crash"]["d30"] == 1 and r["counts"]["unexpected"]["d30"] == 0, r["counts"]
        print("selftest ok")
    else:
        t0 = time.time()
        d = collect()
        print(json.dumps(d, ensure_ascii=False, indent=1)[:2500])
        print("seconds: %.1f" % (time.time() - t0))
