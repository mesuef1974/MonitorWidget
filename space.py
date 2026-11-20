# -*- coding: utf-8 -*-
"""Disk-space explainer: periodic folder sizes, growth deltas, free-space drops, SAFE cleanup.
Safety: cleanup is whitelisted to three disposable caches only (never projects, never user documents),
always previewable (dry run), and deletes only files not modified for 3+ days or known cache folders."""
import collections, ctypes, json, os, re, shutil, subprocess, threading, time
import errlog
from ctypes import wintypes

import db
import safedel
import secure

H = os.path.expanduser("~")
LA = os.environ.get("LOCALAPPDATA", os.path.join(H, "AppData", "Local"))
TEMP = os.environ.get("TEMP", os.path.join(LA, "Temp"))
MIN_AGE_DAYS = 3

# key -> (label, path, advice, cleanable-key or None)
DOCKER = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
TARGETS = {
    "docker":   ("Docker (قرص الحاويات)", os.path.join(LA, "Docker"), "يكبر مع الاستخدام ولا يصغر وحده. تنظيف ذاكرة البناء يحرّر داخله، وعودة المساحة للقرص تحتاج ضغطاً لاحقاً.", "dockercache"),
    "temp":     ("الملفات المؤقتة", TEMP, "الحجم هو كل المجلد؛ يُحذف منه ما لم يتغيّر منذ يوم ومعه المجلدات الفارغة القديمة، والباقي قيد الاستخدام.", "temp"),
    "edge":     ("ذاكرة المتصفح Edge", os.path.join(LA, "Microsoft", "Edge"), "الحجم هو كل Edge (ملفات شخصية ونموذج ذكاء محلي)؛ يُمسح منه الكاشف المؤقت فقط.", "edgecache"),
    "claude":   ("أرشيف Claude", os.path.join(H, ".claude"), "جلساتك وإعداداتك؛ لا يُلمس من هنا.", None),
    "packages": ("حزم التطبيقات", os.path.join(LA, "Packages"), "بيانات تطبيقات المتجر؛ لا يُلمس.", None),
    "installer": ("مثبّت ويندوز", r"C:\Windows\Installer", "لا يُحذف يدوياً أبداً.", None),
    "winlogs":  ("سجلات ويندوز", r"C:\Windows\Logs", "صغير.", None),
    "swdist":   ("تنزيلات التحديث", r"C:\Windows\SoftwareDistribution\Download", "كاش تنزيلات التحديثات؛ يعيد ويندوز بناءه. تُترك الملفات قيد الاستخدام.", "swdist"),
    "npm":      ("ذاكرة npm", os.path.join(LA, "npm-cache"), "آمن: تُعاد عند الحاجة، ولا تمسّ مجلدات المشاريع.", "npmcache"),
    "downloads": ("التنزيلات", os.path.join(H, "Downloads"), "ملفاتك الشخصية؛ لا تُلمس.", None),
    "winold":   ("Windows.old (النظام السابق)", r"C:\Windows.old", "يتيح الرجوع للإصدار السابق؛ لا يُحذف إلا بعد 14 يوماً من إنشائه ويستغرق دقائق.", "winold"),
}
# whole-volume breakdown so the list reconciles with the real used space: key -> (label, path, kind) ; kind "file" = single file, "dir" = slow folder scan
BIG = {
    "pagefile": ("ملف الذاكرة الافتراضية", r"C:\pagefile.sys", "file"),
    "hiber": ("ملف السبات", r"C:\hiberfil.sys", "file"),
    "swapf": ("ملف تبديل التطبيقات", r"C:\swapfile.sys", "file"),
    "windows": ("ويندوز", r"C:\Windows", "dir"),
    "progfiles": ("Program Files", r"C:\Program Files", "dir"),
    "progfiles86": ("Program Files (x86)", r"C:\Program Files (x86)", "dir"),
    "progdata": ("ProgramData", r"C:\ProgramData", "dir"),
    "profile": ("ملفك الشخصي", H, "dir"),
}
BIG_HOURS = 24
SCHEMA = ("CREATE TABLE IF NOT EXISTS dirsize(ts INTEGER NOT NULL, key TEXT NOT NULL, gb REAL, files INTEGER, PRIMARY KEY(ts,key)) WITHOUT ROWID;"
          "CREATE TABLE IF NOT EXISTS space_runs(id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, kind TEXT, mb REAL, files INTEGER, skipped INTEGER, src TEXT);")
SAFE = ("temp", "edgecache", "npmcache")
# kinds whose large files go to the 7-day quarantine instead of being deleted (setting "space.quarantine_kinds", default temp).
# The quarantine lives on the same volume, so those bytes are freed only when it is purged (Q_DAYS later); small files are deleted at once.
Q_MIN_BYTES = 5 * 2**20
KIND_LABEL = {"temp": "الملفات المؤقتة", "edgecache": "ذاكرة Edge", "npmcache": "ذاكرة npm", "swdist": "تنزيلات تحديث ويندوز", "winold": "Windows.old"}
# manual-only kinds (never part of the hourly auto-clean): the update download cache, and the previous Windows after its grace period.
EXTRA = ("swdist", "winold")
AUTO_EXTRA = ("pipcache",)   # extras.py light kinds that may be switched on for the hourly auto-clean
# البند 32 من المجلس 19/11: Windows\Temp وتقارير الأعطال يملكها محرّك التنظيف (قاعدتا windows-temp وcrash-dumps يوميًّا،
# وتبقى أحدث 3 تفريغات)، فخرجا من الساعي هنا وبقي زراهما يدويَّين؛ ودورتان على المجلدات نفسها كانت تنقض keepNewest.
# البند 25 من المجلس 19/11: مخابئ تُعاد تنزيلها عند الحاجة؛ مسحها الساعي والقرص غير ممتلئ إعادةُ تنزيل وبطء (الخط الأحمر 7).
# فالتلقائي لها يعمل فقط حين تنزل المساحة الحرة في C عن عتبة تحذير المساحة (alertcfg disk.freeWarnGB)؛ اليدوي متاح دائمًا.
CACHE_KINDS = ("edgecache", "npmcache", "pipcache")
BULK = SAFE + ("swdist",)     # what "clean all" runs; Windows.old is deliberately a separate, explicit action
WINOLD = r"C:\Windows.old"
WINOLD_DAYS = 14
SWDIST = r"C:\Windows\SoftwareDistribution\Download"
SETTINGS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "space.settings.json")


def _setting(k, d=None):
    with db._lock:
        r = _c().execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    try:
        return json.loads(r[0]) if r else d
    except Exception:
        return d


def _setting_set(k, v):
    with db._lock:
        c = _c(); c.execute("INSERT INTO settings(key,value,ts) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts", (k, json.dumps(v), int(time.time()))); c.commit()


def get_auto():
    a = _setting("space.auto")
    if a is None:
        a = {}
        try:   # one-time import of the old JSON file
            a = json.load(open(SETTINGS, encoding="utf-8")).get("auto", {})
        except Exception:
            errlog.swallowed()
        _setting_set("space.auto", {k: bool(a.get(k)) for k in SAFE})
    return {k: bool(a.get(k)) for k in SAFE + AUTO_EXTRA}


def set_auto(kind, on):
    if kind not in SAFE + AUTO_EXTRA:
        return False
    a = get_auto(); a[kind] = bool(on)
    _setting_set("space.auto", a)
    return True


def _held_note(r):
    """The audit row's expected MB counts what went to the quarantine, its actual MB does not: say so, or the gap reads as a shortfall."""
    h = r.get("heldMB") or 0
    return "حُجز %.1f MB في الحجر؛ يتحرّر بعد %d أيام" % (h, Q_DAYS) if h > 0 else None


