"""جلسات المتصفح والتذاكر والرمز المحلي (websession، البند 6/ط): الرمز يُنشأ في ‎.token‎ ويُقارن بزمن ثابت، الجلسة تُخزَّن
بتلبيدها وتنتهي بمهلتها وتُلغى وحدها وسقفها محدود، والتذكرة تُستهلك مرة واحدة خلال مهلتها وسقفها يُخرج الأقدم.
معزول: paths بديل وهمي يشير إلى مجلد مؤقت فلا يُلمس ‎.token‎ الحقيقي ولا يُستورد الخادم.

Run: python -X utf8 -B tests/websession_check.py
"""
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="mw_websession_")
sys.modules["paths"] = types.SimpleNamespace(ROOT=TMP)
import websession


class Token(unittest.TestCase):
    def setUp(self):
        websession._TOKEN = None
        if os.path.exists(websession.TOKEN_FILE):
            os.remove(websession.TOKEN_FILE)

    def test_created_once_and_cached(self):
        self.assertFalse(os.path.exists(websession.TOKEN_FILE))
        first = websession.token()
        self.assertTrue(os.path.exists(websession.TOKEN_FILE))
        self.assertGreaterEqual(len(first), 32)   # token_urlsafe(24)
        with open(websession.TOKEN_FILE, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), first)
        os.remove(websession.TOKEN_FILE)
        self.assertEqual(websession.token(), first)   # مخزَّن في الذاكرة بعد أول قراءة

    def test_existing_file_is_read_and_stripped(self):
        with open(websession.TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write("  synthetic-token \n")
        self.assertEqual(websession.token(), "synthetic-token")

    def test_token_ok_constant_time_and_safe(self):
        websession._TOKEN = "synthetic-token"
        self.assertTrue(websession.token_ok("synthetic-token"))
        self.assertFalse(websession.token_ok("synthetic-tokeN"))
        self.assertFalse(websession.token_ok(""))
        self.assertFalse(websession.token_ok(None))
        self.assertFalse(websession.token_ok("رمز غير ASCII"))   # لا استثناء


class Sessions(unittest.TestCase):
    def setUp(self):
        websession._SESSIONS.clear()

    def test_create_valid_revoke_independent(self):
        a, b = websession.create(), websession.create()
        self.assertNotEqual(a, b)
        self.assertNotIn(a, repr(websession._SESSIONS))   # تُخزَّن بتلبيدها لا بقيمتها
        self.assertTrue(websession.valid(a) and websession.valid(b))
        websession.revoke(a)
        self.assertFalse(websession.valid(a))
        self.assertTrue(websession.valid(b))
        websession.revoke("")   # لا استثناء على الفارغ
        websession.revoke("never-issued")

    def test_expiry_and_cleanup(self):
        a = websession.create()
        with patch.object(time, "time", return_value=time.time() + websession.SESSION_TTL + 1):
            self.assertFalse(websession.valid(a))
            self.assertEqual(len(websession._SESSIONS), 0)   # المنتهية تُحذف عند الفحص

    def test_invalid_values(self):
        self.assertFalse(websession.valid(""))
        self.assertFalse(websession.valid(None))
        self.assertFalse(websession.valid("x" * 129))
        self.assertFalse(websession.valid("not-a-session"))

    def test_capacity_bounded_then_freed_by_expiry(self):
        with patch.object(websession, "MAX_SESSIONS", 2):
            self.assertTrue(websession.create())
            self.assertTrue(websession.create())
            self.assertIsNone(websession.create())
            with patch.object(time, "time", return_value=time.time() + websession.SESSION_TTL + 1):
                self.assertTrue(websession.create())   # المنتهية تُحذف قبل عدّ السقف

    def test_cookie_headers(self):
        c = websession.cookie("abc")
        self.assertTrue(c.startswith("mw_s=abc; "))
        for flag in ("Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={websession.SESSION_TTL}"):
            self.assertIn(flag, c)
        out = websession.logout_cookie()
        self.assertTrue(out.startswith("mw_s=; "))
        self.assertIn("Max-Age=0", out)
        self.assertIn("HttpOnly", out)

    def test_read_cookie(self):
        self.assertEqual(websession.read_cookie("a=1; mw_s=xyz; b=2"), "xyz")
        self.assertEqual(websession.read_cookie("mw_s=xyz"), "xyz")
        self.assertEqual(websession.read_cookie("a=1", "a"), "1")
        self.assertEqual(websession.read_cookie(""), "")
        self.assertEqual(websession.read_cookie(None), "")
        self.assertEqual(websession.read_cookie("mw_sx=1"), "")

    def test_ttl_bounds_from_env(self):
        self.assertGreaterEqual(websession.SESSION_TTL, 60)
        self.assertLessEqual(websession.SESSION_TTL, 7 * 86400)


class Tickets(unittest.TestCase):
    def setUp(self):
        websession._TICKETS.clear()

    def test_single_use(self):
        t = websession.issue_ticket()
        self.assertGreaterEqual(len(t), 32)
        self.assertTrue(websession.use_ticket(t))
        self.assertFalse(websession.use_ticket(t))
        self.assertFalse(websession.use_ticket("never-issued"))
        self.assertFalse(websession.use_ticket(""))

    def test_expires(self):
        t = websession.issue_ticket()
        with patch.object(time, "time", return_value=time.time() + websession.TICKET_TTL + 1):
            self.assertFalse(websession.use_ticket(t))

    def test_cap_evicts_oldest(self):
        with patch.object(websession, "MAX_TICKETS", 3):
            first = websession.issue_ticket()
            websession.issue_ticket(); websession.issue_ticket()
            websession.issue_ticket()   # الرابع يُخرج الأول
            self.assertEqual(len(websession._TICKETS), 3)
            self.assertFalse(websession.use_ticket(first))


if __name__ == "__main__":
    unittest.main(verbosity=1)
