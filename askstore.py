"""ذاكرة «اسأل المرصد»: المحادثات 30 يومًا وتقييم الردود والتعليمات الدائمة.

التعليمات يكتبها المالك من الصفحة فقط، ولا أداة للنموذج تكتب هنا، لأن نتائج أدواته قد تحمل نصًّا غير موثوق.
مدة الحفظ في db.RETENTION. الجداول بصيغة IF NOT EXISTS خارج سلسلة الهجرات، فهي إضافية ولا تمس user_version.

    save(question, job)              -> معرّف الرد أو None
    payload()                        -> المحادثة والتعليمات وعدّادات التقييم
    fact_texts()                     -> نصوص التعليمات للنموذج
    post(route, body, invalidate)    -> (رمز الحالة، الردّ) أو None
"""
import time

import db
import errlog

KEEP_DAYS = 30
MAX_TURNS = 200
MAX_FACTS = 20
MAX_FACT = 200
ROUTES = ("/api/assistant-rate", "/api/assistant-clear", "/api/assistant-fact")
SCHEMA = """
CREATE TABLE IF NOT EXISTS ask_turns(
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
  model TEXT, ms INTEGER, rating INTEGER);
CREATE INDEX IF NOT EXISTS ix_ask_turns_ts ON ask_turns(ts);
CREATE TABLE IF NOT EXISTS ask_facts(id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, text TEXT NOT NULL UNIQUE);
"""
_ready = None


def _db():
    """الاتصال المشترك بعد التأكد من الجداول. المستدعي يحمل db._lock."""
    global _ready
    c = db.conn()
    if _ready is not c:
        c.executescript(SCHEMA)
        _ready = c
    return c


def _audit(c, kind):
    c.execute("INSERT INTO actions(ts,kind,src,ok,detail) VALUES(?,?,?,?,?)", (int(time.time()), kind, "page", 1, "{}"))


def _ratings(c):
    r = c.execute("SELECT COALESCE(SUM(rating=1),0) good, COALESCE(SUM(rating=-1),0) bad FROM ask_turns").fetchone()
    return {"good": r[0], "bad": r[1]}


def _facts(c):
    return [{"id": r[0], "text": r[1]} for r in c.execute("SELECT id,text FROM ask_facts ORDER BY id")]


def save(question, job):
    now = int(time.time())
    try:
        with db._lock:
            c = _db()
            c.execute("INSERT INTO ask_turns(ts,role,content) VALUES(?,?,?)", (now, "user", question))
            cur = c.execute("INSERT INTO ask_turns(ts,role,content,model,ms) VALUES(?,?,?,?,?)",
                            (now, "assistant", job.get("text") or "", job.get("model"), job.get("ms")))
            c.commit()
            return cur.lastrowid
    except Exception:
        errlog.swallowed()
        return None


def payload():
    lo = int(time.time()) - KEEP_DAYS * 86400
    with db._lock:
        c = _db()
        rows = c.execute("SELECT id,role,content,model,ms,rating FROM ask_turns WHERE ts>=? ORDER BY id DESC LIMIT ?", (lo, MAX_TURNS)).fetchall()
        turns = [{"id": r[0], "role": r[1], "content": r[2], "model": r[3], "ms": r[4], "rating": r[5]} for r in reversed(rows)]
        return {"turns": turns, "facts": _facts(c), "ratings": _ratings(c), "keepDays": KEEP_DAYS}


def fact_texts():
    try:
        with db._lock:
            return [f["text"] for f in _facts(_db())]
    except Exception:
        errlog.swallowed()
        return []


def _rate(body):
    turn, value = body.get("id"), body.get("value")
    if type(turn) is not int or type(value) is not int or value not in (-1, 0, 1):
        return 400, {"error": "bad params"}
    with db._lock:
        c = _db()
        done = c.execute("UPDATE ask_turns SET rating=? WHERE id=? AND role='assistant'", (value or None, turn)).rowcount
        c.commit()
        return (200, {"ok": True, "ratings": _ratings(c)}) if done else (404, {"error": "الرد غير موجود"})


def _clear(body):
    with db._lock:
        c = _db()
        c.execute("DELETE FROM ask_turns")
        _audit(c, "ask_clear")
        c.commit()
    return 200, {"ok": True}


def _fact(body):
    text = body.get("text")
    with db._lock:
        c = _db()
        if type(body.get("remove")) is int:
            c.execute("DELETE FROM ask_facts WHERE id=?", (body["remove"],))
        else:
            text = text.strip() if isinstance(text, str) else ""
            if not text or len(text) > MAX_FACT:
                return 400, {"error": "bad params"}
            if c.execute("SELECT COUNT(*) FROM ask_facts").fetchone()[0] >= MAX_FACTS:
                return 400, {"error": "بلغت التعليمات حدها (%d)، فاحذف واحدة أولًا" % MAX_FACTS}
            c.execute("INSERT OR IGNORE INTO ask_facts(ts,text) VALUES(?,?)", (int(time.time()), text))
        _audit(c, "ask_fact")
        c.commit()
        return 200, {"ok": True, "facts": _facts(c)}


_TABLE = {"/api/assistant-rate": _rate, "/api/assistant-clear": _clear, "/api/assistant-fact": _fact}


def post(route, body, invalidate=lambda: None):
    fn = _TABLE.get(route)
    return None if fn is None else fn(body if isinstance(body, dict) else {})
