"""Alert thresholds in one place (alerts.json next to the app). Read by the CPU watcher, the footer and the alerts page."""
import os
import re
import json
import time
import shutil
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(ROOT, "alerts.json")
_lock = threading.Lock()
_cache = {"t": 0.0, "v": None}

DEFAULTS = {
    "cpu": {"minCores": 2.0, "windowMin": 10, "ignore": ["System"]},   # 0.6 نواة كانت تُنبّه على أي تحميل عادي؛ عمليةُ System لا يملك المستخدم إغلاقها (قرار المالك 7/11/2026)
    "mem": {"ramWarn": 80, "ramBad": 92, "commitWarn": 85, "commitBad": 95},
    "disk": {"freeWarnGB": 40, "freeBadGB": 20},
    "gpu": {"tempWarn": 80, "tempBad": 85},
}
# (low, high) accepted range for each number
LIMITS = {
    "cpu": {"minCores": (0.1, 8.0), "windowMin": (3, 60)},
    "mem": {"ramWarn": (50, 99), "ramBad": (55, 100), "commitWarn": (50, 99), "commitBad": (55, 100)},
    "disk": {"freeWarnGB": (5, 500), "freeBadGB": (2, 400)},
    "gpu": {"tempWarn": (60, 100), "tempBad": (65, 110)},
}
_NAME = re.compile(r"^[A-Za-z0-9_.\- ]{1,64}$")


def _merge(raw):
    out = json.loads(json.dumps(DEFAULTS))
    if not isinstance(raw, dict):
        return out
    for sec, vals in DEFAULTS.items():
        src = raw.get(sec)
        if not isinstance(src, dict):
            continue
        for k, dv in vals.items():
            v = src.get(k)
            if k == "ignore":
                if isinstance(v, list):
                    out[sec][k] = [str(x) for x in v if isinstance(x, str) and _NAME.match(x)][:40]
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                lo, hi = LIMITS[sec][k]
                if lo <= v <= hi:
                    out[sec][k] = type(dv)(v)
    return out


def get():
    now = time.time()
    with _lock:
        if _cache["v"] is not None and now - _cache["t"] < 5:
            return _cache["v"]
    try:
        raw = json.load(open(PATH, encoding="utf-8-sig"))
    except Exception:
        raw = None
    v = _merge(raw)
    with _lock:
        _cache["v"], _cache["t"] = v, now
    return v


def validate(body):
    """Returns (clean config, error string or None). Pairs must keep warn below bad."""
    if not isinstance(body, dict):
        return None, "bad body"
    out = json.loads(json.dumps(DEFAULTS))
    for sec, vals in DEFAULTS.items():
        src = body.get(sec)
        if src is None:
            continue
        if not isinstance(src, dict):
            return None, "bad section " + sec
        for k, dv in vals.items():
            if k not in src:
                continue
            v = src[k]
            if k == "ignore":
                if not isinstance(v, list) or any((not isinstance(x, str)) or not _NAME.match(x) for x in v):
                    return None, "bad ignore list"
                out[sec][k] = sorted(set(v))[:40]
            else:
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    return None, "bad number " + k
                lo, hi = LIMITS[sec][k]
                if not (lo <= v <= hi):
                    return None, "%s out of range %s..%s" % (k, lo, hi)
                out[sec][k] = type(dv)(v)
    m, d, g = out["mem"], out["disk"], out["gpu"]
    if m["ramWarn"] >= m["ramBad"] or m["commitWarn"] >= m["commitBad"]:
        return None, "warning level must be below the critical level"
    if d["freeBadGB"] >= d["freeWarnGB"]:
        return None, "critical free space must be below the warning level"
    if g["tempWarn"] >= g["tempBad"]:
        return None, "warning temperature must be below the critical one"
    return out, None


def save(cfg):
    with _lock:
        if os.path.exists(PATH):
            try:
                shutil.copyfile(PATH, PATH + ".bak")
            except Exception:
                pass
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PATH)
        _cache["v"], _cache["t"] = cfg, 0.0
    return cfg


def cpu_min_cores():
    return float(get()["cpu"]["minCores"])


def cpu_ignore():
    return {x.lower() for x in get()["cpu"]["ignore"]}
