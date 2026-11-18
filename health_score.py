"""Pure, coverage-aware scoring. Missing and stale measurements never earn points."""
import math
from datetime import datetime

PROBE_MAX_AGE = 8 * 3600  # collector refreshes every 6h; allow 2h scheduling grace
UPDATES_MAX_AGE = 36 * 3600
GPU_MAX_AGE = 180
MIN_COVERAGE = 50
TASK_OK = frozenset({0, 267009, 267011, 267014, 267008, 267010, 2147946720})   # نتائج المهام المجدولة التي لا تُعدّ فشلًا
LOCK_SKIP = 3   # CleanupEngine يخرج بـ3 حين تعمل جولة أخرى (engine.lock)؛ تخطٍّ مقصود لا فشل


def task_ok(task):
    """True إن لم تفشل المهمة: نجاح/جارية، أو تنظيف (group=clean) تخطّى بسبب قفل جولة أخرى (الرمز 3)."""
    r = task.get("result")
    return r in TASK_OK or (r == LOCK_SKIP and task.get("group") == "clean")


TASK_FUTURE_SLACK = 600   # ثوانٍ؛ كما في db.TASK_FUTURE_SLACK


def task_last_ts(task):
    """وقت آخر تشغيل بثوانٍ، أو None إن تعذّرت قراءته."""
    try:
        return datetime.fromisoformat(str(task["last"]).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        return None


def task_from_future(task, now):
    """True إن كان آخر تشغيل بعد الآن: نتيجة سُجّلت بساعة نظام قافزة (14/11/2026) فلا تشهد على حال المهمة،
    ويبقيها المجدول حتى تشغيلها التالي."""
    ts = task_last_ts(task)
    return ts is not None and ts > now + TASK_FUTURE_SLACK


def task_failed(task, now):
    """فشل يُحسب: نتيجة غير ناجحة من تشغيل حقيقي لا من وقت مستقبلي."""
    return not task_ok(task) and not task_from_future(task, now)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def build_score(metrics, tasks, probe_ts, hardware, updates_ts, updates, gpu, now):
    hardware = hardware or {}
    parts = []

    def fresh(ts, limit):
        return number(ts) and -60 <= now - ts <= limit

    def linear(value, bad, good, maximum):
        return round(max(0, min(1, (value - bad) / (good - bad))) * maximum, 1)

    def add(key, label, points, maximum, why, measured_at=None, stale=False):
        parts.append({"key": key, "label": label, "pts": points, "max": maximum,
                      "why": why, "measuredAt": measured_at,
                      "status": "stale" if stale else "missing" if points is None else "measured"})

    commit, available = metrics.get("commitPct"), metrics.get("availGB")
    memory_ok = number(commit) and 0 <= commit <= 100 and number(available) and available >= 0
    add("mem", "الذاكرة", round(linear(commit, 97, 70, 22) + linear(available, .4, 3, 8), 1) if memory_ok else None,
        30, "التزام %s%% · متاح %s GB" % (commit, available) if memory_ok else "قياس الذاكرة غير متاح", now if memory_ok else None)
    free = metrics.get("diskFreeGB")
    disk_ok = number(free) and free >= 0
    add("disk", "مساحة القرص", linear(free, 12, 100, 20) if disk_ok else None,
        20, "حر %s GB" % free if disk_ok else "قياس المساحة غير متاح", now if disk_ok else None)

    enabled = [t for t in (tasks or []) if t.get("enabled")]
    task_data = bool(enabled) and all(not t.get("missing") and isinstance(t.get("result"), int) for t in enabled)

    def age_hours(task):
        ts = task_last_ts(task)
        return math.inf if ts is None else (now - ts) / 3600

    def names(items):
        return "، ".join(str(t.get("label", t.get("name", "مهمة"))) for t in items[:3])

    bad = [t for t in enabled if task_failed(t, now)]
    future = [t for t in enabled if not task_ok(t) and task_from_future(t, now)]
    recent_bad = sum(age_hours(t) <= 7 * 24 for t in bad)
    why = ("فشلت: " + names(bad)) if bad else "كلها سليمة"
    if future:
        why += "؛ لا تُحسب نتيجة بوقت تشغيل مستقبلي (قفزة ساعة): " + names(future)
    add("tasks", "المهام المجدولة", max(0, 15 - 5 * recent_bad - 2 * (len(bad) - recent_bad)) if task_data else None,
        15, why if task_data else "بيانات المهام غير مكتملة", now if task_data else None)
    backups = [t for t in enabled if t.get("group") == "backup"]
    backups_known = bool(backups) and all(not t.get("missing") and isinstance(t.get("result"), int) for t in backups)
    old_backups = [t for t in backups if age_hours(t) > 36 or age_hours(t) < -1 / 60 or t.get("result") not in (0, 267009)]
    backup_points = (10 if not old_backups else 5 if len(old_backups) < len(backups) else 0) if backups_known else None
    add("backup", "النسخ الاحتياطي", backup_points, 10,
        "نسخة قديمة أو مهمة فاشلة" if backups_known and old_backups else "حديثة" if backups_known else "لم تثبت حالة النسخ الاحتياطي",
        now if backups_known else None)

    probe_fresh = fresh(probe_ts, PROBE_MAX_AGE)
    disks = hardware.get("disks") or []
    hw_known = bool(disks) and all(d.get("health") not in (None, "", "Unknown", "Unspecified") for d in disks)
    hp, reasons = 10, []
    for disk in disks:
        if disk.get("health") not in ("Healthy", None, ""):
            hp -= 6; reasons.append("قرص %s" % disk.get("health"))
        if number(disk.get("wearPct")) and disk["wearPct"] >= 80:
            hp -= 4; reasons.append("تآكل القرص %s%%" % disk["wearPct"])
        if sum(v for v in (disk.get("readErr"), disk.get("writeErr")) if number(v)) > 0:
            hp -= 2; reasons.append("أخطاء قراءة/كتابة")
    battery = hardware.get("battery") or {}
    if number(battery.get("healthPct")) and battery["healthPct"] < 70:
        hp -= 2; reasons.append("سعة البطارية %s%%" % battery["healthPct"])
    add("hw", "العتاد", max(0, hp) if hw_known and probe_fresh else None, 10,
        "؛ ".join(reasons) if hw_known and probe_fresh and reasons else "سليم وفق القياسات المتاحة" if hw_known and probe_fresh
        else "قياس العتاد قديم" if hw_known else "العتاد غير مقاس", probe_ts, bool(hw_known and not probe_fresh))

    defender = hardware.get("defender") or {}
    updates = updates or {}
    sec_known = isinstance(defender.get("realtime"), bool) and number(defender.get("sigAgeDays")) and number(updates.get("n")) and updates["n"] >= 0
    sec_fresh = probe_fresh and fresh(updates_ts, UPDATES_MAX_AGE)
    sp, reasons = 10, []
    if defender.get("realtime") is False:
        sp -= 5; reasons.append("الحماية الفورية متوقفة")
    if number(defender.get("sigAgeDays")) and defender["sigAgeDays"] > 7:
        sp -= 2; reasons.append("تعريفات ديفندر قديمة")
    if hardware.get("pendingReboot"):
        sp -= 2; reasons.append("إعادة تشغيل معلّقة")
    if number(updates.get("n")) and updates["n"] > 0:
        sp -= min(3, updates["n"]); reasons.append("%s تحديث معلّق" % updates["n"])
    add("sec", "الأمان والتحديثات", max(0, sp) if sec_known and sec_fresh else None, 10,
        "؛ ".join(reasons) if sec_known and sec_fresh and reasons else "سليم وفق القياسات المتاحة" if sec_known and sec_fresh
        else "قياس الأمان أو التحديثات قديم" if sec_known else "قياسات الأمان والتحديثات غير مكتملة",
        min(probe_ts or 0, updates_ts or 0) or None, bool(sec_known and not sec_fresh))
    gpu = gpu or {}
    gpu_known = number(gpu.get("temp")) and -20 <= gpu["temp"] <= 150
    gpu_fresh = fresh(gpu.get("ts"), GPU_MAX_AGE)
    add("therm", "الحرارة", linear(gpu["temp"], 90, 75, 5) if gpu_known and gpu_fresh else None, 5,
        "حرارة كرت الشاشة %s°" % gpu["temp"] if gpu_known and gpu_fresh else "قياس الحرارة قديم" if gpu_known else "الحرارة غير مقاسة",
        gpu.get("ts"), bool(gpu_known and not gpu_fresh))

    measured = [p for p in parts if p["pts"] is not None]
    coverage = sum(p["max"] for p in measured)
    # The denominator includes measured components only; coverage is always shown alongside it.
    result = round(100 * sum(p["pts"] for p in measured) / coverage) if coverage >= MIN_COVERAGE else None
    return {"score": result, "parts": parts, "probeTs": probe_ts, "coveragePct": coverage,
            "status": "complete" if coverage == 100 else "partial" if result is not None else "insufficient",
            "freshness": {"probeTs": probe_ts, "updatesTs": updates_ts, "gpuTs": gpu.get("ts")},
            "scoringVersion": 2}