def run_scan_big():
    """The slow whole-volume pass in a thread: the 'scanning' flag is always cleared, also when the pass raises (it used to stay on forever)."""
    try:
        scan_big()
    except Exception:
        errlog.swallowed()
    finally:
        state["big"] = False


def clean(kind, dry=True, src="manual", early=False, prog=None, exp=None):
    """Preview (dry=True) or run a whitelisted cleanup. Every real run is audited: expected vs actual and free space before/after.
    early: owner's explicit override of the Windows.old age gate (the UI asks for a separate confirmation).
    prog: the background clean job, updated while deleting; exp: the MB that job already measured, so it is not measured twice."""
    if dry:
        return _clean_impl(kind, True, src)
    if exp is None:
        try:
            exp = dry_mb(kind)
        except Exception:
            exp = None
    fb = shutil.disk_usage("C:\\").free
    r = _clean_impl(kind, False, src + "-early" if early and kind == "winold" else src, early, prog)
    if (_pv.get(kind) or {}).get("state") != "running":
        _pv.pop(kind, None)   # the preview no longer describes the folder
    fa = shutil.disk_usage("C:\\").free
    r.update(expectedMB=exp, freeBeforeGB=round(fb / 2**30, 2), freeAfterGB=round(fa / 2**30, 2))
    if kind in ("dism", "claudearchive", "dockercompact") or r.get("started"):
        return r   # background jobs (dism, archive, compact, and a started Windows.old delete) write their own audit row when they finish
    try:
        with db._lock:
            c = _c()
            cur = c.execute("INSERT INTO actions(ts,kind,src,expected_mb,actual_mb,files,skipped,free_before_gb,free_after_gb,ok,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (int(time.time()), kind, src, exp, r.get("mb", 0), r.get("files", 0), r.get("skipped", 0), r["freeBeforeGB"], r["freeAfterGB"], 1 if r.get("ok") else 0, r.get("error") or _held_note(r)))
            c.commit(); r["actionId"] = cur.lastrowid
    except Exception:
        errlog.swallowed()
    return r


# ---------------- background preview: measuring temp walks thousands of files (6-7 minutes measured live), so the POST
# only starts a thread and the page polls GET /api/space-preview?kind= for progress and the result
PREVIEW_TTL = 300   # seconds a finished preview stays fresh enough to stand in for a re-measure before a real clean
_pv = {}            # kind -> {state: running|done|error, started, ended, dirs, files, mb, res|error}; mutated by the worker only
_pv_lock = threading.Lock()


def _previewable(kind):
    if not isinstance(kind, str):
        return False
    if kind in SAFE + ("swdist", "winold", "dockercache"):
        return True
    import extras
    return kind in extras.KINDS


def _preview_run(kind, j):
    try:
        j["res"] = _clean_impl(kind, True, "manual", prog=j)
        st = "done"
    except Exception as e:
        errlog.swallowed()
        j["error"] = str(e)[:120]
        st = "error"
    j["ended"] = time.time()
    j["state"] = st   # last: a reader that sees done/error also sees ended and res/error


def preview_status(kind):
    j = _pv.get(kind)
    if not j:
        return {"state": "none"}
    out = {"state": j["state"], "dirs": j["dirs"], "files": j["files"], "mb": j["mb"], "elapsed": round((j.get("ended") or time.time()) - j["started"], 1)}
    if j["state"] == "done":
        out["res"] = j["res"]
    elif j["state"] == "error":
        out["error"] = j["error"]
    return out


def preview_start(kind):
    """Start measuring kind in the background; a preview already running for that kind is returned as is (idempotent)."""
    if not _previewable(kind):
        return {"ok": False, "state": "error", "error": "غير مسموح"}
    with _pv_lock:
        j = _pv.get(kind)
        if not (j and j["state"] == "running"):
            j = _pv[kind] = {"state": "running", "started": time.time(), "ended": None, "dirs": 0, "files": 0, "mb": 0.0}
            threading.Thread(target=_preview_run, args=(kind, j), daemon=True).start()
    return {"ok": True, **preview_status(kind)}


def fresh_preview_mb(kind, max_age=PREVIEW_TTL):
    """The MB of a successful preview that finished within max_age seconds, else None."""
    j = _pv.get(kind)
    if j and j["state"] == "done" and (j["res"] or {}).get("ok") and time.time() - j["ended"] <= max_age:
        return j["res"].get("mb", 0)
    return None


def dry_mb(kind, prog=None):
    """What a clean of kind would free now: a fresh preview when there is one, else a synchronous measure."""
    mb = fresh_preview_mb(kind)
    return mb if mb is not None else _clean_impl(kind, True, "manual", prog=prog).get("mb", 0)


# ---------------- background clean: a real temp clean walks the same tens of thousands of folders as the preview, so after the
# confirm token the POST only starts a thread. The job first re-measures against what the user saw (a fresh preview stands in),
# refuses a candidate set that grew past the tolerance, then cleans kind by kind. One clean job at a time; the page polls
# GET /api/space-clean?kind= (kind "all" for the bulk clean)
_cj = {}            # key -> {state, started, ended, phase: measure|clean, kind, dirs, files, mb, res|error[, now_mb]}
_cj_lock = threading.Lock()


def grew(cur, seen):
    return cur > seen * 1.25 + 50


def _clean_run(key, kinds, seen, early, j):
    try:
        exp = {}
        for k in kinds:
            j.update(kind=k, dirs=0, files=0, mb=0.0)
            exp[k] = dry_mb(k, j)
        cur = sum(exp.values())
        if grew(cur, seen):
            j.update(error="candidates_changed", now_mb=round(cur, 1))
            st = "error"
        else:
            j["phase"] = "clean"
            out = []
            for k in kinds:
                j.update(kind=k, dirs=0, files=0, mb=0.0, emptyDirs=0)
                out.append(dict(kind=k, **clean(k, dry=False, src="manual", early=early, prog=j, exp=exp[k])))
            j["res"] = out[0] if key != "all" else {"ok": True, "mb": round(sum(o.get("mb", 0) for o in out), 1), "parts": out}
            st = "done"
    except Exception as e:
        errlog.swallowed()
        j["error"] = str(e)[:120]
        st = "error"
    j["ended"] = time.time()
    j["state"] = st   # last, as in the preview job


def clean_status(key):
    j = _cj.get(key)
    if not j:
        return {"state": "none"}
    out = {k: j.get(k, 0) for k in ("state", "phase", "kind", "dirs", "files", "mb", "emptyDirs")}
    out["elapsed"] = round((j.get("ended") or time.time()) - j["started"], 1)
    if j["state"] == "done":
        out["res"] = j["res"]
    elif j["state"] == "error":
        out["error"] = j["error"]
        if "now_mb" in j:
            out["now_mb"] = j["now_mb"]
    return out


def clean_start(key, seen, early=False, volumes=None):
    """Start a confirmed clean of key (a kind, or "all" for BULK) in the background. The same key already running is returned as is;
    any other running clean refuses (two walks deleting in the same folders would double count and race).
    volumes: for dockervolumes, the names the owner confirmed one by one; nothing else is removed (item 16 of the 19/11 council)."""
    if key != "all" and not _previewable(key):
        return {"ok": False, "state": "error", "error": "غير مسموح"}
    if key == "dockervolumes" and not volumes:
        return {"ok": False, "state": "error", "error": "اختر الوحدات التي تُحذف واحدةً واحدة"}
    with _cj_lock:
        run = next((k for k, j in _cj.items() if j["state"] == "running"), None)
        if run is not None and run != key:
            return {"ok": False, "state": "error", "error": "clean already running", "running": run}
        if run is None:
            kinds = tuple(BULK) if key == "all" else (key,)
            j = _cj[key] = {"state": "running", "started": time.time(), "ended": None, "phase": "measure", "kind": kinds[0], "dirs": 0, "files": 0, "mb": 0.0}
            if key == "dockervolumes":
                j["volumes"] = tuple(volumes)
            threading.Thread(target=_clean_run, args=(key, kinds, seen, early, j), daemon=True).start()
    return {"ok": True, **clean_status(key)}


# ---------------- quarantine: reversible removal for riskier items (held 7 days, then purged)
Q_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quarantine")
Q_DAYS = 7


def _quarantine_store():
    from quarantine_store import QuarantineStore
    return QuarantineStore(db, Q_DIR, _c, allowed_origins=(TEMP,), days=Q_DAYS)


def quarantine_move(path, action_id=None):
    """Durable intent + atomic same-volume rename; never overwrites an existing path."""
    return _quarantine_store().move(path, action_id, lambda p: _size(p, time.time() + 120, fast=True)[0])


def quarantine_list():
    return _quarantine_store().listing()


def quarantine_restore(qid):
    return _quarantine_store().restore(qid)


def _is_restored(path, keep):
    from quarantine_store import QuarantineStore
    return QuarantineStore.is_restored(path, keep)


def _restored_paths():
    """{مسار موحّد: هوية} لما استرجعه المالك من الحجر خلال 30 يومًا؛ فارغة عند أي خطأ فلا يتوقف التنظيف."""
    try:
        return _quarantine_store().restored_paths()
    except Exception:
        errlog.swallowed()
        return {}


Q_CAP_GB = 10   # setting "space.quarantine_cap_gb": what the hold may keep on C; above it the oldest held items go before their 7 days


def purge_quarantine():
    """Hourly: the expired items, then the oldest beyond the size cap; an early purge leaves an alert (item 13 of the 19/11 council)."""
    try:
        cap = float(_setting("space.quarantine_cap_gb", Q_CAP_GB))
    except (TypeError, ValueError):
        cap = Q_CAP_GB
    res = _quarantine_store().purge(cap_bytes=int(max(1.0, cap) * 2**30))
    m = re.search(r"over cap (\d+)", res)
    if m and int(m.group(1)):
        try:
            db.add_event(int(time.time()), "disk", "warn", "الحجر تجاوز سقفه فحُذف أقدمه قبل موعده",
                         "سقف الحجر %g GB؛ حُذف %s عنصرًا قبل انقضاء %d أيام. %s" % (cap, m.group(1), Q_DAYS, res))
        except Exception:
            errlog.swallowed()
    return res


QP = {"running": False, "res": None}
_qp_lock = threading.Lock()
QP_TOLERANCE_MB = 5


def quarantine_purge_now(seen_mb):
    """Owner-triggered permanent deletion of everything held, before the 7 days end. Refuses when what is held now exceeds what the
    owner saw (new items arrived after the confirmation dialog); the deletion itself runs in the background."""
    store = _quarantine_store()
    held_mb = store.held_bytes() / 2**20
    if held_mb > seen_mb * 1.02 + QP_TOLERANCE_MB:
        return {"ok": False, "error": "تغيّر محتوى الحجر منذ آخر عرض؛ حدّث الصفحة وأعد المحاولة"}
    with _qp_lock:
        if QP["running"]:
            return {"ok": False, "running": True, "error": "تفريغ الحجر جارٍ"}
        QP.update(running=True, res=None)

    def run():
        try:
            QP["res"] = store.purge(force=True)
        except Exception as exc:
            errlog.swallowed()
            QP["res"] = "failed: %s" % str(exc)[:200]
        finally:
            QP["running"] = False
    threading.Thread(target=run, daemon=True, name="quarantine-purge-now").start()
    return {"ok": True, "started": True}


def auto_gate():
    """بوابة المخابئ (البند 25): gated=True حين المساحة الحرة في C عند عتبة التحذير أو فوقها، فلا تُمسح المخابئ تلقائيًا."""
    import alertcfg
    th = alertcfg.get()["disk"]["freeWarnGB"]
    try:
        free = shutil.disk_usage("C:\\").free / 2**30
    except OSError:
        return {"gated": False, "freeGB": None, "thresholdGB": th, "kinds": list(CACHE_KINDS)}   # لا قياس: لا تُمنع (سلوك ما قبل البند)
    return {"gated": free >= th, "freeGB": round(free, 1), "thresholdGB": th, "kinds": list(CACHE_KINDS)}


def auto_run():
    """Hourly: run the cleanups the user switched on (each is limited to disposable data); caches only below the free-space gate."""
    gate = auto_gate()
    for k, on in get_auto().items():
        if on and not (gate["gated"] and k in CACHE_KINDS):
            clean(k, dry=False, src="auto")

_ready = False
_scan_lock = threading.Lock()
state = {"running": False, "last": 0}


def _c():
    global _ready
    if not _ready:
        import infra
        with db._lock:
            db.conn().executescript(SCHEMA); db.conn().commit(); infra.migrate(db.conn())
        _ready = True
    return db.conn()


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
_k32.GetCompressedFileSizeW.restype = wintypes.DWORD
CLOUD = 0x1000 | 0x40000 | 0x400000  # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS: placeholder, not on this disk


def _alloc(path, logical):
    hi = wintypes.DWORD(0)
    lo = _k32.GetCompressedFileSizeW(path, ctypes.byref(hi))
    if lo == 0xFFFFFFFF and ctypes.get_last_error() != 0:
        return logical
    return (hi.value << 32) | lo


def _size(path, deadline, dedupe=False, fast=False):
    """Space actually allocated on disk: sparse/compressed files by allocation, cloud placeholders as 0,
    and (dedupe=True) hard-linked files counted once. Returns (bytes, files)."""
    tot = n = 0
    seen = set()
    if os.path.isfile(path):
        try:
            return _alloc(path, os.stat(path).st_size), 1
        except OSError:
            return 0, 0
    stack = [path]
    while stack:
        if time.time() > deadline:
            break
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if e.stat(follow_symlinks=False).st_file_attributes & 0x400:
                                continue  # junction (e.g. "Application Data"): its target is counted where it really lives
                            stack.append(e.path)
                            continue
                        st = e.stat(follow_symlinks=False)
                        n += 1
                        if getattr(st, "st_file_attributes", 0) & CLOUD:
                            continue
                        if dedupe:
                            fs = os.stat(e.path)
                            if fs.st_nlink > 1:
                                key = (fs.st_dev, fs.st_ino)
                                if key in seen:
                                    continue
                                seen.add(key)
                        tot += st.st_size if fast else _alloc(e.path, st.st_size)
                    except OSError:
                        errlog.swallowed()
        except PermissionError:
            pass  # protected system folders: skipped silently, not logged on every scan
        except OSError:
            errlog.swallowed()
    return tot, n


def scan(budget=100):
    if not _scan_lock.acquire(blocking=False):
        return False
    state["running"] = True
    try:
        ts = int(time.time()); deadline = time.time() + budget; rows = []
        for k, (_, p, _, _) in TARGETS.items():
            if k != "winold" and os.path.exists(p):   # Windows.old is huge: measured by its own background thread
                # own budget per folder: one huge folder must not starve the ones after it (they were recorded as 0)
                b, n = _size(p, time.time() + budget)
                rows.append((ts, k, round(b / 2**30, 3), n))
        for k in SAFE:
            try:
                r = clean(k, dry=True)
                rows.append((ts, "rec_" + k, round(r["gb"], 4), r["files"]))
            except Exception:
                errlog.swallowed()
        with db._lock:
            c = _c(); c.executemany("INSERT OR REPLACE INTO dirsize VALUES(?,?,?,?)", rows)
            c.commit()
        state["last"] = ts
        return True
    finally:
        state["running"] = False; _scan_lock.release()


def drives():
    """Fixed local drives (letter, label, used/total/free GB)."""
    out = []
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                L = chr(65 + i); root = L + ":\\"
                if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
                    try:
                        du = shutil.disk_usage(root)
                    except OSError:
                        continue
                    lab = ctypes.create_unicode_buffer(64)
                    ctypes.windll.kernel32.GetVolumeInformationW(root, lab, 64, None, None, None, None, 0)
                    out.append({"letter": L, "label": lab.value, "used": round(du.used / 2**30, 1), "total": round(du.total / 2**30, 1), "free": round(du.free / 2**30, 1)})
    except Exception:
        errlog.swallowed()
    return out


def scan_drive(L, budget=600):
    """Top-level folders of another drive, by on-disk size."""
    root = L + ":\\"; ts = int(time.time()); deadline = time.time() + budget; rows = []; files_b = 0
    try:
        with os.scandir(root) as it:
            ents = list(it)
    except OSError:
        return
    for e in ents:
        try:
            if e.name.lower() in ("$recycle.bin", "system volume information"):
                continue
            if e.is_dir(follow_symlinks=False):
                b, n = _size(e.path, deadline)
                rows.append((ts, "vol_%s_%s" % (L, e.name), round(b / 2**30, 3), -1 if time.time() > deadline else n))
            else:
                files_b += _alloc(e.path, e.stat().st_size)
        except OSError:
            errlog.swallowed()
    if files_b:
        rows.append((ts, "vol_%s_(ملفات الجذر)" % L, round(files_b / 2**30, 3), 0))
    if rows:
        with db._lock:
            c = _c(); c.executemany("INSERT OR REPLACE INTO dirsize VALUES(?,?,?,?)", rows); c.commit()


def drive_overview(L):
    with db._lock:
        c = _c()
        t = c.execute("SELECT MAX(ts) FROM dirsize WHERE key LIKE ?", ("vol_%s_%%" % L,)).fetchone()[0]
        rows = c.execute("SELECT key, gb, files FROM dirsize WHERE ts=? AND key LIKE ?", (t, "vol_%s_%%" % L)).fetchall() if t else []
    pre = "vol_%s_" % L
    parts = sorted(({"label": r["key"][len(pre):], "gb": r["gb"], "partial": r["files"] == -1} for r in rows), key=lambda x: -x["gb"])
    return {"parts": parts, "ts": t, "scanning": bool(state.get("big"))}


ROOT_MIN_GB = 0.1
ROOT_SKIP = {"users"}   # the profile is its own part; other users' profiles are not readable anyway


def _root_rows(ts, deadline):
    """Readable top-level folders of C:\\ that BIG does not cover (e.g. a projects folder), so they are named instead of lumped into "other"."""
    covered = {os.path.normcase(os.path.normpath(p)) for _, p, _ in BIG.values()} | {os.path.normcase(os.path.normpath(H))}
    rows = []
    try:
        entries = list(os.scandir("C:\\"))
    except OSError:
        errlog.swallowed()
        return rows
    for e in entries:
        try:
            if not e.is_dir(follow_symlinks=False) or e.name.lower() in ROOT_SKIP or os.path.normcase(os.path.normpath(e.path)) in covered:
                continue
            if e.stat(follow_symlinks=False).st_file_attributes & 0x400:
                continue
            b, n = _size(e.path, deadline)
            if b / 2**30 >= ROOT_MIN_GB:
                rows.append((ts, "big_root_" + e.name, round(b / 2**30, 3), -1 if time.time() > deadline else n))
        except OSError:
            errlog.swallowed()
    return rows


def scan_big(budget=900):
    """Slow whole-volume pass (daily). Partial results are flagged, never presented as exact.
    يعمل الخيط بوضع الخلفية (أولوية قرص منخفضة جدًا، البند 36 من المجلس 19/11)، ويعيد مدته وذروة القرص الكلية في نافذته
    (من io_min) فتُحفظ في تفاصيل المهمة وسجل job_runs، ويُعرف هل زاحم المسحُ القرصَ أم لا."""
    import procprio
    t0 = time.time()
    with procprio.background_thread() as low:
        n = _scan_big(budget)
    return "%d بندًا في %.0f ث%s؛ %s" % (n, time.time() - t0, "" if low else " (بلا وضع الخلفية)", _disk_peak(t0, time.time()))


def _disk_peak(t0, t1):
    """ذروة القرص الكلية بين t0 وt1 من دقائق io_min: أعلى انشغال وأعلى زمن استجابة وأطول طابور. نصّ للتفاصيل، لا يرمي."""
    try:
        with db.ro() as c:
            r = c.execute("SELECT MAX(busy_max), MAX(MAX(COALESCE(lat_r_max,0), COALESCE(lat_w_max,0))), MAX(q_max) FROM io_min "
                          "WHERE dev='_total' AND ts BETWEEN ? AND ?", (int(t0) - 60, int(t1))).fetchone()
    except Exception:
        return "ذروة القرص غير مقيسة"
    if not r or r[0] is None:
        return "ذروة القرص غير مقيسة"
    return "ذروة القرص: انشغال %.0f%%، استجابة %.1f مللي ث، طابور %.1f" % (r[0] or 0, r[1] or 0, r[2] or 0)


def _scan_big(budget):
    ts = int(time.time()); deadline = time.time() + budget; rows = []
    for k, (_, p, kind) in BIG.items():
        if not os.path.exists(p):
            continue
        if kind == "file":
            try:
                rows.append((ts, "big_" + k, round(os.stat(p).st_size / 2**30, 3), 1))
            except OSError:
                errlog.swallowed()
        else:
            b, n = _size(p, deadline, dedupe=(k in ("windows", "progdata", "progfiles")))
            rows.append((ts, "big_" + k, round(b / 2**30, 3), -1 if time.time() > deadline else n))  # files=-1 marks a truncated scan
    rows += _root_rows(ts, deadline)
    if rows:
        with db._lock:
            c = _c(); c.executemany("INSERT OR REPLACE INTO dirsize VALUES(?,?,?,?)", rows); c.commit()
    for d in drives():
        if d["letter"] != "C":
            scan_drive(d["letter"])
    return len(rows)


def scan_if_due(hours=6):
    with db._lock:
        r = _c().execute("SELECT MAX(ts) FROM dirsize WHERE key NOT LIKE 'big_%' AND key NOT LIKE 'vol_%'").fetchone()
    if not r[0] or time.time() - r[0] > hours * 3600:
        scan()
    with db._lock:
        r = _c().execute("SELECT MAX(ts) FROM dirsize WHERE key LIKE 'big_%'").fetchone()
    if (not r[0] or time.time() - r[0] > BIG_HOURS * 3600) and not state.get("big"):
        state["big"] = True
        threading.Thread(target=run_scan_big, daemon=True).start()


LIVE_KEYS = ("docker", "temp", "edge", "npm", "swdist", "downloads")


def live_tick():
    """Agent job: re-measure every watched folder (cheap) and publish to fs_live; docker reclaimable every 3 min."""
    now = int(time.time()); rows = []
    for k in LIVE_KEYS:  # only the folders that change between scans; short per-folder budget
        _, p, _, _ = TARGETS[k]
        if os.path.exists(p):
            b, n = _size(p, time.time() + 15, fast=True)
            rows.append((k, round(b / 2**30, 3), n, now))
    with db._lock:
        c = _c()
        last = c.execute("SELECT ts FROM fs_live WHERE key='dockercache_mb'").fetchone()
    if not last or now - last[0] > 180:
        try:
            r = docker_cache(True)
            rows.append(("dockercache_mb", r.get("mb") if r.get("ok") else None, 0, now))
        except Exception:
            errlog.swallowed()
    with db._lock:
        c = _c(); c.executemany("INSERT INTO fs_live(key,gb,files,ts) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET gb=excluded.gb, files=excluded.files, ts=excluded.ts", rows); c.commit()
    return "%d folders" % len(rows)


def _fs_live():
    with db._lock:
        return {r["key"]: (r["gb"], r["files"], r["ts"]) for r in _c().execute("SELECT key, gb, files, ts FROM fs_live").fetchall()}


def _agent_status():
    with db._lock:
        r = _c().execute("SELECT value FROM meta WHERE key='agent_hb'").fetchone()
    hb = int(r[0]) if r else None
    return {"alive": bool(hb and time.time() - hb < 60), "heartbeat": hb}


def _drops(c, hours=72, min_gb=2.0):
    rows = c.execute("SELECT ts, disk_free_gb v FROM samples WHERE disk_free_gb IS NOT NULL AND ts>=? ORDER BY ts", (int(time.time()) - hours * 3600,)).fetchall()
    out = []
    dq, j = collections.deque(), 0   # أصغر قيمة في نافذة الثلاث ساعات بمؤشرين: خطي لا تربيعي (72 ساعة من العينات كانت تكلّف نصف ثانية تحت القفل)
    for i, a in enumerate(rows):
        while j < len(rows) and rows[j]["ts"] - a["ts"] <= 3 * 3600:
            while dq and rows[dq[-1]]["v"] > rows[j]["v"]:
                dq.pop()
            dq.append(j); j += 1
        while dq[0] < i:
            dq.popleft()
        lo = rows[dq[0]]
        if a["v"] - lo["v"] >= min_gb:
            if out and a["ts"] <= out[-1]["to"]:
                m = out[-1]
                if a["v"] > m["hi"]:
                    m["hi"] = a["v"]
                if lo["v"] < m["lo"]:
                    m["lo"] = lo["v"]; m["to"] = lo["ts"]
            else:
                out.append({"from": a["ts"], "to": lo["ts"], "hi": a["v"], "lo": lo["v"]})
    for m in out:
        m["gb"] = round(m["hi"] - m["lo"], 1); m["hi"] = round(m["hi"], 1); m["lo"] = round(m["lo"], 1)
    return out[-3:]


_rc = {"t": 0, "v": {}}


def _rec_refresh():
    try:
        r = {k: clean(k, dry=True) for k in SAFE + ("swdist", "winold")}
        _rc["v"] = {k: v.get("mb", 0.0) for k, v in r.items()}; _rc["locked"] = {k: v.get("lockedMB", 0.0) for k, v in r.items()}
    except Exception:
        errlog.swallowed()
    finally:
        _rc["t"] = time.time(); _rc["busy"] = False


def _rec_live():
    """What each safe cleanup would free (cached 60 s). Measured in the background so the page never waits on a slow folder walk."""
    if time.time() - _rc["t"] > 60 and not _rc.get("busy"):
        _rc["busy"] = True
        threading.Thread(target=_rec_refresh, daemon=True).start()
    return _rc["v"] or {k: 0.0 for k in SAFE + EXTRA}


def overview():
    with db._lock:
        c = _c()
        snaps = [r[0] for r in c.execute("SELECT DISTINCT ts FROM dirsize WHERE key NOT LIKE 'big_%' AND key NOT LIKE 'vol_%' ORDER BY ts DESC LIMIT 200").fetchall()]
        free = c.execute("SELECT disk_free_gb FROM samples WHERE disk_free_gb IS NOT NULL ORDER BY ts DESC LIMIT 1").fetchone()
        hist = [(r[0], r[1]) for r in c.execute("SELECT (ts/600)*600 t, ROUND(AVG(disk_free_gb),2) v FROM samples WHERE disk_free_gb IS NOT NULL AND ts>=? GROUP BY t ORDER BY t", (int(time.time()) - 86400,)).fetchall()]
        drops = _drops(c)
        cur = {r["key"]: r for r in c.execute("SELECT * FROM dirsize WHERE ts=?", (snaps[0],)).fetchall()} if snaps else {}
        old, oldts = {}, None
        if len(snaps) > 1:
            want = snaps[0] - 24 * 3600
            oldts = min(snaps[1:], key=lambda t: abs(t - want))
            old = {r["key"]: r for r in c.execute("SELECT * FROM dirsize WHERE ts=?", (oldts,)).fetchall()}
    rec = _rec_live()
    with db._lock:
        runs = [dict(r) for r in _c().execute("SELECT ts, kind, actual_mb AS mb, expected_mb, files, skipped, src, free_before_gb, free_after_gb FROM actions WHERE ok=1 ORDER BY ts DESC LIMIT 6").fetchall()]
    LV = _fs_live()
    du = shutil.disk_usage("C:\\")
    with db._lock:
        bigrows = {r["key"]: r for r in _c().execute("SELECT key, gb, files, ts FROM dirsize WHERE key LIKE 'big_%' AND ts=(SELECT MAX(ts) FROM dirsize WHERE key LIKE 'big_%')").fetchall()}
    parts = []
    for k, (label, p, kind) in BIG.items():
        r = bigrows.get("big_" + k)
        if r:
            parts.append({"key": k, "label": label, "gb": r["gb"], "partial": r["files"] == -1})
    parts += [{"key": k, "label": "C:\\" + k[len("big_root_"):], "gb": r["gb"], "partial": r["files"] == -1} for k, r in bigrows.items() if k.startswith("big_root_")]
    known = sum(x["gb"] for x in parts)
    used = round(du.used / 2**30, 1)
    volume = {"used": used, "total": round(du.total / 2**30, 1), "free": round(du.free / 2**30, 1), "parts": parts,
              "other": round(max(0.0, used - known), 1) if parts else None, "ts": next(iter(bigrows.values()))["ts"] if bigrows else None,
              "partial": any(x["partial"] for x in parts), "scanning": bool(state.get("big"))}
    items = []
    for k, (label, p, advice, clean) in TARGETS.items():
        if k == "winold":
            if os.path.isdir(p):
                wb, wn = _winold_size()
                items.append({"key": k, "label": label, "gb": round(wb / 2**30, 3), "delta": None, "files": wn, "clean": clean, "measuring": not _wo["t"],
                              "advice": advice + ("" if _wo["t"] else " (جارٍ القياس…)")})
            continue
        if k not in cur:
            continue
        gb, nf = cur[k]["gb"], cur[k]["files"]
        lv = LV.get(k)
        if lv and lv[0] is not None and time.time() - lv[2] < 300:
            gb, nf = lv[0], lv[1]
        d = round(gb - old[k]["gb"], 2) if k in old else None
        items.append({"key": k, "label": label, "gb": gb, "delta": d, "files": nf, "advice": advice, "clean": clean})
    items.sort(key=lambda x: -x["gb"])
    grew = [x for x in items if x["delta"] and x["delta"] >= 0.3]
    return {"free": round(free[0], 1) if free else None, "hist": hist, "drops": drops, "items": items,
            "ts": (int(max(v[2] for kk, v in LV.items() if kk in TARGETS)) if any(kk in TARGETS for kk in LV) else (snaps[0] if snaps else None)), "since": oldts, "culprit": max(grew, key=lambda x: x["delta"])["key"] if grew else None,
            "running": state["running"], "auto": get_auto(), "autoGate": auto_gate(), "recoverableMB": rec, "runs": runs, "dockerMB": (LV.get("dockercache_mb") or (None,))[0], "agent": _agent_status(), "lockedMB": _rc.get("locked", {}), "winold": winold_status(), "elevated": {k: elevated_state(k) for k in ELEVATED}, "bulk": list(BULK), "extra": list(EXTRA), "labels": KIND_LABEL, "volume": volume, "extras": _extras_overview(),
            "drives": drives(), "dvol": {d["letter"]: drive_overview(d["letter"]) for d in drives() if d["letter"] != "C"}}


def _extras_overview():
    try:
        import extras
        return extras.overview()
    except Exception:
        errlog.swallowed()
        return None


def _edge_running():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq msedge.exe", "/NH"], capture_output=True, timeout=15, creationflags=0x08000000)
        return b"msedge.exe" in r.stdout
    except Exception:
        return True


_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_INVALID = wintypes.HANDLE(-1).value


def _deletable(p):
    h = _k32.CreateFileW(p, 0x10000, 7, None, 3, 0x02000000, None)  # DELETE access, share all, OPEN_EXISTING, BACKUP_SEMANTICS
    if h is None or h == _INVALID:
        return False
    _k32.CloseHandle(h)
    return True


_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: junctions, symlinks, mount points


def _is_reparse(path):
    try:
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & _REPARSE) or os.path.islink(path)
    except OSError:
        return True   # unreadable: treat as unsafe


