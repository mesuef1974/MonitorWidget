# -*- coding: utf-8 -*-
"""تفريغ ذاكرة كرت الشاشة عند الطلب (زر صفحة الرسوميات): يُخرج النماذج المحمّلة في LM Studio (explain.unload)
ويقيس ذاكرة الكرت قبل التفريغ وبعده عبر gpuwatch. لا يُنهي أي عملية ولا يلمس التعريف.
المرور يجري في خيط لأن استعلامَي nvidia-smi وطلب LM Studio يتجاوزان 300 ms معًا: POST يبدأه وGET يعرض حالته.

    ROUTES / post(route, body, invalidate)   POST /api/gpu-purge يبدأ مرورًا واحدًا (409 إن كان مرور جارياً)
    view()                                   حالة آخر تفريغ للواجهة (GET /api/gpu-purge)
    purge()                                  المرور نفسه (يُستدعى في خيط)
"""
import threading
import time

import db
import errlog
import explain
import gpuwatch

ROUTES = ("/api/gpu-purge",)
SETTLE_S = 1.0   # التعريف يحرّر الذاكرة بعد إخراج النموذج بلحظة؛ ننتظرها قبل القياس الثاني
STATE = {"running": False, "ts": None, "beforeMB": None, "afterMB": None, "freedMB": None, "text": None, "err": None}
_lock = threading.Lock()


def _vram():
    """ذاكرة الكرت المستعملة بالميغابايت من القراءة الحية، أو None إن غاب nvidia-smi."""
    try:
        v = gpuwatch.live_info()
        return v.get("vram") if v else None
    except Exception:
        errlog.swallowed()
        return None


def _record(ok, detail):
    try:
        with db._lock:
            c = db.conn()
            c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)", (int(time.time()), "gpupurge", "button", 1 if ok else 0, detail[:200]))
            c.commit()
    except Exception:
        errlog.swallowed()


def purge():
    """مرور واحد: قياس ← إخراج نماذج LM Studio ← قياس. يحدّث STATE ويسجّل في جدول actions."""
    before = _vram()
    try:
        r = explain.unload()
        time.sleep(SETTLE_S)
        gpuwatch.invalidate_live()
        after = _vram()
        freed = None
        if before is not None and after is not None:
            freed = max(0, int(round(before - after)))
        ok = bool(r.get("ok"))
        with _lock:
            STATE.update(ts=int(time.time()), beforeMB=before, afterMB=after, freedMB=freed, text=r.get("text"), err=None if ok else (r.get("err") or "failed"))
        _record(ok, "before=%s after=%s freed=%s %s" % (before, after, freed, r.get("text") or r.get("err") or ""))
    except Exception as e:
        errlog.swallowed()
        with _lock:
            STATE.update(ts=int(time.time()), beforeMB=before, afterMB=None, freedMB=None, text=None, err=str(e)[:140])
        _record(False, "error=" + str(e)[:160])
    finally:
        with _lock:
            STATE["running"] = False


def view():
    with _lock:
        return dict(STATE)


def post(route, body, invalidate=lambda: None):
    """(رمز الحالة، الردّ) لمسار من ROUTES، أو None إن لم يكن المسار من هذه الوحدة."""
    if route != "/api/gpu-purge":
        return None
    with _lock:
        if STATE["running"]:
            return 409, {"error": "gpu purge already running"}
        STATE["running"] = True
        STATE["err"] = None
    threading.Thread(target=purge, daemon=True).start()
    invalidate()
    return 200, {"ok": True}
