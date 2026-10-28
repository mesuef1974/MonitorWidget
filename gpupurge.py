# -*- coding: utf-8 -*-
"""تفريغ ذاكرة كروت الشاشة عند الطلب (زر صفحة الرسوميات)، عام لكل الكروت ومتدرّج:
1. آمن دائمًا: يُخرج النماذج المحمّلة من خدمات الذكاء المحلية (LM Studio عبر explain.unload، وOllama عبر keep_alive=0).
2. بتأكيد منفصل (dwm=True): يعيد تشغيل مدير سطح المكتب dwm عبر المهمة المرتفعة الثابتة MonitorWidget-Gpu-Dwm،
   فيحرّر ما يحجزه من ذاكرة الرسوميات المشتركة (تسرّب تعريف إنتل). تومض الشاشة لحظة ولا يُغلق أي برنامج.
يقيس قبل وبعد: ذاكرة NVIDIA المخصّصة عبر gpuwatch، والذاكرة المشتركة لكل الكروت (ومنها نصيب dwm) عبر عدّادات Windows.
لا يُنهي هذا الملف أي عملية بنفسه ولا يلمس التعريف. المرور يجري في خيط لأنه يتجاوز 300 ms: POST يبدأه وGET يعرض حالته.

    ROUTES / post(route, body, invalidate)   POST /api/gpu-purge {"dwm": bool} يبدأ مرورًا واحدًا (409 إن كان مرور جارياً)
    view()                                   حالة آخر تفريغ للواجهة (GET /api/gpu-purge)
    purge(dwm)                               المرور نفسه (يُستدعى في خيط)
"""
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request

import db
import errlog
import explain
import gpuwatch
import secure
import space

ROUTES = ("/api/gpu-purge",)
SETTLE_S = 1.0   # التعريف يحرّر الذاكرة بعد إخراج النموذج بلحظة؛ ننتظرها قبل القياس الثاني
OLLAMA = "http://127.0.0.1:11434"
DWM_TIMEOUT_S = 60
STATE = {"running": False, "ts": None, "beforeMB": None, "afterMB": None, "freedMB": None, "sharedBeforeMB": None, "sharedAfterMB": None,
         "sharedFreedMB": None, "dwmMB": None, "dwm": False, "steps": [], "text": None, "err": None}
_lock = threading.Lock()
# مجموع الذاكرة المشتركة لكل العمليات على كل الكروت، ونصيب dwm منها (بالميغابايت)؛ العدّادات تعمل بصلاحية عادية
_SHARED_PS = ("$ErrorActionPreference='SilentlyContinue';$s=(Get-Counter '\\GPU Process Memory(*)\\Shared Usage').CounterSamples;"
              "$ids=@(Get-Process dwm|ForEach-Object{$_.Id});$t=0;$d=0;foreach($x in $s){$t+=$x.CookedValue;"
              "if($x.InstanceName -match '^pid_(\\d+)_' -and $ids -contains [int]$matches[1]){$d+=$x.CookedValue}};"
              "'{0} {1}' -f [int]($t/1MB),[int]($d/1MB)")


def _vram():
    """ذاكرة NVIDIA المستعملة بالميغابايت من القراءة الحية، أو None إن غاب nvidia-smi."""
    try:
        v = gpuwatch.live_info()
        return v.get("vram") if v else None
    except Exception:
        errlog.swallowed()
        return None


def _shared():
    """(المشتركة لكل الكروت، نصيب dwm) بالميغابايت، أو (None, None) إن تعذّرت العدّادات."""
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SHARED_PS], capture_output=True, text=True,
                           timeout=30, creationflags=0x08000000)
        a, b = r.stdout.split()[:2]
        return int(a), int(b)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        errlog.swallowed()
        return None, None


