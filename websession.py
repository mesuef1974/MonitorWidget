"""جلسات المتصفح والتذاكر أحادية الاستعمال والرمز المحلي (البند 6/ط من الخارطة): كانت في server.py حرفيًا.

الرمز الطويل العمر في ‎.token‎ يستعمله الويدجت والسكربتات في ترويسة X-Token ولا يظهر في أي رابط أو سطر أوامر.
نافذة المتصفح تُفتح بتذكرة ‎?k=<ticket>‎ تُستهلك مرة واحدة خلال TICKET_TTL ثم تُبدَّل بجلسة في كعكة HttpOnly
تنتهي بعد SESSION_TTL (متغيّر البيئة MW_SESSION_TTL_SECONDS بين دقيقة و7 أيام). الجلسات والتذاكر في الذاكرة فقط:
تسقط كلها مع إعادة تشغيل الخادم عمدًا، وتُخزَّن الجلسة بتلبيدها لا بقيمتها.

    token()                 الرمز المحلي (يُنشأ الملف عند أول طلب إن لم يوجد)
    token_ok(value)         مقارنة بزمن ثابت مع الرمز؛ آمنة مع القيم غير ASCII والفارغة
    create() / valid(v) / revoke(v)   الجلسات؛ create تعيد None عند بلوغ MAX_SESSIONS
    cookie(session) / logout_cookie()  ترويسة Set-Cookie للجلسة ولإلغائها
    read_cookie(header, name=COOKIE)   قيمة كعكة من ترويسة Cookie الخام
    issue_ticket() / use_ticket(t)     التذاكر؛ use_ticket تعيد True مرة واحدة فقط ضمن المهلة
"""
import hashlib
import hmac
import os
import secrets
import threading
import time

from paths import ROOT

try:
    SESSION_TTL = max(60, min(7 * 86400, int(os.environ.get("MW_SESSION_TTL_SECONDS", "43200"))))
except ValueError:
    SESSION_TTL = 43200
MAX_SESSIONS = 128
TICKET_TTL = 60
MAX_TICKETS = 256
COOKIE = "mw_s"
TOKEN_FILE = os.path.join(ROOT, ".token")

_SESSIONS, _SLOCK = {}, threading.Lock()
_TICKETS, _TLOCK = {}, threading.Lock()
_TOKEN = None


def token():
    """الرمز المحلي من ‎.token‎؛ يُنشأ بـ 24 بايتًا عشوائيًا عند أول استدعاء إن لم يوجد، ويُخزَّن في الذاكرة بعدها."""
    global _TOKEN
    if _TOKEN is None:
        if not os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(secrets.token_urlsafe(24))
        with open(TOKEN_FILE, encoding="utf-8") as f:
            _TOKEN = f.read().strip()
    return _TOKEN


def token_ok(value):
    """مقارنة بزمن ثابت؛ الطرفان مرمَّزان حتى لا ترفع ترويسة غير ASCII استثناءً. الفارغ مرفوض دائمًا."""
    return bool(value) and hmac.compare_digest(value.encode("utf-8"), token().encode("utf-8"))


def _key(value):
    return hashlib.sha256(value.encode("utf-8")).digest()


def create():
    now = time.time()
    with _SLOCK:
        for key in [key for key, exp in _SESSIONS.items() if exp <= now]:
            del _SESSIONS[key]
        if len(_SESSIONS) >= MAX_SESSIONS:
            return None
        value = secrets.token_urlsafe(32)
        _SESSIONS[_key(value)] = now + SESSION_TTL
        return value


def valid(value):
    if not value or len(value) > 128:
        return False
    key = _key(value)
    with _SLOCK:
        exp = _SESSIONS.get(key, 0)
        if exp <= time.time():
            _SESSIONS.pop(key, None)
            return False
        return True


def revoke(value):
    if not value:
        return
    with _SLOCK:
        _SESSIONS.pop(_key(value), None)


def cookie(session):
    return f"{COOKIE}={session}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}"


def logout_cookie():
    return f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"


def read_cookie(header, name=COOKIE):
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return ""


def issue_ticket():
    t = secrets.token_urlsafe(24)
    with _TLOCK:
        now = time.time()
        for k in [k for k, exp in _TICKETS.items() if exp < now]:
            del _TICKETS[k]
        if len(_TICKETS) >= MAX_TICKETS:
            del _TICKETS[next(iter(_TICKETS))]
        _TICKETS[t] = now + TICKET_TTL
    return t


def use_ticket(t):
    with _TLOCK:
        exp = _TICKETS.pop(t, None)
    return bool(exp and exp >= time.time())
