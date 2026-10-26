"""Headless collector, run every 5 minutes by the scheduled task MonitorWidget-Collector (pythonw = no window).
Keeps history alive even when the dashboard is closed: metrics sample, process snapshot (leak detection), GPU sample,
hardware/security probe (every 6 h), dashboard state (every 3 h) and the daily health score."""

from sqlite_runtime import ensure_sqlite
ensure_sqlite()
import ctypes, json, os, subprocess, sys, time
import errlog

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import db, health, server, space  # noqa: E402

_mutex = None


def acquire_instance():
    """Kernel-owned mutex survives long runs and is released if the process crashes."""
    global _mutex
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    _mutex = kernel.CreateMutexW(None, True, "Local\\Azkia-MonitorCollector")
    if not _mutex:
        raise ctypes.WinError()
    if kernel.GetLastError() == 183:
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle(_mutex)
        _mutex = None
        return False
    return True


def release_instance():
    global _mutex
    if _mutex:
        kernel = ctypes.windll.kernel32
        kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.ReleaseMutex(_mutex)
        kernel.CloseHandle(_mutex)
        _mutex = None


def log(msg):
    try:
        with open(os.path.join(ROOT, "collector.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
        if os.path.getsize(os.path.join(ROOT, "collector.log")) > 200_000:
            os.remove(os.path.join(ROOT, "collector.log"))
    except Exception:
        errlog.swallowed()


def main(*, raise_errors=False):
    if not acquire_instance():
        return
    try:
        db.bootstrap()
        now = time.time()
        db.import_all()
        m = server.native_metrics()
        db.add_sample(now, m, "collector")
        health.add_procs(now, health.read_procs())
        g, gp = health.read_gpu()
        if g:
            health.add_gpu(now, g, gp)
        try:
            health.add_igpu(now, health.read_igpu(g and g.get('vram')))
        except Exception:
            errlog.swallowed()
        try:
            space.scan_if_due(6)
        except Exception as e:
            log("space scan failed: %s" % e)
        ts, hw = health.get_hw("probe")
        # refresh when the stored probe is older than 6 h, or when the elevated task wrote a newer admin_probe.json
        newer_admin = os.path.exists(health.ADMIN_PROBE) and os.path.getmtime(health.ADMIN_PROBE) > ts + 60
        if now - ts > 6 * 3600 or newer_admin:
            try:
                health.run_probe(skip_updates=bool(hw and "updates" in hw and now - ts < 24 * 3600))
            except Exception as e:
                log("probe failed: %s" % e)
        snap = db.get_json("tasks_snapshot") or {}
        if now - snap.get("ts", 0) > 3 * 3600:
            try:
                r = subprocess.run([server.PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(ROOT, "collect.ps1"), "-Root", ROOT],
                                   capture_output=True, timeout=120, creationflags=server.CNW)
                data = json.loads(r.stdout.decode("utf-8", "replace").strip())
                snap = {"ts": now, "tasks": data.get("tasks", [])}
                db.set_json("tasks_snapshot", snap)
                db.note_tasks(data.get("tasks"))
            except Exception as e:
                log("collect failed: %s" % e)
        if snap.get("tasks") is not None:
            health.save_daily(health.score(m, snap.get("tasks")))
        db.prune_if_due()
    except Exception as e:
        log("error: %r" % e)
        if raise_errors:
            raise
    finally:
        release_instance()


if __name__ == "__main__":
    main()
