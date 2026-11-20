# -*- coding: utf-8 -*-
"""Local monitoring backend for the dashboard window and the mini desktop widget.
Binds to 127.0.0.1 only; authenticated APIs use a local token or expiring browser session.
The agent keeps the backend running independently of the dashboard."""

from sqlite_runtime import ensure_sqlite
ensure_sqlite()
import json, os, secrets, socket, sys, threading, time
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import infra
import routes_destructive   # البند 6/أ: المسارات الهدّامة (space/reaper-run/run-cleanup) مع تأكيد 428 من جانب الخادم
import routes_policy        # البند 6/ج: سياسة التنظيف والمهام ووضع الصيانة وبدء التشغيل ومهام الوكيل
import routes_settings      # البند 6/د: المظهر والوضع والتنبيهات وخطة الطاقة وإعدادات reaper
import shellwin             # البند 6/هـ: نافذة التطبيق وغلافها وحالة الغلاف لصفحة الصحة وبلاغ الويدجت
import widgetctl            # البند 6/و: ويدجت سطح المكتب (يعمل/ظاهر، إيقاف آمن، تشغيل، إعادة إلى الشاشة)
import snapshot             # البند 6/ز: القياسات الأصلية والجامع الثقيل وذاكرته والخيوط الخلفية وحمولتا state/mini
import routes_read          # البند 6/ح: جدول GET /api/* القرائية بمعالجة أخطاء موحّدة وتسجيل سبب كل 500
import gpupurge             # تفريغ ذاكرة كرت الشاشة عند الطلب (POST /api/gpu-purge يبدأ، GET يعرض الحالة)
import askstore             # ذاكرة «اسأل المرصد»: التقييم والمسح والتعليمات
import cpuprio              # حارس المعالج: POST /api/cpu-lower يخفض أولوية العملية المنبَّه إليها
import routes_app           # البند 8: ما بقي من POST في الخادم (explain وexplain-errors وllm-unload وpage-open وshutdown)
import websession           # البند 6/ط: جلسات المتصفح والتذاكر أحادية الاستعمال والرمز المحلي
import webstatic            # البند 6/ي: الصفحة والأنماط والخطوط والأيقونات وسياسة CSP للصفحة
import webboot              # البند 6/ك: الإقلاع (النسخة الواحدة، تشخيص الانهيار، الوكيل، الخيوط الخلفية، حلقة الخدمة)
POST_MODULES = (routes_destructive, routes_policy, routes_settings, shellwin, widgetctl, gpupurge, askstore, cpuprio, routes_app)   # وحدات المسارات: كل وحدة post(route, body, invalidate) تعيد (رمز، ردّ) أو None
from paths import PORT
# التذاكر أحادية الاستعمال والجلسات والرمز في websession.py (البند 6/ط)
MAX_POST_BYTES = 65536
REQUEST_TIMEOUT_SECONDS = 15
MAX_HTTP_THREADS = 24


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

def _healthz_ok():
    """quick_check مخزَّن في infra.integrity_cached ويُجدَّد في خيط خلفي كل 60 ث على الأكثر: مسار بلا مصادقة يجب ألا يكون
    رافعة على القاعدة ولا ينتظرها (قبل أول نتيجة يُعدّ سليمًا)."""
    return infra.integrity_cached(60)["ok"] is not False


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
        return websession.read_cookie(self.headers.get("Cookie", ""), name)

    def _authed(self, read_only=False):
        # الرمز بمقارنة زمن ثابت أو جلسة متصفح صالحة (websession). read_only: مسارات GET القرائية تقبل رمز القراءة أيضًا
        tok = self.headers.get("X-Token", "")
        ok = websession.read_token_ok(tok) if read_only else websession.token_ok(tok)
        if ok or websession.valid(self._cookie(websession.COOKIE)):
            return True
        self._send(401, {"error": "unauthorized"})
        return False

    def do_GET(self):
        if not self._guard():
            return
        path = self.path.split("?")[0]
        if webstatic.is_page(path):   # تبديل التذكرة بجلسة قبل تقديم الصفحة (البند 6/ط)
            k = parse_qs(urlparse(self.path).query).get("k", [""])[0]
            if k and websession.use_ticket(k):  # exchange the one-time ticket for a cookie, then drop it from the URL
                session = websession.create()
                if session is None:
                    return self._send(503, {"error": "session capacity reached"})
                self.send_response(302)
                self.send_header("Set-Cookie", websession.cookie(session))
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        st = webstatic.serve(path, self.headers.get("If-None-Match", ""))   # الصفحة والأنماط والخطوط والأيقونات (البند 6/ي)
        if st is not None:
            if st["code"] == 304:
                self.send_response(304)
                self.send_header("ETag", st["etag"])
                self.send_header("Cache-Control", st["cache"])
                self.end_headers()
                return
            return self._send(st["code"], st["body"], st["ctype"], cache=st["cache"], csp=st["csp"], etag=st["etag"])
        if path == "/api/healthz":
            ok = _healthz_ok()
            self._send(200 if ok else 503, {"ok": ok, "uptime_s": int(time.time() - infra.START), "pid": os.getpid()})
        elif path in routes_read.ROUTES:   # البند 6/ح: المسارات القرائية المصادَق عليها، جدول واحد ومعالجة أخطاء موحّدة
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            if not self._authed(read_only=not routes_read.needs_full(path, q)):   # أسئلة المالك: الرمز الكامل وحده (البند 10)
                return
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
                websession.revoke(self._cookie(websession.COOKIE))
                return self._send(200, {"ok": True}, cookie=websession.logout_cookie())
            if route == "/api/ticket":
                return self._send(200, {"ticket": websession.issue_ticket()})
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


if __name__ == "__main__":
    webboot.run(LocalHTTPServer, Handler, sys.argv)   # الإقلاع كله في webboot.py (البند 6/ك)