def _walk(root, prog=None):
    """os.walk that never descends into a reparse point (a junction inside a cache folder must not lead the deletion elsewhere).
    Yields (dirpath, real_subdirs, link_subdirs, files); the root itself must be a real folder inside its allowed area.
    prog: the preview job, whose dirs count every folder walked (Temp holds tens of thousands of empty ones)."""
    if _is_reparse(root):
        return
    for dp, dn, fn in os.walk(root, topdown=True, followlinks=False):
        links = [d for d in dn if _is_reparse(os.path.join(dp, d))]
        dn[:] = [d for d in dn if d not in links]
        if prog is not None:
            prog["dirs"] = prog.get("dirs", 0) + 1
        yield dp, list(dn), links, fn


def _old_files(root, days=None, prog=None, dirs=None):
    """Files under root unchanged for days, walked top-down. dirs: a list that receives (folder, mtime) for every folder walked below
    root, the mtime read before any of its files is yielded (deleting them would make the folder look freshly changed)."""
    cut = time.time() - (MIN_AGE_DAYS if days is None else days) * 86400
    for dp, dn, _links, fn in _walk(root, prog):
        if dirs is not None and dp != root:
            try:
                dirs.append((dp, os.stat(dp).st_mtime))
            except OSError:
                errlog.swallowed()
        for f in fn:
            p = os.path.join(dp, f)
            try:
                st = os.stat(p)
                if st.st_mtime < cut:
                    yield p, st.st_size
            except OSError:
                errlog.swallowed()


