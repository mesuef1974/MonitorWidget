# -*- coding: utf-8 -*-
"""Disk I/O collector + stall detector (stdlib + ctypes, no subprocess, no admin).

Reads cumulative IOCTL_DISK_PERFORMANCE counters of every physical drive and fixed volume once per second,
turns deltas into rates/latency/queue/busy, aggregates 10 s rows into io_samples, per-process I/O (GetProcessIoCounters)
into io_procs once a minute, and records a stall (io_stalls + an event) when latency stays above the limit.
It also records a stall when this thread itself was frozen (the whole machine hung).

Run:  python diskio_collect.py --probe      5 s of live readings on this machine (verifies the Windows part)
      python diskio_collect.py --selftest   logic test with synthetic readings (any OS)"""
import collections, ctypes, json, os, sys, threading, time

IOCTL_DISK_PERFORMANCE = 0x70020
T100NS = 1e7
WIN = os.name == "nt"


# ---------------------------------------------------------------- Windows adapters
if WIN:
    from ctypes import wintypes as wt

    class DISK_PERFORMANCE(ctypes.Structure):
        _fields_ = [("BytesRead", ctypes.c_longlong), ("BytesWritten", ctypes.c_longlong),
                    ("ReadTime", ctypes.c_longlong), ("WriteTime", ctypes.c_longlong), ("IdleTime", ctypes.c_longlong),
                    ("ReadCount", wt.DWORD), ("WriteCount", wt.DWORD), ("QueueDepth", wt.DWORD), ("SplitCount", wt.DWORD),
                    ("QueryTime", ctypes.c_longlong), ("StorageDeviceNumber", wt.DWORD), ("StorageManagerName", ctypes.c_wchar * 8)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wt.HANDLE
    _k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
    _k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.OpenProcess.restype = wt.HANDLE
    _k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    _k32.GetProcessIoCounters.argtypes = [wt.HANDLE, ctypes.POINTER(IO_COUNTERS)]
    _k32.QueryUnbiasedInterruptTime.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    _INVALID = wt.HANDLE(-1).value


def mono_now():
    """Seconds of awake time (excludes sleep/hibernate): a gap here means this thread was starved, not that the PC slept."""
    v = ctypes.c_ulonglong(0)
    _k32.QueryUnbiasedInterruptTime(ctypes.byref(v))
    return v.value / T100NS


class WinSource:
    """Opens each drive once; read() returns {dev: (bytes_r, bytes_w, time_r, time_w, idle, ops_r, ops_w, qdepth, split, qtime)}."""

    def __init__(self):
        self.handles = {}
        for n in range(16):
            self._open("disk%d" % n, r"\\.\PhysicalDrive%d" % n)
        mask = _k32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                letter = chr(65 + i)
                if _k32.GetDriveTypeW(letter + ":\\") == 3:      # DRIVE_FIXED
                    self._open(letter + ":", r"\\.\%s:" % letter)

    def _open(self, dev, path):
        h = _k32.CreateFileW(path, 0, 3, None, 3, 0, None)
        if h is not None and h != _INVALID:
            self.handles[dev] = h

    def read(self):
        out = {}
        dp = DISK_PERFORMANCE(); ret = wt.DWORD(0)
        for dev, h in list(self.handles.items()):
            if _k32.DeviceIoControl(h, IOCTL_DISK_PERFORMANCE, None, 0, ctypes.byref(dp), ctypes.sizeof(dp), ctypes.byref(ret), None):
                out[dev] = (dp.BytesRead, dp.BytesWritten, dp.ReadTime, dp.WriteTime, dp.IdleTime,
                            dp.ReadCount, dp.WriteCount, dp.QueueDepth, dp.SplitCount, dp.QueryTime)
        return out

    def close(self):
        for h in self.handles.values():
            _k32.CloseHandle(h)
        self.handles = {}


def proc_io_snapshot():
    """{(pid, name): (read_bytes, write_bytes, read_ops, write_ops)} for every process we may open."""
    import procsnap
    snap = {}
    ioc = IO_COUNTERS()
    for pe in procsnap.processes():
        if pe.pid > 4:
            h = _k32.OpenProcess(0x1000, False, pe.pid)      # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                if _k32.GetProcessIoCounters(h, ctypes.byref(ioc)):
                    snap[(pe.pid, pe.name.lower())] = (ioc.ReadBytes, ioc.WriteBytes, ioc.ReadOps, ioc.WriteOps)
                _k32.CloseHandle(h)
    return snap


# ---------------------------------------------------------------- pure logic (testable anywhere)
MASK32 = 0xFFFFFFFF


def _d(cur, prev, mask=None):
    v = cur - prev
    return (v & mask) if mask else max(v, 0)


def delta(prev, cur):
    """Raw delta of two tuples from WinSource.read(); None if the interval is unusable."""
    qt = cur[9] - prev[9]
    if qt <= 0:
        return None
    return dict(dt=qt / T100NS, rb=_d(cur[0], prev[0]), wb=_d(cur[1], prev[1]), rt=_d(cur[2], prev[2]), wt=_d(cur[3], prev[3]),
                idle=_d(cur[4], prev[4]), ro=_d(cur[5], prev[5], MASK32), wo=_d(cur[6], prev[6], MASK32), q=cur[7], sp=_d(cur[8], prev[8], MASK32))


def zero():
    return dict(dt=0.0, rb=0, wb=0, rt=0, wt=0, idle=0, ro=0, wo=0, q=0, sp=0, qmax=0)


def add(acc, d):
    for k in ("dt", "rb", "wb", "rt", "wt", "idle", "ro", "wo", "sp"):
        acc[k] += d[k]
    acc["q"] = d["q"]; acc["qmax"] = max(acc["qmax"], d["q"])
    return acc


def metrics(a):
    """Rates and indicators from an accumulated delta window."""
    dt = a["dt"]
    if dt <= 0:
        return None
    busy = max(0.0, min(100.0, 100.0 * (1.0 - a["idle"] / (dt * T100NS))))
    return dict(dt=dt, r_bps=a["rb"] / dt, w_bps=a["wb"] / dt, r_iops=a["ro"] / dt, w_iops=a["wo"] / dt,
                lat_r_ms=(a["rt"] / 1e4 / a["ro"]) if a["ro"] else None, lat_w_ms=(a["wt"] / 1e4 / a["wo"]) if a["wo"] else None,
                r_size_kb=(a["rb"] / a["ro"] / 1024.0) if a["ro"] else None, w_size_kb=(a["wb"] / a["wo"] / 1024.0) if a["wo"] else None,
                q_avg=(a["rt"] + a["wt"]) / (dt * T100NS), q_cur=a["q"], busy_pct=busy, idle_pct=100.0 - busy, split_iops=a["sp"] / dt)


class Engine:
    """Consumes per-second readings; produces io_samples rows (every `window` s), stall records and per-process minutes."""

    def __init__(self, cfg, snap_fn=None, window=10):
        self.cfg = dict(cfg); self.snap_fn = snap_fn; self.window = window
        self.prev = {}; self.acc = {}; self.acc_t0 = None; self.last_mono = None
        self.stall = {}            # dev -> dict(state)
        self.bad = collections.Counter(); self.good = collections.Counter()
        self.rows = []; self.stalls = []; self.events = []

    # --- one second
    def tick(self, wall, mono, readings):
        gap = (mono - self.last_mono) if self.last_mono is not None else 0.0
        self.last_mono = mono
        freeze = gap > float(self.cfg.get("freeze_gap_s", 4))
        if self.acc_t0 is None or wall < self.acc_t0:
            self.acc_t0 = wall          # wall clock went back: restart the window instead of waiting for the old time
        total = zero(); seen = False; fz = zero()
        for dev, cur in readings.items():
            p = self.prev.get(dev)
            self.prev[dev] = cur
            if p is None:
                continue
            d = delta(p, cur)
            if d is None:
                continue
            if freeze:                       # one long interval covering the hang: keep it out of the 10 s rows, report it as a stall
                if dev.startswith("disk"):
                    add(fz, d); seen = True
                continue
            if d["dt"] > 5.0:                # sleep/resume: the counters kept running while we did not; skip the interval
                continue
            add(self.acc.setdefault(dev, zero()), d)
            if dev.startswith("disk"):
                add(total, d); seen = True
                self._stall_check(dev, wall, d)
        if freeze:
            self._freeze(wall, gap, fz if seen else None)
        elif seen:
            add(self.acc.setdefault("_total", zero()), total)
        if wall - self.acc_t0 >= self.window:
            self.flush(wall)

    def flush(self, wall):
        ts = int(wall)
        for dev, a in self.acc.items():
            m = metrics(a)
            if m:
                m.update(ts=ts, dev=dev); self.rows.append(m)
        self.acc = {}; self.acc_t0 = wall

    # --- stalls
    def _stall_check(self, dev, wall, d):
        m = metrics(dict(zero(), **d))
        if not m:
            return
        lat = max(m["lat_r_ms"] or 0.0, m["lat_w_ms"] or 0.0)
        st = self.stall.get(dev)
        limit = float(self.cfg["stall_lat_ms"])
        if lat > limit:
            self.bad[dev] += 1; self.good[dev] = 0
            if st is None:
                st = self.stall[dev] = dict(t0=wall, peak_lat=lat, peak_q=m["q_avg"], busy=[m["busy_pct"]], rb=d["rb"], wb=d["wb"],
                                            snap=self.snap_fn() if self.snap_fn else None, confirmed=False)
            else:
                st["peak_lat"] = max(st["peak_lat"], lat); st["peak_q"] = max(st["peak_q"], m["q_avg"])
                st["busy"].append(m["busy_pct"]); st["rb"] += d["rb"]; st["wb"] += d["wb"]
            if self.bad[dev] >= float(self.cfg["stall_min_s"]):
                st["confirmed"] = True
        else:
            self.good[dev] += 1
            if st is not None:
                if self.good[dev] >= 2:
                    self._end_stall(dev, wall, st)
                else:
                    st["rb"] += d["rb"]; st["wb"] += d["wb"]
            self.bad[dev] = 0

    def _end_stall(self, dev, wall, st, note=None):
        self.stall.pop(dev, None)
        if not st["confirmed"]:
            return
        top = self._top(st["snap"])
        self.stalls.append(dict(ts_start=int(st["t0"]), ts_end=int(wall), dev=dev, peak_lat_ms=round(st["peak_lat"], 1), peak_q=round(st["peak_q"], 2),
                                busy_avg=round(sum(st["busy"]) / len(st["busy"]), 1), r_mb=round(st["rb"] / 1048576.0, 1), w_mb=round(st["wb"] / 1048576.0, 1),
                                top=top, note=note or "latency above %s ms" % self.cfg["stall_lat_ms"]))

    def _top(self, snap0):
        if not (self.snap_fn and snap0):
            return []
        try:
            return top_procs(snap0, self.snap_fn(), 5)
        except Exception:
            return []

    def _freeze(self, wall, gap, fz=None):
        """This thread was not scheduled for `gap` s of awake time: the machine (or the disk) hung."""
        m = metrics(fz) if fz and fz["dt"] > 0 else None
        lat = max((m["lat_r_ms"] or 0.0), (m["lat_w_ms"] or 0.0)) if m else None
        self.stalls.append(dict(ts_start=int(wall - gap), ts_end=int(wall), dev=None, peak_lat_ms=round(lat, 1) if lat is not None else None,
                                peak_q=round(m["q_avg"], 2) if m else None, busy_avg=round(m["busy_pct"], 1) if m else None,
                                r_mb=round(fz["rb"] / 1048576.0, 1) if m else None, w_mb=round(fz["wb"] / 1048576.0, 1) if m else None,
                                top=[], note="monitor thread frozen %.0f s (system-wide hang)" % gap))

    def close_open(self, wall):
        for dev in list(self.stall):
            self._end_stall(dev, wall, self.stall[dev])


def top_procs(s0, s1, n=10):
    """Per image name deltas between two snapshots -> [{name, r_mb, w_mb, r_ops, w_ops, procs}] sorted by volume."""
    agg = {}
    for key, v1 in s1.items():
        v0 = s0.get(key, (0, 0, 0, 0)) if s0 is not None else v1       # a process new since s0 counts fully
        d = [max(a - b, 0) for a, b in zip(v1, v0)]
        if d[0] + d[1] == 0:
            continue
        name = os.path.basename(key[1]) or key[1]
        a = agg.setdefault(name, [0, 0, 0, 0, 0])
        for i in range(4):
            a[i] += d[i]
        a[4] += 1
    rows = [dict(name=k, r_mb=round(v[0] / 1048576.0, 2), w_mb=round(v[1] / 1048576.0, 2), r_ops=v[2], w_ops=v[3], procs=v[4]) for k, v in agg.items()]
    rows.sort(key=lambda r: r["r_mb"] + r["w_mb"], reverse=True)
    return rows[:n]


# ---------------------------------------------------------------- thread glue
class Collector(threading.Thread):
    def __init__(self, db_mod, diskio_mod):
        super().__init__(name="diskio", daemon=True)
        self.db = db_mod; self.io = diskio_mod; self.stop_ev = threading.Event()

    def _cfg(self):
        with self.db._lock:
            c = self.db.conn()
            cfg = {k: self.io.cfg(c, k) for k in self.io.DEFAULT_CFG}
        return cfg

    def run(self):
        log = None
        try:
            import infra
            log = infra.log
        except Exception:
            pass
        try:
            src = WinSource()
            if not src.handles:
                if log:
                    log.warning("diskio: no drive handles; collector disabled")
                return
            cfg = self._cfg()
            self.eng = Engine(cfg, snap_fn=proc_io_snapshot, window=int(cfg["sample_s"]))
            self.last_cfg = self.last_proc = time.monotonic(); self.prev_snap = proc_io_snapshot()
            if log:
                log.info("diskio collector started on %s", ",".join(sorted(src.handles)))
        except Exception:
            if log:
                log.exception("diskio collector failed to start")
            return
        self.loop(src, log)

    def loop(self, src, log=None):
        """One reading per second until stopped. A failing second (SQLite busy, a counter read) is logged
        and retried with backoff; it never ends the thread (19/11/2026: one exception killed it for good)."""
        fails = 0
        while not self.stop_ev.is_set():
            t0 = time.monotonic()
            try:
                self.step(src)
                fails = 0
            except Exception:
                fails += 1
                if log and (fails == 1 or fails % 60 == 0):
                    log.exception("diskio collector step failed (%d in a row)", fails)
                self.stop_ev.wait(min(60.0, 2.0 ** min(fails, 6)))
                continue
            self.stop_ev.wait(max(0.05, 1.0 - (time.monotonic() - t0)))

    def step(self, src):
        """Intervals use the monotonic clock; the wall clock only stamps rows (it can jump either way)."""
        eng = self.eng
        eng.tick(time.time(), mono_now(), src.read())
        if eng.rows or eng.stalls:
            self._write(eng)
        mono = time.monotonic()
        if mono - self.last_proc >= 60:                       # per-process minute
            snap = proc_io_snapshot()
            rows = [(r["name"], r["r_mb"], r["w_mb"], r["r_ops"], r["w_ops"], r["procs"]) for r in top_procs(self.prev_snap, snap, 30)]
            self.prev_snap = snap; self.last_proc = mono
            now = time.time()
            with self.db._lock:
                c = self.db.conn()
                if rows:
                    self.io.add_procs(c, now - 30, rows)
                self.io.rollup_min(c, now - 180)
        if mono - self.last_cfg >= 300:
            eng.cfg = self._cfg(); self.last_cfg = mono

    def _write(self, eng):
        rows, stalls = eng.rows, eng.stalls
        eng.rows = []; eng.stalls = []
        with self.db._lock:
            c = self.db.conn()
            if rows:
                self.io.add_samples(c, rows)
            for s in stalls:
                free = None
                try:
                    r = c.execute("SELECT disk_free_gb, ram_pct FROM samples WHERE source='live' ORDER BY ts DESC LIMIT 1").fetchone()
                    free, ram = (r[0], r[1]) if r else (None, None)
                except Exception:
                    ram = None
                self.io.add_stall(c, s["ts_start"], s["ts_end"], s["dev"], s["peak_lat_ms"], s["peak_q"], s["busy_avg"], s["r_mb"], s["w_mb"], s["top"], free, ram, s["note"])
                try:
                    detail = "%s | %s" % (s["note"], ", ".join("%s %.0f MB" % (t["name"], t["w_mb"]) for t in s["top"][:3]))
                    self.db.add_event(s["ts_end"], "disk", "warn", "توقف القرص", detail)
                except Exception:
                    pass


_collector = None


def start(db_mod):
    """Called once by agent.py. Never raises."""
    global _collector
    if _collector is not None or not WIN:
        return
    import diskio
    _collector = Collector(db_mod, diskio)
    _collector.start()


def maintenance_job():
    import db, diskio
    with db._lock:
        diskio.maintenance(db.conn())
    return "ok"


# ---------------------------------------------------------------- CLI
def _probe():
    src = WinSource(); print("devices:", ", ".join(sorted(src.handles)) or "NONE")
    prev = src.read(); snap0 = proc_io_snapshot(); t0 = time.time()
    for _ in range(5):
        time.sleep(1); cur = src.read()
        for dev in sorted(cur):
            if dev in prev:
                d = delta(prev[dev], cur[dev]); m = d and metrics(dict(zero(), **d))
                if m:
                    f = lambda v: "-" if v is None else "%.1f" % v
                    print("%-6s R %7.2f MB/s  W %7.2f MB/s  lat R/W %s/%s ms  q %.2f  busy %.0f%%" % (
                        dev, m["r_bps"] / 1048576, m["w_bps"] / 1048576, f(m["lat_r_ms"]), f(m["lat_w_ms"]), m["q_avg"], m["busy_pct"]))
        prev = cur; print("--")
    print("top processes over %.0f s:" % (time.time() - t0))
    for r in top_procs(snap0, proc_io_snapshot(), 8):
        print("  %-28s R %8.2f MB  W %8.2f MB  (%d)" % (r["name"], r["r_mb"], r["w_mb"], r["procs"]))
    src.close()


def _selftest():
    cfg = dict(stall_lat_ms=100, stall_min_s=5, freeze_gap_s=4)
    snaps = [{(1, "a.exe"): (0, 0, 0, 0), (2, "b.exe"): (0, 0, 0, 0)}, {(1, "a.exe"): (50 << 20, 700 << 20, 5, 90), (2, "b.exe"): (1 << 20, 0, 1, 0), (3, "new.exe"): (0, 10 << 20, 0, 5)}]
    calls = {"n": 0}

    def snap_fn():
        i = min(calls["n"], 1); calls["n"] += 1; return snaps[i]
    e = Engine(cfg, snap_fn=snap_fn, window=10)
    qt = 0; cum = [0, 0, 0, 0, 0, 0, 0, 0, 0]; wall = 1_800_000_000.0; mono = 100.0

    def step(wbytes, wops, wtime_ms, idle_frac):
        nonlocal qt, wall, mono
        qt += int(T100NS)
        cum[1] += wbytes; cum[6] += wops; cum[3] += int(wtime_ms * 1e4 * wops); cum[4] += int(T100NS * idle_frac); cum[0] += 1 << 20; cum[5] += 10
        wall += 1; mono += 1
        e.tick(wall, mono, {"disk0": (cum[0], cum[1], cum[2], cum[3], cum[4], cum[5], cum[6], 1, 0, qt)})
    for _ in range(12):               # normal: 50 MB/s, 3 ms, 60% idle
        step(50 << 20, 200, 3.0, 0.6)
    assert len(e.rows) >= 1, "no rows"
    r = [x for x in e.rows if x["dev"] == "disk0"][0]
    assert abs(r["w_bps"] - (50 << 20)) < 1 and abs(r["lat_w_ms"] - 3.0) < 1e-6 and abs(r["busy_pct"] - 40) < 1e-6, r
    assert abs(r["w_size_kb"] - (50 << 20) / 200 / 1024) < 1e-6
    tot = [x for x in e.rows if x["dev"] == "_total"]
    assert tot, "no _total"
    for _ in range(7):                # stall: 400 ms latency for 7 s
        step(5 << 20, 20, 400.0, 0.0)
    assert not e.stalls and "disk0" in e.stall
    for _ in range(3):
        step(50 << 20, 200, 3.0, 0.6)
    assert len(e.stalls) == 1, e.stalls
    s = e.stalls[0]
    assert s["peak_lat_ms"] >= 399 and s["top"][0]["name"] == "a.exe" and abs(s["top"][0]["w_mb"] - 700) < 1e-6, s
    assert {t["name"] for t in s["top"]} == {"a.exe", "b.exe", "new.exe"}
    e2 = Engine(cfg, window=10)
    def rd(k, qts):   # reading with k MB written and given QueryTime seconds
        return {"disk0": (0, k << 20, 0, int(k * 5e4), 0, 0, k * 10, 1, 0, int(qts * T100NS))}
    e2.tick(1000.0, 10.0, rd(0, 0)); e2.tick(1001.0, 11.0, rd(10, 1)); e2.tick(1030.0, 40.0, rd(5000, 30))   # 29 s awake-gap with 5 GB written
    assert len(e2.stalls) == 1 and "frozen" in e2.stalls[0]["note"] and e2.stalls[0]["dev"] is None
    fs = e2.stalls[0]; assert abs(fs["w_mb"] - 4990) < 1 and fs["peak_lat_ms"] is not None, fs
    e2b = Engine(cfg, window=10)                       # sleep: wall/QueryTime jump but awake clock does not -> no stall, no row pollution
    e2b.tick(1000.0, 10.0, rd(0, 0)); e2b.tick(1001.0, 11.0, rd(10, 1)); e2b.tick(9000.0, 12.0, rd(20, 8000))
    assert not e2b.stalls and all(r["w_bps"] < 50e6 for r in e2b.rows), (e2b.stalls, e2b.rows)
    e3 = Engine(cfg, window=10)                                                           # short blip must NOT be a stall
    cum[:] = [0] * 9; qt = 0
    e3_tick = e3.tick
    def step3(lat):
        nonlocal qt, wall, mono
        qt += int(T100NS); cum[1] += 1 << 20; cum[6] += 10; cum[3] += int(lat * 1e4 * 10); wall += 1; mono += 1
        e3_tick(wall, mono, {"disk0": (cum[0], cum[1], cum[2], cum[3], cum[4], cum[5], cum[6], 1, 0, qt)})
    for lat in (3, 3, 300, 300, 3, 3, 3):
        step3(lat)
    assert not e3.stalls
    assert delta((0,) * 9 + (5,), (0,) * 9 + (5,)) is None
    d = delta((0, 0, 0, 0, 0, MASK32, 0, 0, 0, 0), (0, 0, 0, 0, 0, 3, 0, 0, 0, int(T100NS)))
    assert d["ro"] == 4, d      # 32-bit counter wrap
    e4 = Engine(cfg, window=10)                       # wall clock back 47 days (14/11/2026): rows keep flowing
    e4.tick(5_000_000.0, 1.0, rd(0, 0))
    for i in range(1, 25):
        e4.tick(1_000_000.0 + i, 1.0 + i, rd(i, 0.01 * i))
    assert e4.rows and e4.acc_t0 < 5_000_000.0, (e4.rows, e4.acc_t0)
    class _Src:
        n = 0
        def read(self):
            _Src.n += 1
            if _Src.n == 2:
                raise OSError("database is locked")
            return {}
    col = Collector.__new__(Collector)
    col.stop_ev = threading.Event(); col.eng = Engine(cfg, window=10)
    col.last_proc = col.last_cfg = time.monotonic(); col.prev_snap = {}
    calls = []
    def _wait(t):
        calls.append(t)
        if len(calls) >= 4:
            col.stop_ev.set()
    col.stop_ev.wait = _wait
    col.loop(_Src())
    assert _Src.n >= 4 and calls[1] == 2.0, (_Src.n, calls)   # the locked second backs off, the thread lives on
    print("selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--probe" in sys.argv:
        _probe()
    else:
        print(__doc__)
