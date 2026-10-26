# -*- coding: utf-8 -*-
"""Additional, strictly whitelisted cleanups for the space page (added 2026-10-24).
Every kind: dry-run first, real run audited by space.clean() (actions table). Nothing here touches user documents.
Kinds: light folder caches (pip, crash dumps, Windows\\Temp, PyCharm, new Outlook web cache, Claude VM bundles),
Docker (dangling images, anonymous volumes, VHDX compaction), DISM component cleanup (never /ResetBase),
archiving of old Claude transcripts (zip, verified, then remove), a report-only duplicate/large-file scan,
and a read-only list of the biggest memory consumers."""
import csv
import ctypes
import glob
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from ctypes import wintypes

import db
import errlog
import space

H = space.H
LA = space.LA
APPDATA = os.environ.get("APPDATA", os.path.join(H, "AppData", "Roaming"))
PD = os.environ.get("ProgramData", r"C:\ProgramData")
NW = 0x08000000  # CREATE_NO_WINDOW


def _g(pattern):
    return sorted(glob.glob(pattern))


# key -> spec. roots(): folders whose FILES may be removed; days: only files not modified for that long (0 = all, in-use files skipped);
# guard: processes that must be closed first; auto: may be switched to the hourly auto-clean; admin: needs the elevated server.
LIGHT = {
    "pipcache": dict(label="ذاكرة pip", roots=lambda: [os.path.join(LA, "pip", "Cache")], days=0, guard=(), auto=True, admin=False,
                     note="حزم بايثون المنزّلة؛ تُعاد عند الحاجة."),
    "crashdumps": dict(label="تقارير الأعطال", roots=lambda: [os.path.join(LA, "CrashDumps"), os.path.join(PD, "Microsoft", "Windows", "WER", "ReportArchive"),
                                                               os.path.join(PD, "Microsoft", "Windows", "WER", "ReportQueue"), os.path.join(LA, "Microsoft", "Windows", "WER")],
                       days=7, guard=(), auto=True, admin=False, note="ملفات تشخيص الأعطال الأقدم من 7 أيام."),
    "wintemp": dict(label="Windows\\Temp", roots=lambda: [r"C:\Windows\Temp"], days=3, guard=(), auto=True, admin=True,
                    note="مؤقتات النظام الأقدم من 3 أيام."),
    "pycharm": dict(label="ذاكرة PyCharm", roots=lambda: [p for sub in ("caches", "index", "log", "jcef_cache", "tmp") for p in _g(os.path.join(LA, "JetBrains", "PyCharm*", sub))],
                    days=0, guard=("pycharm64.exe",), auto=False, admin=False, note="الفهارس والذاكرة المؤقتة؛ يعيد PyCharm بناءها (فهرسة أولى أبطأ). أغلقه أولاً."),
    "olkcache": dict(label="ذاكرة Outlook الجديد", roots=lambda: [os.path.join(LA, "Microsoft", "Olk", "EBWebView", "Default", s) for s in ("Cache", "Code Cache", "GPUCache")],
                     days=0, guard=("olk.exe",), auto=False, admin=False, note="ذاكرة عرض الويب فقط؛ لا تُمسّ الرسائل ولا المرفقات. أغلق Outlook أولاً."),
    "wingetcache": dict(label="تنزيلات WinGet", roots=lambda: [os.path.join(LA, "Temp", "WinGet")], days=1, guard=("winget.exe",), auto=False, admin=False,
                        note="مثبّتات منزّلة مؤقتاً؛ لا تُحذف الحزم المثبّتة."),
    "claudevm": dict(label="أقراص Claude الافتراضية (vm_bundles)", roots=lambda: [os.path.join(APPDATA, "Claude", "vm_bundles")], days=0, guard=("claude.exe", "Claude.exe"),
                     auto=False, admin=False, note="يعيد التطبيق تنزيلها عند استخدام الميزة التي تحتاجها. أغلق تطبيق Claude تماماً قبل التنفيذ."),
}
DOCKER_KINDS = ("dockerimages", "dockervolumes", "dockercompact")
JOB_KINDS = ("dism", "claudearchive", "dockercompact")
REPORT_KIND = "dupes"
KINDS = tuple(LIGHT) + DOCKER_KINDS + ("dism", "claudearchive")
AUTO_OK = tuple(k for k, s in LIGHT.items() if s["auto"])
MANUAL_LABEL = {
    "dockerimages": ("صور Docker المعلّقة (dangling)", "طبقات يتيمة بلا اسم؛ لا تُحذف الصور المسمّاة ولا الحاويات."),
    "dockervolumes": ("وحدات تخزين Docker المجهولة", "الوحدات المجهولة غير المرتبطة بأي حاوية فقط؛ لا تُحذف الوحدات المسمّاة أبداً."),
    "dockercompact": ("ضغط قرص Docker الافتراضي", "يُغلق Docker وWSL مؤقتاً ثم يُضغط القرص الافتراضي لتعود المساحة إلى C. يوقف الحاويات الجارية."),
    "dism": ("تنظيف مكوّنات ويندوز (DISM)", "StartComponentCleanup فقط؛ لا /ResetBase ولا يُلغى التراجع عن التحديثات. يستغرق دقائق."),
    "claudearchive": ("أرشفة سجلات Claude القديمة", "تُضغط جلسات .claude الأقدم من 60 يوماً في أرشيف zip مُتحقَّق منه ثم تُزال الأصول؛ لا حذف بلا أرشفة."),
}
KIND_LABELS = {k: s["label"] for k, s in LIGHT.items()}
KIND_LABELS.update({k: v[0] for k, v in MANUAL_LABEL.items()})
ARCHIVE_DAYS = 60
ARCHIVE_DIR = os.path.join(H, ".claude", "archive")
CLAUDE_PROJECTS = os.path.join(H, ".claude", "projects")
DOCKER_VHDX = os.path.join(LA, "Docker", "wsl", "disk", "docker_data.vhdx")

