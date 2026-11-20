# -*- coding: utf-8 -*-
"""Protected-location constants and integrity helpers (see CHANGES_2026-10-24.md, item 1).
The owner runs install_secure_2026-10-24.ps1 as administrator once: it copies the probe and cleanup scripts to
SECURE_DIR\bin (ACL: admins/SYSTEM full, users read+execute) and registers tasks that run only from there.
Legacy fallback is available only before any protected installation state exists."""
import hashlib, json, os


def _system_secure_dir():
    """Resolve machine ProgramData without trusting inherited user variables."""
    if os.name != "nt":
        return r"C:\ProgramData\MonitorWidget"
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    # CSIDL_COMMON_APPDATA. This is the configured machine folder, not os.environ.
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x23, None, 0, buffer)
    if result != 0 or not buffer.value:
        raise RuntimeError("Cannot resolve the machine protected application directory")
    return os.path.join(buffer.value, "MonitorWidget")


_CODE_ROOT = os.path.dirname(os.path.abspath(__file__))
_DEPLOYMENT_MARKER = os.path.join(_CODE_ROOT, ".deployment", "manifest.json")
# In a prepared/installed deployment, inherited user overrides cannot redirect
# elevated probes or hash pins to user-owned files. Portable test copies retain
# their explicit override support.
SECURE_DIR = _system_secure_dir() if os.path.isfile(_DEPLOYMENT_MARKER) else (os.environ.get("MW_SECURE_DIR") or _system_secure_dir())
BIN = os.path.join(SECURE_DIR, "bin")
OUT = os.path.join(SECURE_DIR, "out")
# مهام تفريغ الذاكرة المتقاعدة: البند 24 من المجلس 19/11 حذف standby وdeep، وقرار المالك 20/11/2026 حذف light (يحرّر نحو 3 MB)؛
# النشر والمثبّت المحمي يلغيان تسجيلها
RETIRED_PURGE_TASKS = ("MemoryPurge-Standby", "MemoryPurge-Deep", "MemoryPurge-Light")
HEALTH_PROBE = os.path.join(BIN, "healthprobe.ps1")   # علامة التثبيت المحمي: ينسخه المثبّت دائمًا (كانت purge.exe قبل حذف التنظيف الخفيف)
ADMIN_PROBE = os.path.join(OUT, "admin_probe.json")
WINOLD_TASKS = {"normal": "MonitorWidget-WinOld", "early": "MonitorWidget-WinOld-Early"}   # fixed elevated tasks; the server never deletes Windows.old itself
WINOLD_RESULT = os.path.join(OUT, "winold_result.json")
# fixed elevated cleanup tasks (wintemp / swdist / dism): the limited server only triggers them and reads the result file
# dwm: إعادة تشغيل مدير سطح المكتب لتحرير ذاكرة الرسوميات المشتركة (gpu_dwm_restart.ps1؛ يطلبها gpupurge بتأكيد المالك)
CLEAN_TASKS = {"wintemp": "MonitorWidget-Clean-WinTemp", "swdist": "MonitorWidget-Clean-SwDist", "dism": "MonitorWidget-Clean-Dism",
               "dwm": "MonitorWidget-Gpu-Dwm"}
CLEAN_SCRIPT = os.path.join(BIN, "elevated_clean.ps1")
# الكانس (Invoke-OrphanReaper.ps1 من MachineCare) يُنهي شلات المستخدم الخاملة فلا يحتاج صلاحية مسؤول. الخادم المرتفع لا يشغّل
# نسخة مجلد المستخدم بصلاحيته؛ يطلق مهمتين ثابتتين بصلاحية محدودة (Limited) يسجّلهما المثبّت المحمي، بوضع مثبَّت في وسيطهما.
REAPER_TASKS = {"dry": "MonitorWidget-Reaper-Dry", "apply": "MonitorWidget-Reaper-Apply"}


def clean_result(kind):
    return os.path.join(OUT, "clean_%s_result.json" % kind)


REAPER_PIN = os.path.join(OUT, "reaper_pin.json")   # {"sha256": "...", "script": "..."} written by the installer


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def installed():
    """True once the protected install exists (the probe script plus output folder)."""
    return os.path.isfile(HEALTH_PROBE) and os.path.isdir(OUT)


def protected_expected():
    """A partial/previous protected install must never silently downgrade."""
    return os.environ.get("MW_REQUIRE_SECURE", "").lower() in ("1", "true", "yes") or os.path.isdir(SECURE_DIR)


def protected_app_ready():
    """Recognize the final installed app, not a marker in a source/staging copy.

    Stage/Apply separately verify ACLs and release hashes. The final location and
    the protected manifest must agree before changing collection ownership.
    """
    expected = os.path.join(_system_secure_dir(), "app")
    if os.path.normcase(os.path.realpath(_CODE_ROOT)) != os.path.normcase(os.path.realpath(expected)):
        return False
    try:
        with open(_DEPLOYMENT_MARKER, encoding="utf-8-sig") as fh:
            manifest = json.load(fh)
        target = manifest.get("destination")
        return isinstance(target, str) and os.path.normcase(os.path.realpath(target)) == os.path.normcase(os.path.realpath(expected))
    except (OSError, ValueError, AttributeError):
        return False


def reaper_pin_state(script_path):
    """('pinned-ok'|'pinned-mismatch'|'unpinned', detail). A mismatch must block execution; unpinned only warns."""
    try:
        with open(REAPER_PIN, encoding="utf-8-sig") as fh:
            pin = json.load(fh)
    except (OSError, ValueError):
        if protected_expected():
            return "pinned-mismatch", "protected installation requires a readable pin file"
        return "unpinned", "no pin file"
    if not isinstance(pin, dict) or not isinstance(pin.get("sha256"), str):
        return "pinned-mismatch", "invalid pin file"
    try:
        actual = sha256_file(script_path)
    except OSError as e:
        return "pinned-mismatch", "script unreadable: %s" % e
    if actual.lower() == str(pin.get("sha256", "")).lower():
        return "pinned-ok", actual
    return "pinned-mismatch", actual


def is_admin():
    """هل تعمل هذه العملية بصلاحية مسؤول؟ الخادم يجب أن يعمل عاديًا دائمًا (فصل الصلاحيات)؛ القيمة تُقرأ مرة عند بدء العملية."""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