def _ollama_unload():
    """يُخرج كل نموذج محمّل في Ollama بطلب keep_alive=0 (الطريقة الرسمية)؛ لا يُنهي الخدمة."""
    try:
        with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=3) as r:
            names = [m.get("name") or m.get("model") for m in (json.load(r).get("models") or []) if isinstance(m, dict)]
    except (urllib.error.URLError, OSError, ValueError):
        return {"ok": True, "text": "Ollama غير مشغّل."}
    names = [n for n in names if n]
    if not names:
        return {"ok": True, "text": "لا نموذج محمّل في Ollama."}
    done = 0
    for n in names:
        try:
            req = urllib.request.Request(OLLAMA + "/api/generate", data=json.dumps({"model": n, "keep_alive": 0}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15):
                done += 1
        except (urllib.error.URLError, OSError):
            errlog.swallowed()
    if done == len(names):
        return {"ok": True, "text": "أُخرج %d نموذج من Ollama." % done}
    return {"ok": False, "err": "تعذّر إخراج %d من نماذج Ollama." % (len(names) - done)}


def _restart_dwm():
    """الخطوة المؤكَّدة: المهمة المرتفعة الثابتة بعد مفتاح المالك وجاهزيتها؛ الخادم لا يُنهي dwm بنفسه."""
    e = space.elevated_gate("dwm")
    if e:
        return {"ok": False, "err": e.get("error") or "المهمة غير متاحة"}
    out, err = space.run_fixed_task(secure.CLEAN_TASKS["dwm"], secure.clean_result("dwm"), DWM_TIMEOUT_S)
    if out is None:
        return {"ok": False, "err": err}
    if not out.get("ok"):
        return {"ok": False, "err": out.get("error") or "تعذّرت إعادة تشغيل مدير سطح المكتب"}
    return {"ok": True, "text": "أُعيد تشغيل مدير سطح المكتب."}


def _record(ok, detail):
    try:
        with db._lock:
            c = db.conn()
            c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)", (int(time.time()), "gpupurge", "button", 1 if ok else 0, detail[:200]))
            c.commit()
    except Exception:
        errlog.swallowed()


def _freed(a, b):
    return max(0, int(round(a - b))) if a is not None and b is not None else None


def purge(dwm=False):
    """مرور واحد: قياس ← إخراج النماذج (LM Studio ثم Ollama) ← [إعادة تشغيل dwm بتأكيد] ← قياس. يحدّث STATE ويسجّل في actions."""
    before = _vram()
    sb, _ = _shared()
    steps = []
    try:
        plan = [("LM Studio", explain.unload), ("Ollama", _ollama_unload)] + ([("dwm", _restart_dwm)] if dwm else [])
        for name, fn in plan:
            try:
                r = fn()
            except Exception as e:
                errlog.swallowed()
                r = {"ok": False, "err": str(e)[:140]}
            steps.append({"name": name, "ok": bool(r.get("ok")), "text": r.get("text") or r.get("err") or ""})
        time.sleep(SETTLE_S)
        gpuwatch.invalidate_live()
        after = _vram()
        sa, dwm_mb = _shared()
        failed = [s for s in steps if not s["ok"]]
        err = (failed[0]["text"] or "failed") if failed else None
        text = " ".join(s["text"] for s in steps if s["ok"] and s["text"])
        with _lock:
            STATE.update(ts=int(time.time()), beforeMB=before, afterMB=after, freedMB=_freed(before, after), sharedBeforeMB=sb, sharedAfterMB=sa,
                         sharedFreedMB=_freed(sb, sa), dwmMB=dwm_mb, dwm=bool(dwm), steps=steps, text=text or None, err=err)
        _record(not failed, "before=%s after=%s shared=%s->%s dwm=%s %s" % (before, after, sb, sa, int(bool(dwm)), " | ".join(s["name"] + ":" + s["text"] for s in steps)))
    except Exception as e:
        errlog.swallowed()
        with _lock:
            STATE.update(ts=int(time.time()), beforeMB=before, afterMB=None, freedMB=None, sharedBeforeMB=sb, sharedAfterMB=None, sharedFreedMB=None,
                         dwm=bool(dwm), steps=steps, text=None, err=str(e)[:140])
        _record(False, "error=" + str(e)[:160])
    finally:
        with _lock:
            STATE["running"] = False


def view():
    with _lock:
        v = dict(STATE)
    v["dwmOn"] = bool(space.get_elevated().get("dwm"))   # مفتاح المالك فقط (إعداد محلي سريع)؛ الجاهزية تظهر عند المحاولة
    return v


def post(route, body, invalidate=lambda: None):
    """(رمز الحالة، الردّ) لمسار من ROUTES، أو None إن لم يكن المسار من هذه الوحدة."""
    if route != "/api/gpu-purge":
        return None
    dwm = bool((body or {}).get("dwm")) if isinstance(body, dict) else False
    with _lock:
        if STATE["running"]:
            return 409, {"error": "gpu purge already running"}
        STATE["running"] = True
        STATE["err"] = None
    threading.Thread(target=purge, args=(dwm,), daemon=True).start()
    invalidate()
    return 200, {"ok": True}