JOBS = {}          # kind -> {"state": running|done|partial|error|refused, ...}
_cache = {}        # kind -> (ts, result) for the slow dry-runs


# ---------------- helpers
_proc = {"t": 0, "names": set()}


def _running_names():
    if time.time() - _proc["t"] > 4:
        try:
            r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20, creationflags=NW)
            _proc["names"] = {ln.split('","')[0].strip('"').lower() for ln in r.stdout.splitlines() if ln}
            _proc["t"] = time.time()
        except Exception:
            errlog.swallowed()
    return _proc["names"]


def _blocked_by(guard):
    run = _running_names()
    return [g for g in guard if g.lower() in run]


def _free():
    return shutil.disk_usage("C:\\").free


def _audit(kind, src, expected_mb, actual_mb, files, skipped, fb, fa, ok, detail=None):
    try:
        with db._lock:
            c = space._c()
            c.execute("INSERT INTO space_runs(ts,kind,mb,files,skipped,src) VALUES(?,?,?,?,?,?)", (int(time.time()), kind, actual_mb, files, skipped, src))
            c.execute("INSERT INTO actions(ts,kind,src,expected_mb,actual_mb,files,skipped,free_before_gb,free_after_gb,ok,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (int(time.time()), kind, src + "-job", expected_mb, actual_mb, files, skipped, round(fb / 2**30, 2), round(fa / 2**30, 2), 1 if ok else 0, detail))
            c.commit()
    except Exception:
        errlog.swallowed()


def _admin_or_err():
    if not space._is_admin():
        return {"ok": False, "error": "يتطلب تشغيل الخادم بصلاحية المسؤول"}
    return None


def _res(dry, mb, files=0, skipped=0, **kw):
    d = {"ok": True, "dry": dry, "gb": round(mb / 1024, 3), "mb": round(mb, 1), "files": files, "skipped": skipped, "lockedMB": 0, "heldMB": 0}
    d.update(kw)
    return d


# ---------------- light folder caches
def _light(kind, dry, src):
    s = LIGHT[kind]
    blk = _blocked_by(s["guard"])
    if s["admin"] and not dry and not space._is_admin():
        return {"ok": False, "error": "يتطلب تشغيل الخادم بصلاحية المسؤول"}
    if blk and not dry:
        return {"ok": False, "error": "أغلق %s أولاً" % "، ".join(blk), "blocked": True}
    freed = n = skipped = locked = 0
    cut = time.time() - s["days"] * 86400
    for root in s["roots"]():
        if not os.path.isdir(root) or space._is_reparse(root):
            continue
        for dp, dn, links, fn in reversed(list(space._walk(root))):
            for f in fn:
                p = os.path.join(dp, f)
                try:
                    st = os.lstat(p)
                    if s["days"] and st.st_mtime >= cut:
                        continue
                    if dry:
                        if space._deletable(p):
                            freed += st.st_size; n += 1
                        else:
                            skipped += 1; locked += st.st_size
                    else:
                        os.remove(p); freed += st.st_size; n += 1
                except OSError:
                    skipped += 1
            if not dry:
                for d in dn + links:
                    try:
                        os.rmdir(os.path.join(dp, d))
                    except OSError:
                        pass   # not empty (skipped files) or in use: expected
    if not dry:
        threading.Thread(target=space.scan, args=(60,), daemon=True).start()
        try:
            with db._lock:
                c = space._c(); c.execute("INSERT INTO space_runs(ts,kind,mb,files,skipped,src) VALUES(?,?,?,?,?,?)", (int(time.time()), kind, round(freed / 2**20, 1), n, skipped, src)); c.commit()
        except Exception:
            errlog.swallowed()
    r = _res(dry, freed / 2**20, n, skipped)
    r["lockedMB"] = round(locked / 2**20, 1)
    if blk:
        r["blockedBy"] = blk
    return r


# ---------------- Docker
def _docker_up():
    try:
        r = space._docker(["version", "--format", "{{.Server.Version}}"], 25)
        return r.returncode == 0 and bool(r.stdout.strip()), r.stdout.strip()
    except Exception:
        return False, ""


def _docker_images_list():
    r = space._docker(["images", "-f", "dangling=true", "--format", "{{.ID}}|{{.Size}}"], 40)
    ids, tot = [], 0.0
    for ln in r.stdout.splitlines():
        if "|" in ln:
            i, s = ln.split("|", 1); ids.append(i.strip()); tot += space._gb(s)
    return sorted(set(ids)), tot


def _docker_volumes_list():
    """Anonymous (64-hex name), unlinked volumes only, with their sizes from `docker system df -v`."""
    r = space._docker(["system", "df", "-v", "--format", "{{json .}}"], 90)
    out = []
    try:
        d = json.loads(r.stdout.strip().splitlines()[0])
        for v in d.get("Volumes") or []:
            name = v.get("Name", "")
            if re.fullmatch(r"[0-9a-f]{64}", name) and str(v.get("Links", "1")) == "0":
                out.append((name, space._gb(v.get("Size", "0"))))
        return out
    except Exception:
        pass
    # older CLI without json for -v: names from `volume ls`, size unknown
    r = space._docker(["volume", "ls", "-f", "dangling=true", "--format", "{{.Name}}"], 40)
    return [(n.strip(), 0.0) for n in r.stdout.splitlines() if re.fullmatch(r"[0-9a-f]{64}", n.strip())]


def _dockerimages(dry, src):
    up, _ = _docker_up()
    if not up:
        return {"ok": False, "error": "Docker غير مشغّل"}
    ids, gb = _docker_images_list()
    if dry:
        return _res(True, gb * 1024, len(ids), note="داخل Docker؛ تعود للقرص بعد ضغط القرص الافتراضي")
    done = 0
    for i in ids:
        r = space._docker(["rmi", i], 120)   # plain rmi: refuses anything still referenced
        if r.returncode == 0:
            done += 1
    return _res(False, gb * 1024, done, len(ids) - done, note="حُرّر داخل Docker؛ يعود للقرص بعد الضغط")


def _dockervolumes(dry, src):
    up, ver = _docker_up()
    if not up:
        return {"ok": False, "error": "Docker غير مشغّل"}
    vols = _docker_volumes_list()
    gb = sum(s for _, s in vols)
    if dry:
        return _res(True, gb * 1024, len(vols), note="داخل Docker؛ المجهولة غير المرتبطة فقط")
    done = 0
    for name, _s in vols:
        r = space._docker(["volume", "rm", name], 60)   # refuses a volume that is in use
        if r.returncode == 0:
            done += 1
    return _res(False, gb * 1024, done, len(vols) - done, note="حُرّر داخل Docker؛ يعود للقرص بعد الضغط")


def _dockercompact_estimate():
    up, _ = _docker_up()
    if not up or not os.path.isfile(DOCKER_VHDX):
        return None
    vh = os.path.getsize(DOCKER_VHDX) / 2**30
    r = space._docker(["system", "df", "--format", "{{json .}}"], 40)
    used = 0.0
    for ln in r.stdout.splitlines():
        try:
            used += space._gb(json.loads(ln).get("Size", "0"))
        except Exception:
            pass
    return max(0.0, vh - used - 1.0)   # 1 GB slack for the ext4 metadata


def _dockercompact(dry, src):
    if not os.path.isfile(DOCKER_VHDX):
        return {"ok": False, "error": "قرص Docker الافتراضي غير موجود"}
    if dry:
        est = _dockercompact_estimate()
        running = 0
        try:
            r = space._docker(["ps", "-q"], 20)
            running = len([x for x in r.stdout.splitlines() if x.strip()])
        except Exception:
            pass
        return _res(True, (est or 0) * 1024, 0, 0, note=("يوقف %d حاوية جارية" % running) if running else "لا حاويات جارية",
                    containers=running, estimated=True, vhdxGB=round(os.path.getsize(DOCKER_VHDX) / 2**30, 1))
    e = _admin_or_err()
    if e:
        return e
    if (JOBS.get("dockercompact") or {}).get("state") == "running":
        return {"ok": False, "error": "الضغط جارٍ"}
    threading.Thread(target=_dockercompact_job, args=(src,), daemon=True).start()
    return {"ok": True, "dry": False, "started": True, "gb": 0, "mb": 0, "files": 0, "skipped": 0, "note": "بدأ ضغط القرص في الخلفية؛ سيُغلق Docker مؤقتاً"}


def _dockercompact_job(src):
    t0 = time.time(); JOBS["dockercompact"] = {"state": "running", "ts": int(t0), "step": "إيقاف Docker"}
    fb = _free(); b0 = os.path.getsize(DOCKER_VHDX)
    try:
        est = _dockercompact_estimate()
        subprocess.run([space.DOCKER, "desktop", "stop"], capture_output=True, timeout=300, creationflags=NW)
        JOBS["dockercompact"]["step"] = "إيقاف WSL"
        subprocess.run(["wsl", "--shutdown"], capture_output=True, timeout=120, creationflags=NW)
        time.sleep(8)
        JOBS["dockercompact"]["step"] = "ضغط القرص"
        script = os.path.join(os.environ.get("TEMP", LA), "mw_compact_%d.txt" % int(t0))
        with open(script, "w", encoding="ascii") as f:
            f.write('select vdisk file="%s"\nattach vdisk readonly\ncompact vdisk\ndetach vdisk\nexit\n' % DOCKER_VHDX)
        r = subprocess.run(["diskpart", "/s", script], capture_output=True, text=True, timeout=5400, creationflags=NW)
        try:
            os.remove(script)
        except OSError:
            pass
        ok = r.returncode == 0 and "100 percent" in (r.stdout or "").lower()
        b1 = os.path.getsize(DOCKER_VHDX); fa = _free()
        mb = round(max(0, b0 - b1) / 2**20, 1)
        _audit("dockercompact", src, round((est or 0) * 1024, 1), mb, 0, 0 if ok else 1, fb, fa, ok, None if ok else (r.stdout or "")[-120:].strip())
        JOBS["dockercompact"] = {"state": "done" if ok else "partial", "mb": mb, "ts": int(time.time()), "note": "افتح Docker Desktop من جديد عند الحاجة"}
    except Exception as e:
        errlog.swallowed()
        JOBS["dockercompact"] = {"state": "error", "error": str(e)[:80], "ts": int(t0)}
    finally:
        threading.Thread(target=space.scan, args=(60,), daemon=True).start()


# ---------------- DISM component store (StartComponentCleanup only)
_dism = {"t": 0, "mb": 0.0, "recommended": None, "busy": False}


def _dism_analyze_bg():
    if _dism["busy"] or time.time() - _dism["t"] < 3600:
        return
    _dism["busy"] = True

    def run():
        try:
            r = subprocess.run(["dism", "/Online", "/Cleanup-Image", "/AnalyzeComponentStore"], capture_output=True, text=True, timeout=900, creationflags=NW)
            t = r.stdout or ""
            rec = re.search(r"Component Store Cleanup Recommended\s*:\s*(\w+)", t)
            cache = re.search(r"Cache and Temporary Data\s*:\s*([\d.]+\s*\w+)", t)
            back = re.search(r"Backups and Disabled Features\s*:\s*([\d.]+\s*\w+)", t)
            _dism.update(recommended=(rec.group(1).lower() == "yes") if rec else None, t=time.time(),
                         mb=round((space._gb(cache.group(1)) if cache else 0) * 1024, 1), backupsGB=round(space._gb(back.group(1)) if back else 0, 2))
        except Exception:
            errlog.swallowed()
        finally:
            _dism["busy"] = False
    threading.Thread(target=run, daemon=True).start()


def _dismkind(dry, src):
    e = _admin_or_err()
    if e:
        return e
    if dry:
        _dism_analyze_bg()
        return _res(True, _dism["mb"], 0, 0, note="جارٍ التحليل…" if not _dism["t"] else ("يُنصح بالتنظيف" if _dism["recommended"] else "لا يُنصح به الآن"),
                    measuring=not _dism["t"], recommended=_dism["recommended"])
    if (JOBS.get("dism") or {}).get("state") == "running":
        return {"ok": False, "error": "التنظيف جارٍ"}
    threading.Thread(target=_dism_job, args=(src,), daemon=True).start()
    return {"ok": True, "dry": False, "started": True, "gb": 0, "mb": 0, "files": 0, "skipped": 0, "note": "بدأ التنظيف في الخلفية؛ قد يستغرق دقائق"}


def _dism_job(src):
    t0 = time.time(); JOBS["dism"] = {"state": "running", "ts": int(t0)}
    fb = _free()
    try:
        r = subprocess.run(["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], capture_output=True, text=True, timeout=5400, creationflags=NW)
        ok = r.returncode == 0
        fa = _free(); mb = round(max(0, fa - fb) / 2**20, 1)
        _audit("dism", src, _dism["mb"], mb, 0, 0 if ok else 1, fb, fa, ok, None if ok else (r.stdout or "")[-120:].strip())
        JOBS["dism"] = {"state": "done" if ok else "error", "mb": mb, "ts": int(time.time())}
        _dism["t"] = 0
    except Exception as e:
        errlog.swallowed()
        JOBS["dism"] = {"state": "error", "error": str(e)[:80], "ts": int(t0)}
    finally:
        threading.Thread(target=space.scan, args=(60,), daemon=True).start()


def dism_monthly():
    """Agent job: StartComponentCleanup at most every 30 days, only when the owner enabled it and the server is elevated."""
    if not space._setting("space.dism_monthly", False) or not space._is_admin():
        return "off"
    with db._lock:
        r = space._c().execute("SELECT MAX(ts) FROM actions WHERE kind='dism' AND ok=1 AND actual_mb IS NOT NULL").fetchone()
    if r and r[0] and time.time() - r[0] < 30 * 86400:
        return "not due"
    if (JOBS.get("dism") or {}).get("state") == "running":
        return "running"
    space.clean("dism", dry=False, src="auto")
    return "started"


# ---------------- Claude transcript archive (zip -> verify -> remove originals)
def _archive_candidates():
    cut = time.time() - ARCHIVE_DAYS * 86400
    out = []
    if not os.path.isdir(CLAUDE_PROJECTS) or space._is_reparse(CLAUDE_PROJECTS):
        return out
    for dp, dn, links, fn in space._walk(CLAUDE_PROJECTS):
        dn[:] = [d for d in dn if d.lower() != "memory"]
        for f in fn:
            if f.lower().endswith(".jsonl"):
                p = os.path.join(dp, f)
                try:
                    st = os.lstat(p)
                    if st.st_mtime < cut:
                        out.append((p, st.st_size, st.st_mtime))
                except OSError:
                    pass
    return out


def _archivekind(dry, src):
    c = _archive_candidates()
    tot = sum(s for _, s, _ in c)
    if dry:
        return _res(True, tot * 0.8 / 2**20, len(c), 0, note="تقدير بعد الضغط؛ الأصول تُزال بعد التحقق من الأرشيف", estimated=True, rawMB=round(tot / 2**20, 1))
    if (JOBS.get("claudearchive") or {}).get("state") == "running":
        return {"ok": False, "error": "الأرشفة جارية"}
    threading.Thread(target=_archive_job, args=(src, c), daemon=True).start()
    return {"ok": True, "dry": False, "started": True, "gb": 0, "mb": 0, "files": 0, "skipped": 0, "note": "بدأت الأرشفة في الخلفية"}


def _archive_job(src, cands):
    t0 = time.time(); JOBS["claudearchive"] = {"state": "running", "ts": int(t0)}
    fb = _free(); removed = kept = 0; raw = 0
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        groups = {}
        for p, sz, mt in cands:
            groups.setdefault(time.strftime("%Y%m", time.localtime(mt)), []).append((p, sz))
        for ym, items in sorted(groups.items()):
            zp = os.path.join(ARCHIVE_DIR, "transcripts-%s.zip" % ym)
            existing = set()
            if os.path.exists(zp):
                with zipfile.ZipFile(zp) as z:
                    existing = set(z.namelist())
            added = []
            with zipfile.ZipFile(zp, "a", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for p, sz in items:
                    arc = os.path.relpath(p, CLAUDE_PROJECTS).replace("\\", "/")
                    if arc in existing:
                        arc = "%s.%d" % (arc, int(os.path.getmtime(p)))
                    try:
                        z.write(p, arc); added.append((p, sz, arc))
                    except OSError:
                        kept += 1
            with zipfile.ZipFile(zp) as z:     # verify before removing anything
                bad = z.testzip()
                info = {i.filename: i.file_size for i in z.infolist()}
            for p, sz, arc in added:
                if bad is None and info.get(arc) == sz:
                    try:
                        os.remove(p); removed += 1; raw += sz
                    except OSError:
                        kept += 1
                else:
                    kept += 1
        fa = _free(); mb = round(max(0, fa - fb) / 2**20, 1)
        _audit("claudearchive", src, round(sum(s for _, s, _ in cands) * 0.8 / 2**20, 1), mb, removed, kept, fb, fa, kept == 0, None if kept == 0 else "بعض الملفات بقيت")
        JOBS["claudearchive"] = {"state": "done" if kept == 0 else "partial", "mb": mb, "files": removed, "ts": int(time.time()), "note": ARCHIVE_DIR}
    except Exception as e:
        errlog.swallowed()
        JOBS["claudearchive"] = {"state": "error", "error": str(e)[:80], "ts": int(t0)}
    finally:
        threading.Thread(target=space.scan, args=(60,), daemon=True).start()


# ---------------- dispatcher used by space._clean_impl
def run(kind, dry, src):
    if kind in LIGHT:
        return _light(kind, dry, src)
    if kind == "dockerimages":
        return _safe(_dockerimages, dry, src)
    if kind == "dockervolumes":
        return _safe(_dockervolumes, dry, src)
    if kind == "dockercompact":
        return _safe(_dockercompact, dry, src)
    if kind == "dism":
        return _safe(_dismkind, dry, src)
    if kind == "claudearchive":
        return _archivekind(dry, src)
    return {"ok": False, "error": "غير مسموح"}


def _safe(fn, dry, src):
    try:
        return fn(dry, src)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "انتهت المهلة"}
    except Exception as e:
        errlog.swallowed()
        return {"ok": False, "error": str(e)[:80]}


# ---------------- report only: duplicates, large files, empty folders (nothing is deleted)
_rep = {"busy": False}
REPORT_ROOTS = [os.path.join(H, d) for d in ("Downloads", "Documents", "Desktop", "Videos", "Pictures", "Music")]
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "site-packages", ".idea"}
MIN_DUP = 1 * 2**20
LARGE = 200 * 2**20


def _hash(path, limit=None):
    h = hashlib.blake2b(digest_size=16)
    try:
        with open(path, "rb") as f:
            if limit:
                h.update(f.read(limit))
            else:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
        return h.hexdigest()
    except OSError:
        return None


def _report_job(budget=1200):
    deadline = time.time() + budget
    try:
        by_size, empties, large = {}, [], []
        for root in REPORT_ROOTS:
            if not os.path.isdir(root) or space._is_reparse(root):
                continue
            for dp, dn, links, fn in space._walk(root):
                if time.time() > deadline:
                    break
                dn[:] = [d for d in dn if d not in SKIP_DIRS]
                if not dn and not fn and not links:
                    empties.append(dp)
                for f in fn:
                    p = os.path.join(dp, f)
                    try:
                        st = os.lstat(p)
                    except OSError:
                        continue
                    if getattr(st, "st_file_attributes", 0) & space.CLOUD:
                        continue
                    if st.st_size >= LARGE:
                        large.append((st.st_size, p))
                    if st.st_size >= MIN_DUP:
                        by_size.setdefault(st.st_size, []).append(p)
        # large files anywhere under the profile (excluding folders that are handled elsewhere)
        for dp, dn, links, fn in space._walk(H):
            if time.time() > deadline:
                break
            dn[:] = [d for d in dn if d not in SKIP_DIRS and os.path.join(dp, d).lower() not in (os.path.join(H, ".claude").lower(), os.path.join(LA, "Docker").lower(), os.path.join(LA, "Packages").lower())]
            for f in fn:
                p = os.path.join(dp, f)
                try:
                    st = os.lstat(p)
                    if st.st_size >= LARGE and not any(p == q for _, q in large):
                        large.append((st.st_size, p))
                except OSError:
                    pass
        groups = []
        for size, paths in by_size.items():
            if len(paths) < 2:
                continue
            part = {}
            for p in paths:
                h = _hash(p, 262144)
                if h:
                    part.setdefault(h, []).append(p)
            for ps in part.values():
                if len(ps) < 2:
                    continue
                full = {}
                for p in ps:
                    h = _hash(p)
                    if h:
                        full.setdefault(h, []).append(p)
                for fp in full.values():
                    if len(fp) > 1:
                        groups.append({"size": size, "wasted": size * (len(fp) - 1), "paths": fp[:6], "n": len(fp)})
        groups.sort(key=lambda g: -g["wasted"])
        large.sort(reverse=True)
        out = {"ts": int(time.time()), "partial": time.time() > deadline,
               "dups": groups[:30], "dupWastedMB": round(sum(g["wasted"] for g in groups) / 2**20, 1),
               "large": [{"mb": round(s / 2**20, 1), "path": p} for s, p in large[:25]],
               "empty": empties[:40], "emptyCount": len(empties)}
        space._setting_set("space.report", out)
    except Exception:
        errlog.swallowed()
    finally:
        _rep["busy"] = False


def report_start():
    if _rep["busy"]:
        return False
    _rep["busy"] = True
    threading.Thread(target=_report_job, daemon=True).start()
    return True


def report():
    r = space._setting("space.report")
    return {"busy": _rep["busy"], "data": r}


# ---------------- memory consumers (read only; no RAM "cleaner")
_mem = {"t": 0, "v": None, "busy": False}


class _MS(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _mem_refresh():
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20, creationflags=NW)
        agg = {}
        for row in csv.reader(io.StringIO(r.stdout)):
            if len(row) < 5:
                continue
            kb = int(re.sub(r"[^\d]", "", row[4]) or 0)
            a = agg.setdefault(row[0], [0, 0]); a[0] += kb; a[1] += 1
        top = sorted(agg.items(), key=lambda kv: -kv[1][0])[:10]
        ms = _MS(); ms.dwLength = ctypes.sizeof(_MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        _mem["v"] = {"top": [{"name": n, "mb": round(v[0] / 1024), "count": v[1]} for n, v in top],
                     "totalGB": round(ms.ullTotalPhys / 2**30, 1), "usedGB": round((ms.ullTotalPhys - ms.ullAvailPhys) / 2**30, 1), "load": ms.dwMemoryLoad}
    except Exception:
        errlog.swallowed()
    finally:
        _mem["t"] = time.time(); _mem["busy"] = False


def memory_top():
    if time.time() - _mem["t"] > 15 and not _mem["busy"]:
        _mem["busy"] = True
        threading.Thread(target=_mem_refresh, daemon=True).start()
    return _mem["v"]


# ---------------- overview piece
_light_est = {"t": 0, "v": {}, "busy": False}


def _light_refresh():
    try:
        v = {}
        for k in LIGHT:
            r = _light(k, True, "estimate")
            v[k] = {"mb": r.get("mb", 0.0), "locked": r.get("lockedMB", 0.0), "blockedBy": r.get("blockedBy")}
        _light_est["v"] = v
    except Exception:
        errlog.swallowed()
    finally:
        _light_est["t"] = time.time(); _light_est["busy"] = False


def _last_runs():
    try:
        with db._lock:
            rows = space._c().execute("SELECT kind, MAX(ts) ts, actual_mb FROM actions WHERE ok=1 AND src NOT LIKE '%estimate%' AND actual_mb>0 GROUP BY kind").fetchall()
        return {r["kind"]: {"ts": r["ts"], "mb": r["actual_mb"]} for r in rows}
    except Exception:
        return {}


def overview():
    if time.time() - _light_est["t"] > 600 and not _light_est["busy"]:
        _light_est["busy"] = True
        threading.Thread(target=_light_refresh, daemon=True).start()
    est = _light_est["v"]
    last = _last_runs()
    auto = space.get_auto()
    items = []
    for k, s in LIGHT.items():
        e = est.get(k) or {}
        items.append({"key": k, "label": s["label"], "note": s["note"], "mode": "light", "mb": e.get("mb"), "lockedMB": e.get("locked", 0), "blockedBy": e.get("blockedBy"),
                      "auto": auto.get(k) if s["auto"] else None, "admin": s["admin"], "last": last.get(k)})
    for k, (lab, note) in MANUAL_LABEL.items():
        items.append({"key": k, "label": lab, "note": note, "mode": "job" if k in JOB_KINDS else "manual", "mb": None, "auto": None, "last": last.get(k),
                      "job": JOBS.get(k), "monthly": space._setting("space.dism_monthly", False) if k == "dism" else None})
    return {"items": items, "memory": memory_top(), "report": report()}
