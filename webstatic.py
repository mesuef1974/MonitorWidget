"""الملفات الثابتة للواجهة وسياسة CSP للصفحة (البند 6/ي من الخارطة): كانت في server.py حرفيًا.

الصفحة web/index.html تُقدَّم بسياسة تسمح بالسكربتات المضمّنة بتلبيد نصّها فقط (بعد توحيد نهايات الأسطر كما يفعل
محلّل HTML في المتصفح)؛ أنماط web/css بـ ETag بلا تخزين طويل حتى يظهر أي تعديل فورًا؛ خطوط web/fonts بـ ETag وتخزين يومي
لأن أسماءها تحمل الوزن؛ الأيقونات والشعارات من assets/ بتخزين يومي لأن الصفحة تحملها بـ ‎?v=N‎. لا أسرار في أي منها.

    PAGE_PATHS / is_page(path)      مسارات الصفحة (‎/v2‎ اسم قديم للصفحة نفسها)
    page_csp(html)                  سياسة CSP للصفحة بتلبيد كل <script> فيها (مخزَّنة لنصّ الصفحة الحالي)
    serve(path, if_none_match="")   -> None إن لم يكن المسار ثابتًا، وإلا قاموس code/body/ctype/cache/csp/etag
                                       (304 عندما يطابق If-None-Match وسم الملف؛ 404 بجسم JSON عند غياب الملف)
"""
import base64
import hashlib
import os
import re

from paths import ROOT

PAGE_PATHS = ("/", "/index.html", "/v2", "/v2/")
ASSETS = {
    "/assets/favicon.svg": "image/svg+xml", "/assets/icon.svg": "image/svg+xml",
    "/assets/logo_light.svg": "image/svg+xml", "/assets/logo_dark.svg": "image/svg+xml",
    "/assets/favicon.ico": "image/x-icon", "/assets/icon.ico": "image/x-icon",
    "/assets/icon_192.png": "image/png", "/assets/icon_512.png": "image/png",
    "/assets/apple-touch-icon.png": "image/png",
    "/assets/logo_light.png": "image/png", "/assets/logo_dark.png": "image/png",
}
CSP_BASE = ("default-src 'none'; connect-src 'self'; img-src 'self' data:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; object-src 'none'")
CSS_RE = re.compile(r"/css/[a-z0-9_-]+\.css")
FONT_RE = re.compile(r"/fonts/[a-z0-9-]+\.woff2")
DAY = "public, max-age=86400"
_csp_cache = {}


def is_page(path):
    return path in PAGE_PATHS


def page_csp(html):
    """CSP for the page: inline scripts are allowed only by the hash of the exact <script> bodies in the served file."""
    key = hash(html)
    if key not in _csp_cache:
        _csp_cache.clear()
        bodies = re.findall(rb"<script>(.*?)</script>", html, re.DOTALL)
        # HTML parsing normalizes CRLF and CR to LF before the browser hashes
        # inline script text. Windows-written source files must use that exact
        # normalized text too, otherwise CSP blocks the entire application.
        digests = [base64.b64encode(hashlib.sha256(b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).digest()).decode() for b in bodies]
        hashes = " ".join(f"'sha256-{d}'" for d in digests) or "'none'"
        _csp_cache[key] = f"script-src {hashes}; {CSP_BASE}"
    return _csp_cache[key]


def _reply(code, body, ctype="application/json; charset=utf-8", cache="no-store", csp=None, etag=None):
    return {"code": code, "body": body, "ctype": ctype, "cache": cache, "csp": csp, "etag": etag}


def _tagged(fp, ctype, cache, if_none_match):
    """ملف ثابت بوسم ETag من محتواه: 304 عند تطابق الوسم، 404 عند غيابه."""
    try:
        with open(fp, "rb") as f:
            body = f.read()
    except OSError:
        return _reply(404, {"error": "not found"})
    tag = f'"{hashlib.sha1(body).hexdigest()[:16]}"'
    if if_none_match == tag:
        return _reply(304, b"", ctype, cache, etag=tag)
    return _reply(200, body, ctype, cache, etag=tag)


def serve(path, if_none_match=""):
    if is_page(path):
        with open(os.path.join(ROOT, "web", "index.html"), "rb") as f:
            page = f.read()
        return _reply(200, page, "text/html; charset=utf-8", csp=page_csp(page))
    if CSS_RE.fullmatch(path):
        return _tagged(os.path.join(ROOT, "web", "css", path.rsplit("/", 1)[1]), "text/css; charset=utf-8", "no-cache", if_none_match)
    if FONT_RE.fullmatch(path):
        return _tagged(os.path.join(ROOT, "web", "fonts", path.rsplit("/", 1)[1]), "font/woff2", DAY, if_none_match)
    if path in ASSETS or path == "/favicon.ico":
        fn = "favicon.ico" if path == "/favicon.ico" else path
        fp = os.path.join(ROOT, "assets", fn.lstrip("/").replace("assets/", "", 1))
        try:
            with open(fp, "rb") as f:
                return _reply(200, f.read(), ASSETS.get(fn, "image/x-icon"), DAY)
        except OSError:
            return _reply(404, {"error": "not found"})
    return None
