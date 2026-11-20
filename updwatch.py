"""مهام التحديث (البند 23 من المجلس 19/11): فحص قرائي يحذّر حين يُعطَّل محدِّث برنامج مثبَّت أو تختفي مهمته.

التشخيص (docs/council23_diagnosis.md): MicrosoftEdgeUpdateTaskMachineCore وTVT\\TVSUUpdateTask_UserLogOn عُطّلتا من صفحة
«بدء التشغيل» نفسها (جدول actions: 17/10، ثم Edge في 20/10 و26/10 و7/11؛ محدِّث Edge يعيد تفعيل مهمته عند كل تحديث).
والمهام محجوبة عن المستخدم العادي: Get-ScheduledTask بلا صلاحية لا يراها أصلًا. لذلك:
  1. evaluate(task_paths()): لكل محدِّث معروف: هل برنامجه مثبَّت (ملف أو مجلد ثابت)، وما مهامه في المجدول وهل هي مفعّلة. المهام تُعدَّد من
     سجل TaskCache\\Tree (يقرؤه المستخدم العادي) وحالة التفعيل من ملف XML للمهمة في System32\\Tasks (عنصر Settings/Enabled،
     وغيابه = مفعّلة). بايثون خالص بلا PowerShell (الخط الأحمر 9)، ويقرأ ملفات المهام المطابقة وحدها (بضعة ملفات).
  2. watch(): بعد الإقلاع بـ DELAY_S ثم كل INTERVAL_S؛ تنبيه warn في سجل التنبيهات (المصدر updates) حين تتغيّر مجموعة
     المحدِّثات المعطّلة أو المفقودة، لا في كل دورة.
  3. is_updater(): startup_mgr يرفض تعطيل مهمة تحديث من صفحة بدء التشغيل (الخط الأحمر 6)؛ تفعيلها مسموح.
لا يُصلح شيئًا: لا إعادة تسجيل ولا تفعيل آلي؛ القرار للمالك من صفحة بدء التشغيل أو بإعادة تثبيت البرنامج.
"""
import os
import re
import threading
import time
import winreg
import xml.etree.ElementTree as ET

TREE = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\TaskCache\Tree"
TASKS_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Tasks")
NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
DELAY_S = 120
INTERVAL_S = 6 * 3600
MAX_KEYS = 5000          # سقف مفاتيح الشجرة المقروءة (الجهاز الحالي نحو 300)

_PF86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
_PF = os.environ.get("ProgramFiles", r"C:\Program Files")
_CPF = os.environ.get("CommonProgramFiles", r"C:\Program Files\Common Files")

# key: معرّف ثابت؛ product: أي مسار موجود = البرنامج مثبَّت؛ task: نمط على المسار الكامل للمهمة (يبدأ بـ \).
UPDATERS = (
    {"key": "edge", "label": "تحديث Microsoft Edge",
     "product": (os.path.join(_PF86, r"Microsoft\Edge\Application\msedge.exe"), os.path.join(_PF, r"Microsoft\Edge\Application\msedge.exe")),
     "task": r"^\\MicrosoftEdgeUpdateTaskMachine(Core|UA)$"},
    {"key": "chrome", "label": "تحديث Google Chrome",
     "product": (os.path.join(_PF, r"Google\Chrome\Application\chrome.exe"), os.path.join(_PF86, r"Google\Chrome\Application\chrome.exe")),
     "task": r"^\\GoogleUpdateTask"},
    {"key": "lenovo-su", "label": "Lenovo System Update (BIOS والتعريفات)",
     "product": (os.path.join(_PF86, r"Lenovo\System Update\tvsu.exe"),),
     "task": r"^\\TVT\\TVSUUpdateTask"},
    {"key": "vantage", "label": "صيانة Lenovo Vantage",
     "product": (os.path.join(_PF86, r"Lenovo\VantageService"),),
     "task": r"^\\Lenovo\\Vantage\\(Lenovo\.Vantage\.ServiceMaintainance|Schedule\\LenovoSystemUpdateAddin_WeeklyTask)$"},
    {"key": "office", "label": "تحديث Microsoft Office",
     "product": (os.path.join(_CPF, r"microsoft shared\ClickToRun\OfficeC2RClient.exe"),),
     "task": r"^\\Microsoft\\Office\\Office Automatic Updates"},
)
_RX = [(u, re.compile(u["task"], re.IGNORECASE)) for u in UPDATERS]

