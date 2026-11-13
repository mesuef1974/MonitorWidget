# -*- coding: utf-8 -*-
"""مراقب الويدجت: يقرأ widget.log (start / layout / ui stall / fatal / exit) فيعرف هل سقط الويدجت أم أُغلق عمدًا،
ويعيد الوكيلُ تشغيله بعد السقوط أو القتل فقط، بحدّ أقصى، ويعرض الأعداد لصفحة الصحة.

    parse(lines, now)      نقية: أعداد 24 ساعة وحالة آخر تشغيل (running-or-killed | crashed | closed | exited | never)
    guard(...)             مهمة الوكيل كل دقيقة: يعيد التشغيل إن سقط الويدجت ولم يطلب المالك إغلاقه
    payload()              لصفحة الصحة: h.widget
    set_wanted(bool)       يسجّلها /api/widget: إخفاء الويدجت من التطبيق قرار، فلا يُعاد تشغيله

متى لا يُعاد التشغيل: «exit: user» («إغلاق الويدجت» من قائمته أو أيقونته)، أو طلب إخفاء من التطبيق،
«exit» المجرّد (إغلاق ويندوز لنافذة معلّقة، أو نهاية الجلسة) يُعاد بعده التشغيل، أو تجاوز MAX_RELAUNCH في الساعة (ويدجت يسقط فور كل بدء لا يُعاد بلا نهاية).
"""
import json
import os
import re
import subprocess
import time

import db
import widgetctl
from paths import CNW, ROOT

LOG = os.path.join(ROOT, "widget.log")
TAIL_BYTES = 256 * 1024
MAX_RELAUNCH = 3          # في الساعة
SETTLE_S = 25             # مهلة بعد آخر سطر: restart_widget.ps1 يقتل ثم يشغّل خلال ثوانٍ
TASK = "MonitorWidget-Launch"
_LINE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d) (start: pid (\d+)|ui stall: ([\d,.]+) s|fatal: (.*)|exit(: user)?$|layout: (.*))")


def _ts(text):
    return int(time.mktime(time.strptime(text, "%Y-%m-%dT%H:%M:%S")))


def parse(lines, now=None, window=86400):
    now = now or time.time()
    out = {"starts": 0, "fatals": 0, "stalls": 0, "stallMaxS": 0.0, "stallSumS": 0.0, "lastStart": None, "lastLine": None,
           "lastFatal": None, "lastLayout": None, "state": "never", "pid": None}
    fatal_after = exit_after = user_exit = False
    for line in lines:
        m = _LINE.match(line)
        if not m:
            continue
        try:
            ts = _ts(m.group(1))
        except ValueError:
            continue
        kind = m.group(2)
        recent = now - ts <= window
        out["lastLine"] = ts
        if kind.startswith("start:"):
            out["lastStart"], out["pid"] = ts, int(m.group(3))
            fatal_after = exit_after = user_exit = False
            out["starts"] += recent
        elif kind.startswith("ui stall:"):
            sec = float(m.group(4).replace(",", ""))
            if recent:
                out["stalls"] += 1
                out["stallSumS"] = round(out["stallSumS"] + sec, 1)
                out["stallMaxS"] = max(out["stallMaxS"], sec)
        elif kind.startswith("fatal:"):
            fatal_after = True
            out["fatals"] += recent
            out["lastFatal"] = {"ts": ts, "text": m.group(5)[:200]}
        elif kind.startswith("exit"):
            exit_after = True
            user_exit = bool(m.group(6))
        elif kind.startswith("layout:"):
            out["lastLayout"] = {"ts": ts, "text": m.group(7)[:80]}
    if out["lastStart"] is not None:
        out["state"] = "crashed" if fatal_after else "closed" if user_exit else "exited" if exit_after else "running-or-killed"
    return out


def read_tail(path=LOG):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - TAIL_BYTES))
            return f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def _meta_json(key, default):
    try:
        value = json.loads(db._meta_get(key, "") or "null")
        return default if value is None else value   # False قيمة صحيحة (الويدجت مخفيّ عمدًا) لا غياب
    except Exception:
        return default


def _meta_set(key, value):
    with db._lock:
        c = db.conn()
        c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        c.commit()


def set_wanted(on, now=None):
    """الإخفاء يُحفظ بوقته: بدءٌ للويدجت بعده (من اختصاره أو قائمة ابدأ) يعني أن المالك أعاده، فيعود المراقب إلى حراسته."""
    _meta_set("widget_wanted", True if on else {"hiddenAt": int(now or time.time())})


def wanted(last_start=None):
    v = _meta_json("widget_wanted", True)
    if isinstance(v, dict):
        return bool(last_start and last_start > int(v.get("hiddenAt") or 0))
    return v is not False   # False قيمة قديمة بلا وقت: مخفيّ حتى يراه المراقب يعمل


def _launch():
    """المهمة المجدولة نفسها التي تشغّله عند الدخول (بالصلاحية نفسها)؛ وإن غابت فالمشغّل العادي."""
    r = subprocess.run([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "schtasks.exe"), "/Run", "/TN", TASK],
                       capture_output=True, timeout=20, creationflags=CNW)
    if r.returncode != 0:
        widgetctl.widget_start()
        return "launcher"
    return "task"


def guard(now=None, running=widgetctl.widget_running, lines=read_tail, launch=_launch):
    now = now or time.time()
    if running():
        if _meta_json("widget_wanted", True) is False:
            set_wanted(True)   # علم إخفاء قديم بلا وقت والويدجت يعمل: المالك أعاده بعد الإخفاء
        return "up"
    st = parse(lines(), now)
    if st["state"] == "never":
        return "down: never started"
    if st["state"] == "closed":
        return "down: closed cleanly"
    if not wanted(st["lastStart"]):
        return "down: hidden from the app"
    if st["lastLine"] and now - st["lastLine"] < SETTLE_S:
        return "down: settling"
    recent = [t for t in _meta_json("widget_relaunches", []) if now - t < 3600]
    if len(recent) >= MAX_RELAUNCH:
        return "down: relaunch limit"
    how = launch()
    _meta_set("widget_relaunches", recent + [int(now)])
    why = {"crashed": "بعد سقوطه", "exited": "بعد إغلاق لم يطلبه المالك"}.get(st["state"], "بعد توقف عمليته")
    detail = {"crashed": (st["lastFatal"] or {}).get("text", ""), "exited": "خرج بلا خطأ ولم يُطلب إغلاقه من قائمته (إغلاق ويندوز لنافذة معلّقة مثلًا)."}.get(st["state"], "انتهت العملية بلا سطر خروج (قتل أو تعليق).")
    db.add_event(now, "widget", "warn", "أُعيد تشغيل الويدجت %s" % why, detail)
    return "relaunched via %s (%s)" % (how, st["state"])


def payload(now=None, running=widgetctl.widget_running, lines=read_tail):
    now = now or time.time()
    st = parse(lines(), now)
    up = bool(running())
    state = "running" if up else {"running-or-killed": "killed"}.get(st["state"], st["state"])
    relaunches = [t for t in _meta_json("widget_relaunches", []) if now - t < 86400]
    return {"running": up, "state": state, "starts": st["starts"], "fatals": st["fatals"], "stalls": st["stalls"],
            "stallMaxS": st["stallMaxS"], "stallSumS": st["stallSumS"], "lastStart": st["lastStart"], "lastFatal": st["lastFatal"],
            "lastLayout": st["lastLayout"], "relaunches": len(relaunches), "wanted": wanted(st["lastStart"])}
