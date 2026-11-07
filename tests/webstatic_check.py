"""الملفات الثابتة وCSP (webstatic، البند 6/ي): الصفحة تُقدَّم بسياسة تلبيد السكربتات المضمّنة بعد توحيد نهايات الأسطر،
الأنماط بـ ETag بلا تخزين طويل و304 عند تطابق الوسم، الخطوط والأيقونات بتخزين يومي، الأسماء خارج النمط لا تُخدَم (لا اجتياز مسارات)،
والملف الغائب 404 بجسم JSON، وما ليس ثابتًا يعيد None للخادم. معزول: paths وهمي إلى مجلد مؤقت فيه نسخة web/ وassets/ صغيرة.

Run: python -X utf8 -B tests/webstatic_check.py
"""
import base64
import hashlib
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="mw_webstatic_")
for d in ("web/css", "web/fonts", "assets"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
PAGE = b"<!doctype html><html><head></head><body><script>const a = 1;\r\nconst b = 2;\r\n</script><script>x()</script></body></html>"
FILES = {"web/index.html": PAGE, "web/css/tokens.css": b":root{--x:1}", "web/fonts/amiri-400-1.woff2": b"wOF2fake",
         "assets/favicon.ico": b"ICO", "assets/icon.svg": b"<svg/>"}
for rel, data in FILES.items():
    with open(os.path.join(TMP, rel), "wb") as f:
        f.write(data)
sys.modules["paths"] = types.SimpleNamespace(ROOT=TMP)
import webstatic


def sha(b):
    return "'sha256-" + base64.b64encode(hashlib.sha256(b).digest()).decode() + "'"


class Page(unittest.TestCase):
    def test_page_paths(self):
        for p in ("/", "/index.html", "/v2", "/v2/"):
            self.assertTrue(webstatic.is_page(p))
        for p in ("/v3", "/index.htm", "/api/mini", ""):
            self.assertFalse(webstatic.is_page(p))

    def test_served_with_hashed_csp(self):
        r = webstatic.serve("/")
        self.assertEqual((r["code"], r["ctype"], r["cache"]), (200, "text/html; charset=utf-8", "no-store"))
        self.assertEqual(r["body"], PAGE)
        self.assertIn(sha(b"const a = 1;\nconst b = 2;\n"), r["csp"])   # CRLF يُوحَّد إلى LF قبل التلبيد كما يفعل المتصفح
        self.assertIn(sha(b"x()"), r["csp"])
        self.assertTrue(r["csp"].startswith("script-src 'sha256-"))
        self.assertTrue(r["csp"].endswith(webstatic.CSP_BASE))
        self.assertNotIn("unsafe-inline", r["csp"].split(";")[0])

    def test_csp_without_scripts_and_cache_follows_content(self):
        self.assertTrue(webstatic.page_csp(b"<html></html>").startswith("script-src 'none'; "))
        a = webstatic.page_csp(b"<script>1</script>")
        self.assertEqual(webstatic.page_csp(b"<script>1</script>"), a)
        self.assertNotEqual(webstatic.page_csp(b"<script>2</script>"), a)
        self.assertEqual(len(webstatic._csp_cache), 1)   # يُحتفظ بسياسة النصّ الحالي فقط


class Files(unittest.TestCase):
    def test_css_etag_and_304(self):
        r = webstatic.serve("/css/tokens.css")
        self.assertEqual((r["code"], r["ctype"], r["cache"], r["body"]), (200, "text/css; charset=utf-8", "no-cache", b":root{--x:1}"))
        self.assertRegex(r["etag"], r'^"[0-9a-f]{16}"$')
        again = webstatic.serve("/css/tokens.css", r["etag"])
        self.assertEqual((again["code"], again["etag"], again["cache"]), (304, r["etag"], "no-cache"))
        self.assertEqual(webstatic.serve("/css/tokens.css", '"other"')["code"], 200)

    def test_font_daily_cache(self):
        r = webstatic.serve("/fonts/amiri-400-1.woff2")
        self.assertEqual((r["code"], r["ctype"], r["cache"]), (200, "font/woff2", "public, max-age=86400"))
        self.assertEqual(webstatic.serve("/fonts/amiri-400-1.woff2", r["etag"])["code"], 304)

    def test_missing_file_is_404_json(self):
        for p in ("/css/missing.css", "/fonts/missing-1.woff2", "/assets/icon_192.png"):
            r = webstatic.serve(p)
            self.assertEqual((r["code"], r["body"], r["ctype"]), (404, {"error": "not found"}, "application/json; charset=utf-8"), p)

    def test_names_outside_pattern_are_not_served(self):
        for p in ("/css/../index.html", "/css/A.css", "/css/x.css.bak", "/fonts/x.ttf", "/assets/other.png", "/assets/../.token", "/api/mini", "/web/index.html"):
            self.assertIsNone(webstatic.serve(p), p)

    def test_assets_and_favicon(self):
        r = webstatic.serve("/favicon.ico")
        self.assertEqual((r["code"], r["ctype"], r["cache"], r["body"]), (200, "image/x-icon", "public, max-age=86400", b"ICO"))
        r = webstatic.serve("/assets/icon.svg")
        self.assertEqual((r["code"], r["ctype"], r["body"]), (200, "image/svg+xml", b"<svg/>"))
        self.assertEqual(set(webstatic.ASSETS.values()), {"image/svg+xml", "image/x-icon", "image/png"})


if __name__ == "__main__":
    unittest.main(verbosity=1)
