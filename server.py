# -*- coding: utf-8 -*-
"""Local monitoring backend for the dashboard window and the mini desktop widget.
Binds to 127.0.0.1 only; authenticated APIs use a local token or expiring browser session.
The agent keeps the backend running independently of the dashboard."""

from sqlite_runtime import ensure_sqlite
ensure_sqlite()
import atexit, base64, hashlib, hmac, ctypes, faulthandler, json, os, re, secrets, subprocess, sys, threading, time, traceback
import atomic_json
import math, socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import db, health, infra
import routes_destructive   # البند 6/أ: المسارات الهدّامة (space/reaper-run/run-cleanup) مع تأكيد 428 من جانب الخادم
import memtrim              # البند 6/ب: تنظيف الذاكرة (trim-memory/trim-auto) وحالته والعدّ التنازلي
import routes_policy        # البند 6/ج: سياسة التنظيف والمهام ووضع الصيانة وبدء التشغيل ومهام الوكيل
import routes_settings      # البند 6/د: المظهر والوضع والتنبيهات وخطة الطاقة وإعدادات reaper
import shellwin             # البند 6/هـ: نافذة التطبيق وغلافها وحالة الغلاف لصفحة الصحة وبلاغ الويدجت
import widgetctl            # البند 6/و: ويدجت سطح المكتب (يعمل/ظاهر، إيقاف آمن، تشغيل، إعادة إلى الشاشة)
import snapshot             # البند 6/ز: القياسات الأصلية والجامع الثقيل وذاكرته والخيوط الخلفية وحمولتا state/mini
import routes_read          # البند 6/ح: جدول GET /api/* القرائية بمعالجة أخطاء موحّدة وتسجيل سبب كل 500
import startup_mgr          # تسخين قائمة بدء التشغيل عند الإقلاع
import winguard             # حارس النوافذ: يرصد نوافذ الطرفية الجديدة
import gpupurge             # تفريغ ذاكرة كرت الشاشة عند الطلب (POST /api/gpu-purge يبدأ، GET يعرض الحالة)
import routes_app           # البند 8: ما بقي من POST في الخادم (explain وexplain-errors وllm-unload وpage-open وshutdown)
POST_MODULES = (routes_destructive, memtrim, routes_policy, routes_settings, shellwin, widgetctl, gpupurge, routes_app)   # وحدات المسارات: كل وحدة post(route, body, invalidate) تعيد (رمز، ردّ) أو None
from paths import ROOT, HOME, PWSH, CNW, ENGINE, POLICY, PROT_ENGINE_DIR, RUNS, MCCFG, TIERS, TIER_TASK, PORT   # noqa: F401 (ثوابت مشتركة مع وحدات المسارات)
# one-time tickets replace the token in URLs: the window opens with ?k=<ticket>, which is consumed once and then
# exchanged for an HttpOnly session cookie. The long-lived token never appears on a command line.
try:
    SESSION_TTL = max(60, min(7 * 86400, int(os.environ.get("MW_SESSION_TTL_SECONDS", "43200"))))
except ValueError:
    SESSION_TTL = 43200
_SESSIONS, _SLOCK = {}, threading.Lock()
MAX_SESSIONS = 128
MAX_POST_BYTES = 65536
REQUEST_TIMEOUT_SECONDS = 15
MAX_HTTP_THREADS = 24


def _session_create():
    now = time.time()
    with _SLOCK:
        for key in [key for key, exp in _SESSIONS.items() if exp <= now]:
            del _SESSIONS[key]
        if len(_SESSIONS) >= MAX_SESSIONS:
            return None
        value = secrets.token_urlsafe(32)
        _SESSIONS[hashlib.sha256(value.encode("utf-8")).digest()] = now + SESSION_TTL
        return value


def _session_valid(value):
    if not value or len(value) > 128:
        return False
    key = hashlib.sha256(value.encode("utf-8")).digest()
    with _SLOCK:
        exp = _SESSIONS.get(key, 0)
        if exp <= time.time():
            _SESSIONS.pop(key, None)
            return False
        return True


def _session_revoke(value):
    with _SLOCK:
        _SESSIONS.pop(hashlib.sha256(value.encode("utf-8")).digest(), None)


def _reject_json_constant(value):
    raise ValueError("non-finite JSON number")


def _validate_json_numbers(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, dict):
        for item in value.values():
            _validate_json_numbers(item)
    elif isinstance(value, list):
        for item in value:
            _validate_json_numbers(item)

