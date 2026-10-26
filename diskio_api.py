# -*- coding: utf-8 -*-
"""Disk I/O dashboard payload (read-only SQL over the io_* tables). Used by server.py: GET /api/io?range=24h&dev=disk0"""
import sqlite3, time

RANGES = {  # seconds back, bucket seconds (0 = raw 10 s samples)
    "1h": (3600, 0), "6h": (6 * 3600, 60), "24h": (86400, 60),
    "7d": (7 * 86400, 600), "30d": (30 * 86400, 1800),
}


def _rows(c, sql, args=()):
    try:
        return [dict(r) for r in c.execute(sql, args)]
    except sqlite3.Error:
        import errlog
        errlog.swallowed()
        return []


def _cfg(c):
    d = {}
    for r in _rows(c, "SELECT key,value FROM io_cfg"):
        try:
            d[r["key"]] = float(r["value"])
        except (TypeError, ValueError):
            pass
    return d


def _r1(v, n=2):
    return None if v is None else round(v, n)


def payload(db, rng="24h", dev="disk0"):
    rng = rng if rng in RANGES else "24h"
    span, bucket = RANGES[rng]
    now = int(time.time())
    t0 = now - span
    with db.ro() as c:   # own read-only connection: heavy aggregations never hold the global write lock
        cfg = _cfg(c)
        devices = [r["dev"] for r in _rows(c, "SELECT DISTINCT dev FROM io_samples WHERE ts > ? ORDER BY dev", (now - 86400,))]
        if dev not in devices and devices:
            dev = "disk0" if "disk0" in devices else devices[0]
        cur = _rows(c, "SELECT * FROM v_io_now")
        # ---- series
        if bucket == 0:
            raw = _rows(c, "SELECT ts,r_bps,w_bps,lat_r_ms,lat_w_ms,q_avg,busy_pct,r_size_kb,w_size_kb FROM io_samples WHERE dev=? AND ts>=? ORDER BY ts", (dev, t0))
            series = [{"t": r["ts"], "r": _r1((r["r_bps"] or 0) / 1048576, 3), "w": _r1((r["w_bps"] or 0) / 1048576, 3),
                       "lr": _r1(r["lat_r_ms"], 2), "lw": _r1(r["lat_w_ms"], 2), "q": _r1(r["q_avg"], 2), "b": _r1(r["busy_pct"], 1),
                       "rk": _r1(r["r_size_kb"], 1), "wk": _r1(r["w_size_kb"], 1)} for r in raw]
        else:
            if bucket == 60:
                sql = ("SELECT ts, r_mb, w_mb, r_bps_max, w_bps_max, lat_r_avg, lat_w_avg, lat_r_max, lat_w_max, q_max, busy_avg, busy_max "
                       "FROM io_min WHERE dev=? AND ts>=? ORDER BY ts")
                raw = _rows(c, sql, (dev, t0))
                span_s = 60.0
            else:
                sql = ("SELECT (ts/%d)*%d AS ts, SUM(r_mb) r_mb, SUM(w_mb) w_mb, MAX(r_bps_max) r_bps_max, MAX(w_bps_max) w_bps_max, "
                       "AVG(lat_r_avg) lat_r_avg, AVG(lat_w_avg) lat_w_avg, MAX(lat_r_max) lat_r_max, MAX(lat_w_max) lat_w_max, "
                       "MAX(q_max) q_max, AVG(busy_avg) busy_avg, MAX(busy_max) busy_max "
                       "FROM io_min WHERE dev=? AND ts>=? GROUP BY ts/%d ORDER BY ts") % (bucket, bucket, bucket)
                raw = _rows(c, sql, (dev, t0))
                span_s = float(bucket)
            series = [{"t": r["ts"], "r": _r1((r["r_mb"] or 0) / span_s, 3), "w": _r1((r["w_mb"] or 0) / span_s, 3),
                       "rp": _r1((r["r_bps_max"] or 0) / 1048576, 2), "wp": _r1((r["w_bps_max"] or 0) / 1048576, 2),
                       "lr": _r1(r["lat_r_avg"], 2), "lw": _r1(r["lat_w_avg"], 2), "lrm": _r1(r["lat_r_max"], 2), "lwm": _r1(r["lat_w_max"], 2),
                       "q": _r1(r["q_max"], 2), "b": _r1(r["busy_avg"], 1), "bm": _r1(r["busy_max"], 1)} for r in raw]
        # ---- free space beside written volume (per minute, bucketed like the series)
        free = _rows(c, "SELECT (ts/%d)*%d AS t, MIN(disk_free_gb) f FROM samples WHERE ts>=? AND disk_free_gb IS NOT NULL GROUP BY ts/%d ORDER BY t" %
                     ((bucket or 60,) * 3), (t0,))
        for p in free:
            p["f"] = _r1(p["f"], 2)
        # ---- heat map: busy by day x hour (last 14 days, from hourly rows; if none yet, from minute rows)
        heat = _rows(c, "SELECT day,hour,busy_avg,busy_max,w_gb,r_gb,lat_w_max FROM v_io_heat WHERE dev=? AND day>=date('now','-14 day','localtime') ORDER BY day,hour", (dev,))
        if not heat:
            heat = _rows(c, "SELECT date(ts,'unixepoch','localtime') day, CAST(strftime('%H',ts,'unixepoch','localtime') AS INTEGER) hour, "
                            "ROUND(AVG(busy_avg),1) busy_avg, ROUND(MAX(busy_max),1) busy_max, ROUND(SUM(w_mb)/1024.0,3) w_gb, ROUND(SUM(r_mb)/1024.0,3) r_gb, "
                            "ROUND(MAX(lat_w_max),1) lat_w_max FROM io_min WHERE dev=? AND ts>=? GROUP BY day,hour ORDER BY day,hour", (dev, now - 14 * 86400))
        # ---- processes
        ptab = "io_procs" if span <= 3 * 86400 else "io_procs_h"
        top = _rows(c, "SELECT name, ROUND(SUM(w_mb),1) w_mb, ROUND(SUM(r_mb),1) r_mb, CAST(SUM(w_ops) AS INTEGER) w_ops, CAST(SUM(r_ops) AS INTEGER) r_ops "
                       "FROM %s WHERE ts>=? GROUP BY name ORDER BY (SUM(w_mb)+SUM(r_mb)) DESC LIMIT 12" % ptab, (t0,))
        if not top and ptab == "io_procs_h":
            top = _rows(c, "SELECT name, ROUND(SUM(w_mb),1) w_mb, ROUND(SUM(r_mb),1) r_mb, CAST(SUM(w_ops) AS INTEGER) w_ops, CAST(SUM(r_ops) AS INTEGER) r_ops "
                           "FROM io_procs WHERE ts>=? GROUP BY name ORDER BY (SUM(w_mb)+SUM(r_mb)) DESC LIMIT 12", (t0,))
        # ---- daily totals (30 d)
        daily = _rows(c, "SELECT day, ROUND(r_gb,2) r_gb, ROUND(w_gb,2) w_gb, ROUND(busy_avg,1) busy_avg, ROUND(busy_max,1) busy_max, stalls "
                         "FROM io_day WHERE dev=? AND day>=date('now','-30 day','localtime') ORDER BY day", (dev,))
        if not daily:
            daily = _rows(c, "SELECT date(ts,'unixepoch','localtime') day, ROUND(SUM(r_mb)/1024.0,2) r_gb, ROUND(SUM(w_mb)/1024.0,2) w_gb, "
                             "ROUND(AVG(busy_avg),1) busy_avg, ROUND(MAX(busy_max),1) busy_max, 0 stalls FROM io_min WHERE dev=? AND ts>=? GROUP BY day ORDER BY day",
                          (dev, now - 30 * 86400))
        # ---- markers: stalls + memory trims
        stalls = _rows(c, "SELECT id,ts_start,ts_end,dev,ROUND(peak_lat_ms,0) peak_lat_ms,ROUND(peak_q,1) peak_q,ROUND(busy_avg,0) busy_avg,"
                          "ROUND(r_mb,0) r_mb,ROUND(w_mb,0) w_mb,top_json,ROUND(free_gb,1) free_gb,ROUND(ram_pct,0) ram_pct,note "
                          "FROM io_stalls WHERE ts_start>=? ORDER BY ts_start DESC LIMIT 30", (t0,))
        trims = _rows(c, "SELECT ts,before_pct,after_pct,freed_mb,ms,mode FROM trims WHERE ts>=? ORDER BY ts", (t0,))
        health = _rows(c, "SELECT * FROM io_health ORDER BY ts DESC LIMIT 1")
        n_samples = (_rows(c, "SELECT COUNT(*) n FROM io_samples") or [{"n": 0}])[0]["n"]
        first = (_rows(c, "SELECT MIN(ts) t FROM io_samples") or [{"t": None}])[0]["t"]
    # ---- summary over the series
    tot_w = sum((p["w"] or 0) * (bucket or 10) for p in series)
    tot_r = sum((p["r"] or 0) * (bucket or 10) for p in series)
    lw = [p["lw"] for p in series if p.get("lw") is not None]
    bs = [p["b"] for p in series if p.get("b") is not None]
    summ = {"w_mb": round(tot_w, 1), "r_mb": round(tot_r, 1),
            "lat_w_avg": _r1(sum(lw) / len(lw)) if lw else None,
            "lat_w_max": max([p.get("lwm") or p.get("lw") or 0 for p in series] or [0]),
            "busy_avg": _r1(sum(bs) / len(bs), 1) if bs else None,
            "busy_max": max([p.get("bm") or p.get("b") or 0 for p in series] or [0]),
            "stalls": len(stalls)}
    return {"ok": True, "now": now, "range": rng, "dev": dev, "devices": devices, "bucket": bucket or 10, "cfg": cfg,
            "cur": cur, "series": series, "free": free, "heat": heat, "top": top, "daily": daily, "stalls": stalls,
            "trims": trims, "health": health[0] if health else None, "summary": summ,
            "collected": {"samples": n_samples, "since": first}}