TEMP_KEEP = ("Low",)   # %TEMP%\Low: the low-integrity processes' temp folder, kept even when empty


def _rm_empty_dirs(root, dirs, days, prog=None):
    """After the files pass: remove the folders it left empty, deepest first, only those unchanged for days (mtime read during the
    walk), never root itself, a link (the walk never yields one) or a kept top-level name. rmdir refuses a folder that is not empty
    or is in use, so a folder something still writes into stays. Temp gathers tens of thousands of these and every walk paid for them."""
    cut = time.time() - days * 86400
    keep = {os.path.normcase(os.path.join(root, k)) for k in TEMP_KEEP}
    n = 0
    try:
        guard = safedel.Root(root)
    except OSError:
        errlog.swallowed()
        return 0
    for d, mt in reversed(dirs):   # the top-down walk order reversed puts every folder after its subfolders
        if mt >= cut or os.path.normcase(d) in keep:
            continue
        try:
            guard.remove(d); n += 1   # بالمقبض: مجلد فارغ فقط، ولا يُتبع رابط زُرع بعد السرد
        except OSError as ex:
            # expected: still holds a file or folder that stays (WinError 145 not empty, 32 in use, 5 denied)
            if getattr(ex, "winerror", None) not in (5, 32, 145):
                errlog.swallowed()
        if prog is not None:
            prog["emptyDirs"] = n
    return n