_TICKETS, _TLOCK = {}, threading.Lock()
def _healthz_ok():
    """quick_check مخزَّن في infra.integrity_cached ويُجدَّد في خيط خلفي كل 60 ث على الأكثر: مسار بلا مصادقة يجب ألا يكون
    رافعة على القاعدة ولا ينتظرها (قبل أول نتيجة يُعدّ سليمًا)."""
    return infra.integrity_cached(60)["ok"] is not False


def _issue_ticket():
    t = secrets.token_urlsafe(24)
    with _TLOCK:
        now = time.time()
        for k in [k for k, exp in _TICKETS.items() if exp < now]:
            del _TICKETS[k]
        if len(_TICKETS) >= 256:
            del _TICKETS[next(iter(_TICKETS))]
        _TICKETS[t] = now + 60
    return t
def _use_ticket(t):
    with _TLOCK:
        exp = _TICKETS.pop(t, None)
    return bool(exp and exp >= time.time())
IDLE_EXIT_SECONDS = 0  # 0 = never exit on idle; the agent's server_guard job keeps it running

TOKEN_FILE = os.path.join(ROOT, ".token")
if not os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE, "w") as f:
        f.write(secrets.token_urlsafe(24))
TOKEN = open(TOKEN_FILE).read().strip()
# Browser sessions are independent, time limited, and intentionally invalidated on restart.

def ensure_agent():
    """Start the always-on agent if its mutex is not held (idempotent; the agent also enforces a single instance)."""
    try:
        if ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "Local\\Azkia-MonitorAgent"):
            return False
        exe = sys.executable.replace("python.exe", "pythonw.exe")
        subprocess.Popen([exe, os.path.join(ROOT, "agent.py")], cwd=ROOT, creationflags=CNW)
        infra.log.info("agent launched by server")
        return True
    except Exception:
        infra.log.exception("ensure_agent")
        return False

_ASSETS = {
    "/assets/favicon.svg": "image/svg+xml", "/assets/icon.svg": "image/svg+xml",
    "/assets/logo_light.svg": "image/svg+xml", "/assets/logo_dark.svg": "image/svg+xml",
    "/assets/favicon.ico": "image/x-icon", "/assets/icon.ico": "image/x-icon",
    "/assets/icon_192.png": "image/png", "/assets/icon_512.png": "image/png",
    "/assets/apple-touch-icon.png": "image/png",
    "/assets/logo_light.png": "image/png", "/assets/logo_dark.png": "image/png",
}


_CSP_BASE = ("default-src 'none'; connect-src 'self'; img-src 'self' data:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'")
_csp_cache = {}


def _page_csp(html):
    """CSP for the page: inline scripts are allowed only by the hash of the exact <script> bodies in the served file."""
    key = hash(html)
    if key not in _csp_cache:
        _csp_cache.clear()
        bodies = re.findall(rb"<script>(.*?)</script>", html, re.S)
        # HTML parsing normalizes CRLF and CR to LF before the browser hashes
        # inline script text. Windows-written source files must use that exact
        # normalized text too, otherwise CSP blocks the entire application.
        hashes = " ".join("'sha256-%s'" % base64.b64encode(hashlib.sha256(b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).digest()).decode() for b in bodies)
        _csp_cache[key] = "script-src %s; %s" % (hashes or "'none'", _CSP_BASE)
    return _csp_cache[key]


