"""خادم ثوابت للفحص المرئي: يقدّم web/ كما هو، ويجيب عن GET /api/<مسار> من tests/fixtures/api/<مسار>.json
(استجابات مسجَّلة من الخادم الحيّ ومنقّاة)، وعن أي POST بـ {"ok": true}. لا يلمس المرصد ولا قاعدة بياناته.

    python tests/web_fixture_server.py [منفذ=47910]
"""
import http.server
import json
import os
import sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(os.path.dirname(HERE), "web")
FIX = os.path.join(HERE, "fixtures", "api")


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=WEB, **k)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            name = path[5:].strip("/").replace("/", "__")
            if not name or ".." in name:
                return self._json(404, {"error": "unknown"})
            f = os.path.join(FIX, name + ".json")
            if not os.path.isfile(f):
                return self._json(404, {"error": "no fixture: " + name})
            with open(f, "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        return self._json(200, {"ok": True})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 47910
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