_lock = threading.Lock()
_STATE = {"state": "pending", "ts": None, "items": [], "error": None}
_LAST_BAD = {"v": None}


def is_updater(full_path):
    """هل المسار الكامل للمهمة (\\مجلد\\اسم) مهمة تحديث معروفة؟"""
    return any(rx.search(full_path or "") for _, rx in _RX)


def task_paths():
    """كل المهام المسجّلة بمسارها الكامل، من شجرة TaskCache (مفتاح فيه القيمة Id = مهمة، وغيره مجلد).
    الشجرة يقرؤها المسؤول والنظام وحدهما: جذر لا يُفتح يرفع PermissionError فتكون الحالة skipped لا تحذيرًا كاذبًا."""
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, TREE):
        pass
    out, stack, seen = [], [""], 0
    while stack and seen < MAX_KEYS:
        sub = stack.pop()
        seen += 1
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, TREE + sub) as k:
                try:
                    winreg.QueryValueEx(k, "Id")
                    out.append(sub)
                except OSError:
                    pass
                i = 0
                while True:
                    try:
                        stack.append(sub + "\\" + winreg.EnumKey(k, i))
                    except OSError:
                        break
                    i += 1
        except OSError:
            continue
    return out


def task_enabled(full_path):
    """True/False من Settings/Enabled في ملف المهمة (غياب العنصر = مفعّلة)؛ None إن تعذّرت القراءة."""
    try:
        root = ET.parse(os.path.join(TASKS_DIR, full_path.lstrip("\\"))).getroot()
    except (OSError, ET.ParseError):
        return None
    el = root.find(NS + "Settings/" + NS + "Enabled")
    return True if el is None else (el.text or "").strip().lower() != "false"


def evaluate(paths, exists=os.path.exists, enabled=task_enabled):
    """بند لكل محدِّث برنامجه مثبَّت: warn إن غابت مهامه أو عُطّلت كلها (لا تحديث آلي)، وpartial إن عُطّل بعضها والباقي
    يحدّث (يُعرض ولا يُنبَّه به)، وok إن كانت كلها مفعّلة. حالة ملف لا يُقرأ (None) لا تُعدّ تعطيلًا."""
    items = []
    for u, rx in _RX:
        if not any(exists(p) for p in u["product"]):
            continue
        tasks = [{"path": p, "enabled": enabled(p)} for p in paths if rx.search(p)]
        if not tasks:
            level, text = "warn", "البرنامج مثبَّت ولا مهمة تحديث له في المجدول"
        elif all(t["enabled"] is False for t in tasks):
            level, text = "warn", "مهام التحديث معطّلة كلها"
        elif any(t["enabled"] is False for t in tasks):
            off = [t["path"].rsplit("\\", 1)[-1] for t in tasks if t["enabled"] is False]
            level, text = "partial", "يحدّث، ومعطّل منها: " + "، ".join(off)
        else:
            level, text = "ok", "مهام التحديث مفعّلة"
        items.append({"key": u["key"], "label": u["label"], "level": level, "text": text, "tasks": tasks})
    return items


def run_once():
    try:
        items = evaluate(task_paths())
    except PermissionError:
        with _lock:
            _STATE.update(state="skipped", ts=int(time.time()), items=[], error=None)
        return dict(_STATE)
    except Exception as e:   # noqa: BLE001 (فحص قرائي: أي خطأ يُعرض حالةً ولا يُسقط الخيط)
        with _lock:
            _STATE.update(state="error", ts=int(time.time()), error=str(e)[:200])
        return dict(_STATE)
    bad = sorted(i["key"] for i in items if i["level"] == "warn")
    with _lock:
        _STATE.update(state="warn" if bad else "ok", ts=int(time.time()), items=items, error=None)
        st = dict(_STATE)
    if bad and bad != _LAST_BAD["v"]:
        import db
        detail = " | ".join(f"{i['label']}: {i['text']}" for i in items if i["level"] == "warn")
        db.add_event(st["ts"], "updates", "warn", f"مهام تحديث معطّلة أو مفقودة ({len(bad)})", detail[:1000])
    _LAST_BAD["v"] = bad
    return st


def watch():
    time.sleep(DELAY_S)
    while True:
        run_once()
        time.sleep(INTERVAL_S)


def payload():
    with _lock:
        return dict(_STATE)
