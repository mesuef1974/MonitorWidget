# -*- coding: utf-8 -*-
"""Always-on headless agent. Owns every background duty so they no longer depend on the dashboard being open:
sampling, automatic memory trim, live folder sizes, space scans, enabled auto-cleanups, quarantine purge, DB maintenance.
Single instance (named mutex). Started at logon and re-started every 5 minutes by a scheduled task if it ever dies."""

from sqlite_runtime import ensure_sqlite
ensure_sqlite()
import ctypes, os, sys, threading, time
import procprio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db, infra, sched, space, snapshot, cpuwatch, gpuwatch, shotwatch, insight, memtrim, commitwatch, bootwatch, portwatch, widgetwatch, battwatch  # noqa: E402
from paths import PORT  # noqa: E402

log = infra.log
_mutex = None


def single_instance():
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\Azkia-MonitorAgent")
    return ctypes.windll.kernel32.GetLastError() != 183


def j_heartbeat():
    with db._lock:
        c = db.conn()
        c.execute("INSERT INTO meta(key,value) VALUES('agent_hb',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(int(time.time())),))
        c.execute("INSERT INTO meta(key,value) VALUES('agent_pid',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(os.getpid()),))
        c.commit()


def j_sample():
    db.add_sample(time.time(), snapshot.native_metrics(), "live")


def j_trim():
    """Adaptive: skip when the auto switch is off, during the first 10 minutes after boot, and whenever memory is comfortable."""
    if not memtrim.auto_trim_on():
        return "skipped: auto trim is off"
    import ctypes
    try:
        k = ctypes.windll.kernel32; k.GetTickCount64.restype = ctypes.c_ulonglong
        up_min = k.GetTickCount64() / 60000.0
    except Exception:
        up_min = 999
    if up_min < 10:
        return "skipped: %.1f min since boot" % up_min
    m = snapshot.native_metrics()
    av, rp = m.get("availGB"), m.get("ramPct")
    try:
        import alertcfg
        warn = float(alertcfg.get()["mem"]["ramWarn"])   # same threshold the dashboard calls "warning" (default 80%)
    except Exception:
        warn = 80.0
    # trim when RAM reaches the warning level (the old gate, < 1 GB free, only fired when the machine was already thrashing)
    # automatic trim only under real pressure: both low free RAM and high use (the old 80% gate fired every ~10 min
    # on a machine that normally runs at 70-75%, and each trim re-read GBs from disk)
    if not (av is not None and av < 3.0 and rp is not None and rp >= 90):
        return "skipped: RAM %s%% (%.2f GB free): no pressure" % (rp, av if av is not None else -1)
    if memtrim.trim_memory() is False:
        return "skipped: trim already running"
    t = memtrim.TRIM
    if t.get("err"):
        raise RuntimeError(t["err"])
    return "%s%% -> %s%% (%s MB)" % (t.get("before"), t.get("after"), t.get("freedMB"))


def j_trimwatch():
    """Reacts within ~30 s when RAM crosses the user's warning level, instead of waiting for the 10-minute trim job.
    trim_memory() pushes the trim job's next_run to now+AUTO_PERIOD, so 'cooldown' = no second trim within 5 minutes."""
    if not memtrim.auto_trim_on() or memtrim.TRIM.get("running"):
        return None
    r = sched.next_run("trim")
    if r and r["next_run"] > time.time() + memtrim.AUTO_PERIOD - 300:
        return "cooldown"
    out = j_trim()
    return out if not str(out).startswith("skipped") else None


def j_server_guard():
    """Keep the local dashboard/API server running (it is started here, not by the widget)."""
    import socket, subprocess
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=1):
            return "up"
    except OSError:
        pass
    exe = sys.executable.replace("python.exe", "pythonw.exe") if sys.executable.lower().endswith("python.exe") else sys.executable
    subprocess.Popen([exe, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")], creationflags=0x08000000)
    log.info("server was down; started it")
    return "started"


def register_protected_collector(scheduler):
    """The protected deployment collects inside its existing elevated agent.

    Its old Limited Collector task stays disabled: do not elevate another task
    or grant unprivileged writers access to administrator-owned app data.
    Source/portable installations retain their existing separate collector task.
    """
    import secure
    if not secure.protected_app_ready():
        return False
    import collector
    scheduler.register("collector", lambda: collector.main(raise_errors=True), 300,
                       mode="after_finish", timeout_s=300, delay=90)
    return True


# Boot-friendly delays: heavy duties wait until the machine has settled (boot trace showed agent.py at 40-140% CPU from 105 s to 186 s).
def main():
    if not single_instance():
        return
    prio = procprio.restore_normal(io=False)   # مهمة المجدول تولد «أقل من العادي» فتُجوَّع العيّنات تحت الحمل؛ القرص يبقى مهذّبًا للمسوح
    c = db.bootstrap()
    log.info("agent start pid=%d priority=%s", os.getpid(), prio)
    s = sched.Scheduler()
    register_protected_collector(s)
    s.register("heartbeat", j_heartbeat, 15)
    s.register("server_guard", j_server_guard, 60, timeout_s=30, delay=10)
    s.register("sample", j_sample, 60)
    s.register("batt_guard", battwatch.guard, 60, timeout_s=20, delay=45)   # حدث تنبيه قبل فراغ البطارية، مرة لكل مستوى في نوبة التفريغ
    s.register("widget_guard", widgetwatch.guard, 60, timeout_s=30, delay=90)   # يعيد الويدجت بعد سقوطه أو قتله، لا بعد إغلاق طلبه المالك
    s.register("cpu", cpuwatch.job, cpuwatch.INTERVAL, timeout_s=20, delay=20)
    s.register("commitwatch", commitwatch.job, commitwatch.INTERVAL, timeout_s=20, delay=35)
    s.register("bootwatch", bootwatch.job, bootwatch.INTERVAL, timeout_s=40, delay=60)
    s.register("portwatch", portwatch.job, portwatch.INTERVAL, timeout_s=40, delay=70)
    s.register("portsample", portwatch.sample_job, portwatch.SAMPLE_S, timeout_s=15, delay=5)   # حلقة لقطات netstat خفيفة تلتقط ذروة اندفاعة المنافذ قبل الحادثة
    s.register("power", insight.power_job, 60, timeout_s=20, delay=50)
    s.register("gpu", gpuwatch.job, gpuwatch.INTERVAL, timeout_s=25, delay=40)
    def _shots_job():
        import importlib
        if os.path.exists(shotwatch.REQ):
            importlib.reload(shotwatch)   # lets the review helper be edited without restarting the agent
        return shotwatch.job()
    s.register("shots", _shots_job, 15, mode="after_finish", timeout_s=900, delay=45)
    # always registered; the job itself checks the trim.auto switch, so the dashboard switch works without restarting the agent
    s.register("trim", j_trim, 3600, mode="after_finish", timeout_s=240, delay=60)  # hourly, pressure-gated (was every 600 s)
    # trimwatch removed: it trimmed at 80% RAM every 5 min and was the main source of freezes
    s.register("live", space.live_tick, 300, mode="after_finish", timeout_s=120, delay=240)
    s.register("scan_small", space.scan, 6 * 3600, timeout_s=300, delay=900)
    s.register("scan_big", space.scan_big, 24 * 3600, timeout_s=1800, delay=1800)
    s.register("auto_clean", space.auto_run, 3600, timeout_s=600, delay=1200)
    s.register("quarantine_purge", space.purge_quarantine, 3600, timeout_s=300, delay=1500)
    s.register("maintenance", infra.maintenance_if_due, 3600, timeout_s=300, delay=600)
    s.register("backup_retry", infra.external_backup_retry, 300, mode="after_finish", timeout_s=120, delay=660)
    try:   # disk I/O collector (1 s IOCTL readings -> io_samples) + hourly rollups/retention; a failure here must never stop the agent
        import diskio_collect
        diskio_collect.start(db)
        s.register("io_maint", diskio_collect.maintenance_job, 3600, timeout_s=120, delay=300)
    except Exception:
        log.exception("disk I/O collector not started")
    try:   # reliability history (event-log reader, hourly); a failure here must never stop the agent
        import relia
        s.register("relia", relia.job, 3600, timeout_s=170, delay=420)
    except Exception:
        log.exception("reliability job not registered")
    try:   # الخادم يبدأ فورًا؛ كان ينتظر تأخير الحارس (10 ثوانٍ) قبل أن يُطلَق
        j_server_guard()
    except Exception:
        log.exception("immediate server start failed")
    s.loop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("agent crashed")
        raise