def _cache_dirs():
    base = os.path.join(LA, "Microsoft", "Edge", "User Data")
    out = []
    if os.path.isdir(base):
        for prof in os.listdir(base):
            if prof == "Default" or prof.startswith("Profile"):
                for sub in ("Cache", "Code Cache", "GPUCache"):
                    p = os.path.join(base, prof, sub)
                    if os.path.isdir(p):
                        out.append(p)
    return out


def _purge_dir(root, prog=None):
    """Delete files one by one; locked/in-use files are skipped (so a running Edge is fine). Returns (bytes freed, files removed, files skipped).
    prog counts files handled and MB freed in this folder only; the caller sums across folders.
    Every deletion goes through safedel (by handle, no reparse point followed between the root and the target)."""
    freed = n = skipped = 0
    try:
        guard = safedel.Root(root)
    except OSError:
        errlog.swallowed()
        return 0, 0, 0
    for dp, dn, links, fn in reversed(list(_walk(root, prog))):
        for f in fn:
            p = os.path.join(dp, f)
            try:
                sz = os.lstat(p).st_size; guard.remove(p); freed += sz; n += 1
            except OSError:
                skipped += 1
        for d in dn + links:   # a link is removed as a link only; its target is never touched
            try:
                guard.remove(os.path.join(dp, d))
            except OSError as ex:
                # expected: the folder still holds a locked/in-use file we skipped (WinError 145 not empty, 32 in use, 5 denied)
                if getattr(ex, "winerror", None) not in (5, 32, 145):
                    errlog.swallowed()
    return freed, n, skipped