class LocalHTTPServer(ThreadingHTTPServer):
    """Bound concurrent connections before creating worker threads."""
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(MAX_HTTP_THREADS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache="no-store", csp=None, cookie=None, etag=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", cache)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        if etag:
            self.send_header("ETag", etag)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        # JSON replies get a locked-down policy; the HTML page gets the hash-based one
        self.send_header("Content-Security-Policy", csp or "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(b)

    def _guard(self):
        if self.headers.get("Host", "") not in ("127.0.0.1:%d" % PORT, "localhost:%d" % PORT):
            self._send(403, {"error": "bad host"})
            return False
        snapshot.touch()
        return True

    def _cookie(self, name):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def _authed(self):
        tok = self.headers.get("X-Token", "")
        ck = self._cookie("mw_s")
        # constant-time comparison; both sides encoded so a non-ASCII header cannot raise
        if (tok and hmac.compare_digest(tok.encode("utf-8"), TOKEN.encode("utf-8"))) or            _session_valid(ck):
            return True
        self._send(401, {"error": "unauthorized"})
        return False

    def do_GET(self):
        if not self._guard():
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/v2", "/v2/"):  # /v2 اسم قديم للصفحة نفسها
            k = parse_qs(urlparse(self.path).query).get("k", [""])[0]
            if k and _use_ticket(k):  # exchange the one-time ticket for a cookie, then drop it from the URL
                session = _session_create()
                if session is None:
                    return self._send(503, {"error": "session capacity reached"})
                self.send_response(302)
                self.send_header("Set-Cookie", "mw_s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (session, SESSION_TTL))
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            page = open(os.path.join(ROOT, "web", "index.html"), "rb").read()
            self._send(200, page, "text/html; charset=utf-8", csp=_page_csp(page))
        elif re.fullmatch(r"/css/[a-z0-9_-]+\.css", path):
            # أنماط الواجهة المركزية: ملفات web/css الثابتة بلا أسرار؛ ETag بدل التخزين الطويل حتى يظهر أي تعديل فورًا
            try:
                body = open(os.path.join(ROOT, "web", "css", path.rsplit("/", 1)[1]), "rb").read()
            except OSError:
                return self._send(404, {"error": "not found"})
            tag = '"%s"' % hashlib.sha1(body).hexdigest()[:16]
            if self.headers.get("If-None-Match") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return
            self._send(200, body, "text/css; charset=utf-8", cache="no-cache", etag=tag)
        elif re.fullmatch(r"/fonts/[a-z0-9-]+\.woff2", path):
            # خطوط الهوية (OFL) من web/fonts: ملفات ثابتة بلا أسرار؛ ETag + تخزين يومي لأن أسماءها تحمل الوزن ولا تتغيّر إلا مع الملف
            try:
                body = open(os.path.join(ROOT, "web", "fonts", path.rsplit("/", 1)[1]), "rb").read()
            except OSError:
                return self._send(404, {"error": "not found"})
            tag = '"%s"' % hashlib.sha1(body).hexdigest()[:16]
            if self.headers.get("If-None-Match") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                return
            self._send(200, body, "font/woff2", cache="public, max-age=86400", etag=tag)
        elif path in _ASSETS or path == "/favicon.ico":
            fn = "favicon.ico" if path == "/favicon.ico" else path
            fp = os.path.join(ROOT, "assets", fn.lstrip("/").replace("assets/", "", 1))
            try:
                # static icons/logos carry ?v=N in the page, so they can be cached; only data replies stay no-store
                self._send(200, open(fp, "rb").read(), _ASSETS.get(fn, "image/x-icon"), cache="public, max-age=86400")
            except OSError:
                self._send(404, {"error": "not found"})
        elif path == "/api/healthz":
            ok = _healthz_ok()
            self._send(200 if ok else 503, {"ok": ok, "uptime_s": int(time.time() - infra.START), "pid": os.getpid()})
        elif path in routes_read.ROUTES:   # البند 6/ح: المسارات القرائية المصادَق عليها، جدول واحد ومعالجة أخطاء موحّدة
            if not self._authed():
                return
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            self._send(*routes_read.get(path, q))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._guard() or not self._authed():
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._send(415, {"error": "json only"})
            return
        org = self.headers.get("Origin")   # browsers always send it on cross-site POSTs; the widget and scripts send none
        if org is not None and org not in ("http://127.0.0.1:%d" % PORT, "http://localhost:%d" % PORT):
            self._send(403, {"error": "bad origin"})
            return
        try:
            if self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                return self._send(400, {"error": "transfer encoding unsupported"})
            raw_length = self.headers.get("Content-Length", "0")
            if not raw_length.isascii() or not raw_length.isdecimal():
                self.close_connection = True
                return self._send(400, {"error": "invalid content length"})
            n = int(raw_length)
            if n > MAX_POST_BYTES:
                self.close_connection = True
                return self._send(413, {"error": "request too large"})
            raw = self.rfile.read(n)
            if len(raw) != n:
                self.close_connection = True
                return self._send(400, {"error": "incomplete request"})
            body = json.loads(raw or b"{}", parse_constant=_reject_json_constant)
            _validate_json_numbers(body)
            if not isinstance(body, dict):
                return self._send(400, {"error": "object expected"})
            route = self.path.split("?")[0]
            for module in POST_MODULES:   # وحدات المسارات (البند 6 و8): كل POST عدا الجلسة والتذكرة
                reply = module.post(route, body, invalidate=snapshot.invalidate)
                if reply is not None:
                    return self._send(*reply)
            if route == "/api/session/logout":
                _session_revoke(self._cookie("mw_s"))
                return self._send(200, {"ok": True}, cookie="mw_s=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
            if route == "/api/ticket":
                return self._send(200, {"ticket": _issue_ticket()})
            return self._send(404, {"error": "not found"})
        except (socket.timeout, TimeoutError):
            self.close_connection = True
            self._send(408, {"error": "request timed out"})
        except (ValueError, TypeError, UnicodeError, RecursionError):
            self._send(400, {"error": "invalid request data"})
        except Exception:
            request_id = secrets.token_hex(6)
            infra.log.exception("POST failed request_id=%s route=%s", request_id, self.path.split("?")[0])
            self._send(500, {"error": "internal error", "request_id": request_id})


def watchdog():
    while True:
        time.sleep(20)
        if IDLE_EXIT_SECONDS and time.time() - snapshot.last_request() > IDLE_EXIT_SECONDS:
            os._exit(0)


def _timed(fn):
    def w(self, *a, **k):
        t0 = time.time()
        self._st = 200
        try:
            return fn(self, *a, **k)
        finally:
            infra.note_request(self.path.split("?")[0], getattr(self, "_st", 200), (time.time() - t0) * 1000)
    return w


_orig_send = Handler._send
def _send_rec(self, code, *a, **k):
    self._st = code
    return _orig_send(self, code, *a, **k)
Handler._send = _send_rec
Handler.do_GET = _timed(Handler.do_GET)
Handler.do_POST = _timed(Handler.do_POST)


def _maint():
    time.sleep(30)
    while True:
        try:
            pass
        except Exception:
            infra.log.exception("maintenance")
        time.sleep(3600)


_server_mutex = None


def _mark(event):
    """علامة مباشرة إلى ملف مستقل، لا تعتمد على نظام السجلات (الذي لم يصل من عملية الخادم)."""
    try:
        with open(os.path.join(infra.LOG_DIR, "server_marks.txt"), "a", encoding="utf-8") as f:
            f.write("%s %s pid=%d\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), event, os.getpid()))
    except Exception:
        pass


def _claim_single_server():
    global _server_mutex
    _server_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\Azkia-MonitorServer")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


if __name__ == "__main__":
    try:
        if not _claim_single_server():
            raise OSError("server already running")
        LocalHTTPServer.allow_reuse_address = False
        srv = LocalHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        if "--open" in sys.argv:
            shellwin.open_window(TOKEN)  # already running: just open another window
        sys.exit(0)
    # تشخيص الانهيار: الخادم يعمل بـ pythonw بلا نافذة، فنكتب كل توقف إلى السجل وإلى ملف faulthandler
    _fault_fh = open(os.path.join(infra.LOG_DIR, "server_fault.log"), "a", encoding="utf-8")
    faulthandler.enable(file=_fault_fh, all_threads=True)
    atexit.register(lambda: (_mark("exit"), infra.log.info("server exit pid=%d", os.getpid())))
    threading.excepthook = lambda a: infra.log.error("thread %s crashed:\n%s", a.thread.name if a.thread else "?",
                                                      "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    _mark("boot")
    infra.log.info("server boot pid=%d", os.getpid())
    snapshot.refresh_extra()
    c0 = db.bootstrap(); health.init()
    infra.integrity_cached()   # أول quick_check يبدأ الآن في خيط خلفي حتى لا تعرض صفحة التحكم «قيد الفحص» طويلًا
    infra.log.info('server start pid=%d schema=%s', os.getpid(), c0.execute('PRAGMA user_version').fetchone()[0])
    ensure_agent()
    for fn in (watchdog, snapshot.sampler, snapshot.refresher, snapshot.ingester, _maint, winguard.watch):
        threading.Thread(target=fn, daemon=True).start()
    threading.Thread(target=startup_mgr.list_items, daemon=True).start()   # تسخين قائمة بدء التشغيل قبل أول طلب
    if "--open" in sys.argv:
        threading.Thread(target=shellwin.open_window, args=(TOKEN,), daemon=True).start()
    try:
        srv.serve_forever()
    except BaseException:
        infra.log.exception("serve_forever stopped")
        raise
