"""Development helper: serves web/ and forwards /api/* to the running monitor with the local token (127.0.0.1 only)."""
import http.server, os, urllib.request, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
TOKEN = open(os.path.join(os.path.dirname(ROOT), ".token")).read().strip()
UP = "http://127.0.0.1:47832"
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k): super().__init__(*a, directory=ROOT, **k)
    def do_GET(self):
        if self.path.startswith("/api/"):
            try:
                req = urllib.request.Request(UP + self.path, headers={"X-Token": TOKEN})
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as r:
                    body = r.read(); self.send_response(r.status); self.send_header("Content-Type", r.headers.get("Content-Type", "application/json")); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            except Exception as e:
                self.send_response(502); self.end_headers(); self.wfile.write(str(e).encode())
            return
        if self.path.split("?")[0] == "/": self.path = "/index.html"
        super().do_GET()
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 47900), H).serve_forever()