def _gb(txt):
    m = re.match(r"([\d.]+)\s*([KMGT]?i?B)", (txt or "").strip(), re.I)
    if not m:
        return 0.0
    v, u = float(m.group(1)), m.group(2).upper()
    return v * {"B": 1 / 2**30, "KB": 1 / 2**20, "KIB": 1 / 2**20, "MB": 1 / 2**10, "MIB": 1 / 2**10, "GB": 1, "GIB": 1, "TB": 1024, "TIB": 1024}.get(u, 0)


def _docker(args, timeout=300):
    return subprocess.run([DOCKER] + args, capture_output=True, text=True, timeout=timeout, creationflags=0x08000000)


def docker_cache(dry):
    """Unused BUILD CACHE only. Never images, containers or volumes."""
    try:
        r = _docker(["system", "df", "--format", "{{json .}}"], 40)
        if r.returncode != 0:
            return {"ok": False, "error": "Docker غير مشغّل"}
        rec = 0.0
        for ln in r.stdout.splitlines():
            d = json.loads(ln)
            if d.get("Type") == "Build Cache":
                rec = _gb(d.get("Reclaimable", "0"))
        if dry:
            return {"ok": True, "dry": True, "gb": round(rec, 3), "mb": round(rec * 1024, 1), "files": 0, "skipped": 0, "note": "داخل Docker؛ يعود للقرص بعد الضغط"}
        r = _docker(["builder", "prune", "-f"], 600)
        m = re.search(r"Total:\s*([\d.]+\s*[KMGT]?i?B)", r.stdout, re.I)
        got = _gb(m.group(1)) if m else 0.0
        return {"ok": r.returncode == 0, "dry": False, "gb": round(got, 3), "mb": round(got * 1024, 1), "files": 0, "skipped": 0, "note": "حُرّر داخل Docker؛ يعود للقرص بعد الضغط"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "انتهت المهلة"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


_wo = {"t": 0, "b": 0, "n": 0, "busy": False, "job": None}


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


_wtask = {"t": 0.0, "ok": False, "why": None}


def _winold_task_ready():
    """The fixed elevated task from the protected install. The server (limited rights) only triggers it; cached 60 s."""
    if time.time() - _wtask["t"] < 60:
        return _wtask["ok"], _wtask["why"]
    ok, why = False, None
    if not secure.installed():
        why = "يلزم التثبيت المحمي: شغّل install_secure_*.ps1 كمسؤول مرة واحدة"
    else:
        try:
            r = subprocess.run(["schtasks", "/query", "/tn", secure.WINOLD_TASKS["normal"]], capture_output=True, timeout=10, creationflags=0x08000000)
            ok = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
            why = "مهمة %s غير مسجّلة؛ أعد تشغيل مثبّت التثبيت المحمي كمسؤول" % secure.WINOLD_TASKS["normal"]
    _wtask.update(t=time.time(), ok=ok, why=why)
    return ok, why


# ---------------- fixed elevated cleanup tasks (wintemp / swdist / dism): the server never runs them itself.
# The owner registers the tasks once with the protected installer, and switches each kind on/off here (setting "space.elevated").
ELEVATED = ("wintemp", "swdist", "dism", "winold", "dwm")   # dwm: مفتاحه في صفحة الرسوميات (gpupurge)
_etask = {}   # task name -> {"t", "ok", "why"} cached 60 s


def _task_ready(task):
    c = _etask.get(task)
    if c and time.time() - c["t"] < 60:
        return c["ok"], c["why"]
    ok, why = False, None
    if not secure.installed():
        why = "يلزم التثبيت المحمي: شغّل install_secure_*.ps1 كمسؤول مرة واحدة"
    else:
        try:
            r = subprocess.run(["schtasks", "/query", "/tn", task], capture_output=True, timeout=10, creationflags=0x08000000)
            ok = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
            why = "مهمة %s غير مسجّلة؛ أعد تشغيل مثبّت التثبيت المحمي كمسؤول" % task
    _etask[task] = {"t": time.time(), "ok": ok, "why": why}
    return ok, why


def get_elevated():
    """Owner switches per elevated kind (default on). Off = the server refuses to trigger that task."""
    a = _setting("space.elevated") or {}
    return {k: bool(a.get(k, True)) for k in ELEVATED}


def set_elevated(kind, on):
    if kind not in ELEVATED:
        return False
    a = get_elevated(); a[kind] = bool(on)
    _setting_set("space.elevated", a)
    return True


def elevated_state(kind):
    """{"on", "ready", "why", "task"} for the UI: switch state and whether the fixed task exists."""
    task = secure.WINOLD_TASKS["normal"] if kind == "winold" else secure.CLEAN_TASKS.get(kind)
    ok, why = (_winold_task_ready() if kind == "winold" else _task_ready(task)) if task else (False, "غير معروف")
    return {"on": get_elevated().get(kind, False), "ready": ok, "why": why, "task": task}


def elevated_gate(kind):
    """None when the kind may be triggered now, else the error dict to return to the caller."""
    if not get_elevated().get(kind, False):   # the owner's switch is checked first: nothing is queried or started
        return {"ok": False, "error": "معطّل من الإعدادات (مفتاح الصلاحية المرتفعة)", "disabled": True}
    st = elevated_state(kind)
    if not st["ready"]:
        return {"ok": False, "error": st["why"] or "المهمة غير متاحة", "task": st["task"]}
    return None


def run_fixed_task(task, result, timeout_s):
    """Trigger a fixed elevated task and wait for a fresh result file (state != running). Returns (out, error)."""
    old = os.path.getmtime(result) if os.path.exists(result) else 0
    try:
        r = subprocess.run(["schtasks", "/run", "/tn", task], capture_output=True, timeout=15, creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired):
        return None, "تعذّر تشغيل المهمة %s" % task
    if r.returncode != 0:
        return None, "تعذّر تشغيل المهمة %s" % task
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(2)
        if os.path.exists(result) and os.path.getmtime(result) > old:
            try:
                with open(result, encoding="utf-8-sig") as fh:
                    out = json.load(fh)
            except (OSError, ValueError):
                continue
            if out.get("state") != "running":
                return out, None
    return None, "انتهت مهلة المهمة بلا نتيجة"


def elevated_clean(kind, timeout_s=240):
    """Synchronous whitelisted cleanup through its fixed task (wintemp / swdist). Deletes nothing in this process."""
    e = elevated_gate(kind)
    if e:
        return e
    out, err = run_fixed_task(secure.CLEAN_TASKS[kind], secure.clean_result(kind), timeout_s)
    if out is None:
        return {"ok": False, "error": err}
    r = {"ok": bool(out.get("ok")), "dry": False, "mb": round(float(out.get("mb") or 0), 1), "files": int(out.get("files") or 0), "skipped": int(out.get("skipped") or 0),
         "lockedMB": 0, "heldMB": 0, "elevated": True}
    r["gb"] = round(r["mb"] / 1024, 3)
    if out.get("error"):
        r["error"] = out.get("error")
    return r


def winold_status():
    """Age gate for Windows.old: deleting it removes the rollback to the previous Windows, so it is allowed only WINOLD_DAYS after creation."""
    if not os.path.isdir(WINOLD):
        return {"present": False}
    if _is_reparse(WINOLD):
        return {"present": True, "unsafe": True}
    age = (time.time() - os.stat(WINOLD).st_ctime) / 86400
    return {"present": True, "ageDays": round(age, 1), "eligible": age >= WINOLD_DAYS, "daysLeft": max(0, int(WINOLD_DAYS - age + 0.999)), "task": _winold_task_ready()[0], "job": _wo["job"]}


def _winold_size():
    if time.time() - _wo["t"] > 600 and not _wo["busy"]:
        _wo["busy"] = True
        def run():
            try:
                b, n = _size(WINOLD, time.time() + 900, dedupe=True)
                _wo.update(b=b, n=n, t=time.time())
            except Exception:
                errlog.swallowed()
            finally:
                _wo["busy"] = False
        threading.Thread(target=run, daemon=True).start()
    return _wo["b"], _wo["n"]


def _winold_delete(src, early=False):
    """Background: trigger the fixed elevated task (normal or early) and wait for its result file. Re-checks the gate; deletes nothing itself."""
    t0 = time.time()
    try:
        st = winold_status()
        if not st.get("present") or st.get("unsafe") or not (st.get("eligible") or early):
            _wo["job"] = {"state": "refused", "ts": int(t0)}
            return
        task = secure.WINOLD_TASKS["early" if early else "normal"]
        res = secure.WINOLD_RESULT
        old = os.path.getmtime(res) if os.path.exists(res) else 0
        nw = 0x08000000
        r = subprocess.run(["schtasks", "/run", "/tn", task], capture_output=True, timeout=15, creationflags=nw)
        if r.returncode != 0:
            _wo["job"] = {"state": "error", "error": "تعذّر تشغيل المهمة %s" % task, "ts": int(t0)}
            return
        _wo["job"] = {"state": "running", "ts": int(t0)}
        out = None
        deadline = t0 + 3 * 3600 + 120
        while time.time() < deadline:
            time.sleep(5)
            if os.path.exists(res) and os.path.getmtime(res) > old:
                try:
                    with open(res, encoding="utf-8-sig") as fh:
                        out = json.load(fh)
                except (OSError, ValueError):
                    continue
                if out.get("state") != "running":
                    break
                out = None
        if out is None:
            _wo["job"] = {"state": "error", "error": "انتهت مهلة المهمة بلا نتيجة", "ts": int(t0)}
            return
        if out.get("state") == "refused":
            _wo["job"] = {"state": "refused", "error": out.get("error"), "ts": int(t0)}
            return
        left = bool(out.get("left")) or out.get("state") == "partial"
        mb = float(out.get("mb") or 0)
        with db._lock:
            c = _c()
            c.execute("INSERT INTO space_runs(ts,kind,mb,files,skipped,src) VALUES(?,?,?,?,?,?)", (int(time.time()), "winold", mb, 0, 1 if left else 0, src))
            c.execute("INSERT INTO actions(ts,kind,src,expected_mb,actual_mb,files,skipped,free_before_gb,free_after_gb,ok,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (int(time.time()), "winold", src, round(_wo["b"] / 2**20, 1), mb, 0, 1 if left else 0, out.get("freeBeforeGB"), out.get("freeAfterGB"),
                       0 if (left or out.get("state") == "error") else 1, out.get("error")))
            c.commit()
        _wo["job"] = {"state": out.get("state") if out.get("state") in ("done", "partial", "error") else ("partial" if left else "done"), "mb": mb,
                      "error": out.get("error"), "ts": int(time.time())}
        _wo["t"] = 0
        threading.Thread(target=scan, args=(60,), daemon=True).start()
    except Exception as e:
        errlog.swallowed()
        _wo["job"] = {"state": "error", "error": str(e)[:80], "ts": int(t0)}


