"""لوحة الذكاء المحلي: ما يعمل في LM Studio وبأي سرعة، دون أي استدعاء لنموذج.

بطاقة 8 GB لا تتسع لنموذجين: إذا اجتمعا هبط التوليد من نحو 45 إلى نحو 2 tok/s. الخيط watch يقرأ الحالة
كل 30 ثانية ويسجّل حدثًا عند اجتماعهما، والمسار /api/local-ai يقرأ آخر حالة فلا ينتظر LM Studio.
"""
import glob
import json
import os
import re
import time
import urllib.request

import db
import errlog

MODELS_URL = "http://127.0.0.1:1234/api/v0/models"
LOGS = os.path.join(os.path.expanduser("~"), ".lmstudio", "server-logs")
WATCH_EVERY = 30
TIMEOUT = 2
TAIL_BYTES = 262144
MIN_TOKENS = 20         # الردود الأقصر لا تعطي سرعة يُعتمد عليها
SLOW_TPS = 10           # نموذج واحد يعطي هنا أكثر من 40، واجتماع نموذجين نحو 2
RECENT_SECONDS = 600    # سرعة أقدم من هذا لا تصف الحال الآن
USAGE_DAYS = 14

_EVAL = re.compile(r"(?<!prompt )eval time =\s*[\d.]+ ms /\s*(\d+) tokens.*?([\d.]+) tokens per second")
_STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")

STATE = {"ready": False, "verdict": None, "models": [], "speed": None, "scannedAt": None}


def models():
    """النماذج اللغوية المحمّلة، أو None إن كان خادم LM Studio متوقفًا."""
    try:
        with urllib.request.urlopen(MODELS_URL, timeout=TIMEOUT) as resp:
            rows = json.loads(resp.read().decode("utf-8")).get("data") or []
    except (OSError, ValueError):
        return None
    return [{"id": m.get("id", ""), "context": m.get("loaded_context_length"), "maxContext": m.get("max_context_length"),
             "quant": m.get("quantization")}
            for m in rows if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm")]


def parse_speed(text):
    """آخر سرعة توليد في نص السجل. سطر eval نفسه بلا ختم وقت، فيؤخذ ختم أقرب سطر قبله."""
    ts, found = None, None
    for line in text.splitlines():
        stamp = _STAMP.match(line)
        if stamp:
            ts = stamp.group(1)
        m = _EVAL.search(line)
        if m and ts and int(m.group(1)) >= MIN_TOKENS:
            found = {"tps": float(m.group(2)), "tokens": int(m.group(1)), "ts": int(time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S")))}
    return found


def _log_tail():
    files = glob.glob(os.path.join(LOGS, "*", "*.log"))
    if not files:
        return ""
    with open(max(files, key=os.path.getmtime), "rb") as fh:
        fh.seek(0, os.SEEK_END)
        fh.seek(max(0, fh.tell() - TAIL_BYTES))
        return fh.read().decode("utf-8", "replace")


def verdict(loaded, speed, now):
    if loaded is None:
        return "off"
    if not loaded:
        return "idle"
    if len(loaded) > 1:
        return "crowded"
    if speed and now - speed["ts"] <= RECENT_SECONDS and speed["tps"] < SLOW_TPS:
        return "slow"
    return "ok"


def refresh(now=None):
    """يحدّث الحالة، ويسجّل حدثًا مرة واحدة عند الدخول في حالة اجتماع النماذج."""
    now = time.time() if now is None else now
    loaded = models()
    try:
        speed = parse_speed(_log_tail())
    except OSError:
        errlog.swallowed()
        speed = None
    state = verdict(loaded, speed, now)
    if state == "crowded" and STATE["verdict"] != "crowded":
        db.add_event(now, "localai", "warn", "نموذجان محمّلان معًا في بطاقة الرسوميات", " + ".join(m["id"] for m in loaded))
    STATE.update(ready=True, verdict=state, models=loaded or [], speed=speed, scannedAt=int(now))
    return STATE


def usage(now=None):
    """استخدام الشرح خلال آخر USAGE_DAYS يومًا: يُحكم به على بقاء الميزة بالأرقام."""
    since = (time.time() if now is None else now) - USAGE_DAYS * 86400
    with db.ro() as c:
        rows = c.execute("SELECT ok,detail FROM actions WHERE kind='explain' AND ts>=?", (int(since),)).fetchall()
    details = []
    for r in rows:
        try:
            details.append(json.loads(r["detail"] or "{}"))
        except ValueError:
            errlog.swallowed()
    times = [d["ms"] for d in details if d.get("ms")]
    return {"days": USAGE_DAYS, "runs": len(rows), "failed": sum(1 for r in rows if not r["ok"]),
            "avgMs": int(sum(times) / len(times)) if times else None,
            "fallback": sum(d.get("fallback") or 0 for d in details), "items": sum(d.get("total") or 0 for d in details)}


def payload():
    return {**STATE, "usage": usage()}


def watch():
    while True:
        try:
            refresh()
        except (OSError, ValueError):
            errlog.swallowed()
        time.sleep(WATCH_EVERY)
