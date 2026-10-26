"""Design-review helper: when shots_request.json exists next to the app, take headless-Edge screenshots of the dashboard tabs
into shots\\auto\\ using the normal one-time-ticket door (the same one the dashboard window uses), then write shots_done.json.
Idle cost is one os.path.exists() per run. Request format: {"tabs":["home","cpu"],"themes":["dark","light"],"w":1626,"h":1000}"""
import os
import json
import time
import shutil
import tempfile
import subprocess
import urllib.request


ROOT = os.path.dirname(os.path.abspath(__file__))
REQ = os.path.join(ROOT, "shots_request.json")
DONE = os.path.join(ROOT, "shots_done.json")
OUTDIR = os.path.join(ROOT, "shots", "auto")
PORT = 47832
CNW = 0x08000000


def _edge():
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            p = os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")
            if os.path.exists(p):
                return p
    return None


def _ticket(tok):
    rq = urllib.request.Request("http://127.0.0.1:%d/api/ticket" % PORT, data=b"{}", method="POST",
                                headers={"X-Token": tok, "Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))["ticket"]


def job():
    if not os.path.exists(REQ):
        return "idle"
    try:
        req = json.load(open(REQ, encoding="utf-8-sig"))
    except Exception:
        os.replace(REQ, REQ + ".bad")
        return "bad request"
    os.remove(REQ)  # consume first so a crash cannot loop
    edge = _edge()
    if not edge:
        json.dump({"ts": int(time.time()), "error": "msedge.exe not found"}, open(DONE, "w"))
        return "no edge"
    tok = open(os.path.join(ROOT, ".token"), encoding="utf-8").read().strip()
    os.makedirs(OUTDIR, exist_ok=True)
    w, h = int(req.get("w", 1626)), int(req.get("h", 1000))
    out = []
    for th in req.get("themes") or ["dark"]:
        for tab in req.get("tabs") or ["home"]:
            png = os.path.join(OUTDIR, "%s-%s-%d.png" % (tab, th, h))
            prof = tempfile.mkdtemp(prefix="shot-prof-")
            try:
                if os.path.exists(png):
                    os.remove(png)
                url = "http://127.0.0.1:%d/?k=%s#%s,%s" % (PORT, _ticket(tok), tab, th)
                pr = subprocess.Popen([edge, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--virtual-time-budget=%d" % int(req.get("budget", 9000)),
                                       "--user-data-dir=" + prof, "--window-size=%d,%d" % (w, h), "--screenshot=" + png, url],
                                      creationflags=CNW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                t0 = time.time()
                while time.time() - t0 < 60:   # headless Edge may hand off to a child process: wait for the file, not the launcher
                    if os.path.exists(png) and os.path.getsize(png) > 0:
                        time.sleep(1.0)
                        break
                    time.sleep(0.5)
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pr.pid)], capture_output=True, creationflags=CNW)
                time.sleep(1.5)
                out.append({"tab": tab, "theme": th, "file": png, "ok": os.path.exists(png)})
            except Exception as e:
                out.append({"tab": tab, "theme": th, "ok": False, "err": str(e)[:120]})
            finally:
                shutil.rmtree(prof, ignore_errors=True)
    json.dump({"ts": int(time.time()), "shots": out}, open(DONE, "w", encoding="utf-8"), ensure_ascii=False)
    return "%d shots" % sum(1 for o in out if o.get("ok"))