def _tick(prog, files, freed):
    if prog is not None:
        prog["files"] = files
        prog["mb"] = round(freed / 2**20, 1)


_SWAP_RE = re.compile(r"\{?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}?", re.I)


def _wsl_swap(path):
    """WSL's swap file TEMP\\<GUID>\\swap.vhdx: recreated at every WSL start, so holding it 7 days only filled the quarantine
    (18 of 18.4 GB held on 20/11). It is deleted at once like a small file; while WSL runs it is in use and simply skipped."""
    parent = os.path.dirname(path)
    return (os.path.basename(path).lower() == "swap.vhdx" and _SWAP_RE.fullmatch(os.path.basename(parent)) is not None
            and os.path.normcase(os.path.dirname(parent)) == os.path.normcase(TEMP))


def _clean_impl(kind, dry=True, src="manual", early=False, prog=None):
    """dry=True only measures. Real deletion is limited to the whitelist; failures (files in use) are skipped.
    prog: a dict the walks update with folders walked, files handled and MB found or freed so far (the background preview and clean jobs)."""
    freed = n = skipped = locked = held = nd = 0
    qkinds = _setting("space.quarantine_kinds", ["temp"]) if not dry else []
    if kind == "temp":
        dirs = None if dry else []
        guard = None
        if not dry:
            try:
                guard = safedel.Root(TEMP)
            except OSError:
                errlog.swallowed()
                return {"ok": False, "error": "مجلد المؤقتات يمرّ بنقطة إعادة توجيه؛ لم يُحذف شيء"}
        # ما استرجعه المالك من الحجر خلال 30 يومًا لا يُحجر ولا يُحذف ثانيةً ما دام هو الملف نفسه (الهوية)؛ ملف جديد بالاسم نفسه يُنظَّف
        keep = _restored_paths()
        for p, sz in _old_files(TEMP, days=1, prog=prog, dirs=dirs):  # الملفات المؤقتة: أقدم من يوم واحد
            if keep and _is_restored(p, keep):
                continue
            if dry:
                if _deletable(p):
                    n += 1; freed += sz
                else:
                    skipped += 1; locked += sz
                _tick(prog, n + skipped, freed)
                continue
            if kind in qkinds and sz >= Q_MIN_BYTES and not _wsl_swap(p):
                try:
                    quarantine_move(p); held += sz; n += 1
                except Exception:
                    skipped += 1
                continue
            n += 1; freed += sz
            try:
                guard.remove(p)
            except OSError:
                freed -= sz; n -= 1; skipped += 1
            _tick(prog, n + skipped, freed + held)
        if not dry:
            nd = _rm_empty_dirs(TEMP, dirs, 1, prog)   # ثم المجلدات الفارغة القديمة
    elif kind == "edgecache":
        for d in _cache_dirs():
            if dry:
                for dp, dn, _links, fn in _walk(d, prog):
                    for f in fn:
                        pp = os.path.join(dp, f)
                        try:
                            sz = os.path.getsize(pp)
                        except OSError:
                            continue
                        if _deletable(pp):
                            freed += sz; n += 1
                        else:
                            skipped += 1; locked += sz
                        _tick(prog, n + skipped, freed)
            else:
                b, k, sk = _purge_dir(d, prog); freed += b; n += k; skipped += sk
    elif kind == "npmcache":
        p = os.path.join(LA, "npm-cache")
        if os.path.isdir(p):
            if dry:
                freed, n = _size(p, time.time() + 60)
            else:
                freed, n, skipped = _purge_dir(p, prog)
    elif kind == "swdist":
        if os.path.isdir(SWDIST):
            if dry:
                for dp, dn, _links, fn in _walk(SWDIST, prog):
                    for f in fn:
                        pp = os.path.join(dp, f)
                        try:
                            sz = os.path.getsize(pp)
                        except OSError:
                            continue
                        if _deletable(pp):
                            freed += sz; n += 1
                        else:
                            skipped += 1; locked += sz
                        _tick(prog, n + skipped, freed)
            else:
                return elevated_clean("swdist")   # SYSTEM-owned files: only the fixed elevated task may delete them
    elif kind == "winold":
        st = winold_status()
        if not st.get("present"):
            return {"ok": True, "dry": dry, "gb": 0, "mb": 0, "files": 0, "skipped": 0, "note": "غير موجود"}
        if st.get("unsafe"):
            return {"ok": False, "error": "المسار غير آمن"}
        b, nf = _winold_size()
        if dry:
            return {"ok": True, "dry": True, "gb": round(b / 2**30, 3), "mb": round(b / 2**20, 1), "files": nf, "skipped": 0, "lockedMB": 0, "heldMB": 0, "winold": st,
                    "note": "جارٍ القياس" if not _wo["t"] else None, "measuring": not _wo["t"]}
        if not st["eligible"] and not early:
            return {"ok": False, "error": "لم تمضِ %d يوماً بعد؛ متبقٍ %d" % (WINOLD_DAYS, st["daysLeft"]), "blocked": True, "winold": st}
        ready, why = _winold_task_ready()
        if not ready:
            return {"ok": False, "error": why}
        if (_wo["job"] or {}).get("state") == "running":
            return {"ok": False, "error": "الحذف جارٍ"}
        threading.Thread(target=_winold_delete, args=(src, early), daemon=True).start()
        return {"ok": True, "dry": False, "started": True, "gb": 0, "mb": 0, "files": 0, "skipped": 0, "note": "بدأ الحذف في الخلفية؛ قد يستغرق دقائق"}
    elif kind == "dockercache":
        return docker_cache(dry)
    else:
        import extras
        if kind in extras.KINDS:
            # the clean job carries the volumes the owner confirmed one by one (dockervolumes); a preview carries none
            return extras.run(kind, dry, src, sel=(prog or {}).get("volumes"))
        return {"ok": False, "error": "غير مسموح"}
    if not dry:
        try:
            with db._lock:
                c = _c(); c.execute("INSERT INTO space_runs(ts,kind,mb,files,skipped,src) VALUES(?,?,?,?,?,?)", (int(time.time()), kind, round(freed / 2**20, 1), n, skipped, src)); c.commit()
        except Exception:
            errlog.swallowed()
        threading.Thread(target=scan, args=(60,), daemon=True).start()
    return {"ok": True, "dry": dry, "gb": round(freed / 2**30, 3), "mb": round(freed / 2**20, 1), "files": n, "skipped": skipped, "lockedMB": round(locked / 2**20, 1), "heldMB": round(held / 2**20, 1), "emptyDirs": nd}
