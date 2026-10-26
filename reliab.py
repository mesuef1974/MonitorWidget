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

_lock = threading.Lock()
_cache = {"data": None, "at": 0, "running": False}


def _q(log, ids):
    cond = " or ".join("EventID=%d" % i for i in ids)
    return ["wevtutil", "qe", log, "/q:*[System[(%s) and TimeCreated[timediff(@SystemTime) <= %d]]]" % (cond, DAYS * 86400 * 1000),
            "/f:xml", "/c:400", "/rd:true"]


def _run(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_CF if sys.platform == "win32" else 0)
        return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else ""
    except Exception:
        return ""


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


def collect(db=None):
    now = int(time.time())
    events = []
    sysids = [41, 6008, 1001, 7, 11, 51, 153, 129, 157, 4101, 17, 18, 19, 20]
    for ts, eid, prov, data in parse(_run(_q("System", sysids), 90)) + parse(_run(_q("Application", [1000, 1002]), 90)):
        k, d = classify(eid, prov, data)
        if k:
            events.append({"ts": ts, "k": k, "d": d})
    if db is not None:
        try:
            with db._lock:
                for r in db.conn().execute("SELECT ts_start,note FROM io_stalls WHERE ts_start>=? AND note LIKE 'monitor thread frozen%'", (now - DAYS * 86400,)):
                    events.append({"ts": r[0], "k": "freeze", "d": r[1]})
        except Exception:
            pass
    # merge duplicates: same kind within 10 minutes (41 + 6008 + 1001 describe one incident)
    events.sort(key=lambda e: e["ts"])
    incidents, lastk = [], {}
    for e in events:
        key = e["k"] if e["k"] not in ("bsod", "unexpected") else "crashy"
        if key in ("crashy", "freeze", "hardware") and key in lastk and e["ts"] - lastk[key] < 600:
            if e["k"] == "bsod":       # keep the more specific record
                incidents[-1].update(e)
            lastk[key] = e["ts"]
            continue
        lastk[key] = e["ts"]
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
            "labels": {k: v[0] for k, v in KINDS.items()}}


def _worker(db):
    try:
        d = collect(db)
    except Exception:
        d = None
    with _lock:
        if d:
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
        print("selftest ok")
    else:
        t0 = time.time()
        d = collect()
        print(json.dumps(d, ensure_ascii=False, indent=1)[:2500])
        print("seconds: %.1f" % (time.time() - t0))
