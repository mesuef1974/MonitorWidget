"""إقلاع الخادم (البند 6/ك من الخارطة): احتكار النسخة الواحدة بمتكس، تشخيص الانهيار (faulthandler وعلامات boot/exit)،
إطلاق الوكيل الدائم، الخيوط الخلفية (watchdog والجامع والحارسان)، ثم حلقة الخدمة.
server.py يبقى نقطة الدخول: يبني المعالج والخادم ويستدعي run(server_cls, handler_cls, argv)."""
import atexit
import ctypes
import faulthandler
import os
import subprocess
import sys
import threading
import time
import traceback

import db
import health
import infra
import localai
import shellwin
import snapshot
import startup_mgr
import websession
import winguard
from paths import CNW, PORT, ROOT

IDLE_EXIT_SECONDS = 0  # 0 = لا خروج عند الخمول؛ مهمة server_guard في الوكيل تبقيه يعمل
SERVER_MUTEX = "Local\\Azkia-MonitorServer"
AGENT_MUTEX = "Local\\Azkia-MonitorAgent"
ERROR_ALREADY_EXISTS = 183
_server_mutex = None   # مقبض المتكس يُحفظ حتى لا يُحرَّر بجمع القمامة
_fault_fh = None       # ملف faulthandler يبقى مفتوحًا طوال عمر العملية


def ensure_agent():
    """يطلق الوكيل الدائم إن لم يكن متكسه محجوزًا (idempotent؛ الوكيل نفسه يفرض نسخة واحدة)."""
    try:
        if ctypes.windll.kernel32.OpenMutexW(0x00100000, False, AGENT_MUTEX):
            return False
        exe = sys.executable.replace("python.exe", "pythonw.exe")
        subprocess.Popen([exe, os.path.join(ROOT, "agent.py")], cwd=ROOT, creationflags=CNW)
        infra.log.info("agent launched by server")
        return True
    except Exception:
        infra.log.exception("ensure_agent")
        return False


def watchdog():
    while True:
        time.sleep(20)
        if IDLE_EXIT_SECONDS and time.time() - snapshot.last_request() > IDLE_EXIT_SECONDS:
            os._exit(0)


def maint():
    time.sleep(30)
    while True:
        try:
            pass
        except Exception:
            infra.log.exception("maintenance")
        time.sleep(3600)


def mark(event):
    """علامة مباشرة إلى ملف مستقل، لا تعتمد على نظام السجلات (الذي لم يصل من عملية الخادم)."""
    try:
        with open(os.path.join(infra.LOG_DIR, "server_marks.txt"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {event} pid={os.getpid()}\n")
    except Exception:
        pass


def claim_single_server():
    global _server_mutex
    _server_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, SERVER_MUTEX)
    return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def background_workers():
    """الخيوط الخلفية التي يطلقها الإقلاع بالترتيب (كلها daemon)."""
    return (watchdog, snapshot.sampler, snapshot.refresher, snapshot.ingester, maint, winguard.watch, localai.watch)


def run(server_cls, handler_cls, argv):
    """يحتكر النسخة الواحدة ويربط المنفذ؛ إن كان الخادم يعمل أصلًا يكتفي بفتح نافذة أخرى عند --open ثم يخرج 0."""
    try:
        if not claim_single_server():
            raise OSError("server already running")
        server_cls.allow_reuse_address = False
        srv = server_cls(("127.0.0.1", PORT), handler_cls)
    except OSError:
        if "--open" in argv:
            shellwin.open_window(websession.token())
        sys.exit(0)
    # تشخيص الانهيار: الخادم يعمل بـ pythonw بلا نافذة، فنكتب كل توقف إلى السجل وإلى ملف faulthandler
    global _fault_fh
    _fault_fh = open(os.path.join(infra.LOG_DIR, "server_fault.log"), "a", encoding="utf-8")   # noqa: SIM115 (يبقى مفتوحًا طوال عمر العملية)
    faulthandler.enable(file=_fault_fh, all_threads=True)
    atexit.register(lambda: (mark("exit"), infra.log.info("server exit pid=%d", os.getpid())))
    threading.excepthook = lambda a: infra.log.error("thread %s crashed:\n%s", a.thread.name if a.thread else "?",
                                                     "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    mark("boot")
    infra.log.info("server boot pid=%d", os.getpid())
    snapshot.refresh_extra()
    c0 = db.bootstrap()
    health.init()
    infra.integrity_cached()   # أول quick_check يبدأ الآن في خيط خلفي حتى لا تعرض صفحة التحكم «قيد الفحص» طويلًا
    infra.log.info("server start pid=%d schema=%s", os.getpid(), c0.execute("PRAGMA user_version").fetchone()[0])
    ensure_agent()
    for fn in background_workers():
        threading.Thread(target=fn, daemon=True).start()
    threading.Thread(target=startup_mgr.list_items, daemon=True).start()   # تسخين قائمة بدء التشغيل قبل أول طلب
    if "--open" in argv:
        threading.Thread(target=shellwin.open_window, args=(websession.token(),), daemon=True).start()
    try:
        srv.serve_forever()
    except BaseException:
        infra.log.exception("serve_forever stopped")
        raise
