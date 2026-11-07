"""خريطة التزام الذاكرة (commitmap): تصنيف العمليات حسب المشروع ووراثة الأب، وسم الحجز الخامل، التجميع، الحاسبة نحو الهدف
مع استثناء keep وClaude وغير المصنّف وإضافة حاويات الطلبات المنتهية، تحليل مخرجات docker، القواعد من الملف، وحمولة المسار
بصفوف محقونة؛ ثم مسار POST /api/docker-stop: رفض المجهول، و428 قبل الإيقاف، والإيقاف بعد التأكيد.
معزول: paths وcommitinfo بدائل وهمية؛ لا docker ولا nvidia-smi ولا gh.

Run: python -X utf8 -B tests/commitmap_check.py
"""
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix="mw_commitmap_")
sys.modules["paths"] = types.SimpleNamespace(ROOT=TMP, CNW=0)
sys.modules["commitinfo"] = types.SimpleNamespace(scan=lambda with_cmd=False: ([], 0), status=dict)
import commitmap as cm

R = cm.DEFAULT_RULES


def row(pid, name, commit, ws, cmd="", ppid=0):
    return {"pid": pid, "ppid": ppid, "name": name, "commitMB": commit, "wsMB": ws, "cmd": cmd}


ROWS = [
    row(10, "pythonw.exe", 2800, 80, r'"C:\Users\o\Documents\Scripts\VoiceDictate\.venv\Scripts\pythonw.exe" dictate.pyw'),
    row(11, "python.exe", 5100, 110, r"python.exe -X utf8 C:\Users\o\Documents\Scripts\SilmaTTS\server.py", ppid=10),
    row(12, "python.exe", 400, 150, "python.exe -c pass", ppid=11),                     # حفيد غير مطابق: يرث سلمى
    row(20, "llama-server.exe", 5900, 830, r"C:\Users\o\.lmstudio\extensions\llama-server.exe --model x"),
    row(30, "vmmemWSL", 3700, 2400),
    row(40, "claude.exe", 700, 500),
    row(50, "unknownapp.exe", 400, 300),
    row(60, "pythonw.exe", 90, 60, r"pythonw.exe C:\Users\o\Documents\Scripts\MonitorWidget\server.py"),
]
ST = {"usedGB": 30.0, "limitGB": 50.0, "pct": 60.0}


class Classify(unittest.TestCase):
    def test_rules_by_command_and_exe(self):
        self.assertEqual(cm.classify(ROWS[0], R), "salma")
        self.assertEqual(cm.classify(ROWS[1], R), "salma")
        self.assertEqual(cm.classify(ROWS[3], R), "lmstudio")
        self.assertEqual(cm.classify(ROWS[4], R), "wsl")
        self.assertEqual(cm.classify(ROWS[5], R), "claude")
        self.assertEqual(cm.classify(ROWS[7], R), "monitor")
        self.assertIsNone(cm.classify(ROWS[6], R))

    def test_arabic_folder_matches(self):
        r = row(1, "python.exe", 10, 10, r"python D:\مسابقة علوم المستقبل\backend\knowledge\model_startup.py")
        self.assertEqual(cm.classify(r, R), "future")

    def test_children_inherit_parent_project_and_unknown_is_other(self):
        owner = cm.assign(ROWS, R)
        self.assertEqual(owner[12], "salma")
        self.assertEqual(owner[50], "other")

    def test_parent_cycle_does_not_hang(self):
        rows = [row(1, "a.exe", 1, 1, ppid=2), row(2, "b.exe", 1, 1, ppid=1)]
        self.assertEqual(cm.assign(rows, R), {1: "other", 2: "other"})


class Idle(unittest.TestCase):
    def test_idle_needs_size_and_ratio(self):
        self.assertTrue(cm.is_idle(row(1, "x", 2800, 80)))
        self.assertFalse(cm.is_idle(row(1, "x", 250, 10)))       # أصغر من الحدّ
        self.assertFalse(cm.is_idle(row(1, "x", 3700, 2400)))    # يستعمل ذاكرته
        self.assertTrue(cm.is_idle(row(1, "x", 400, 0)))         # مجموعة عمل صفرية لا تقسم على صفر


class GroupAndPlan(unittest.TestCase):
    def setUp(self):
        self.owner = cm.assign(ROWS, R)
        self.containers = [
            {"name": "a-web-1", "compose": "a", "project": "shschool", "memMB": 300, "pr": {"number": 1, "state": "merged"}},
            {"name": "a-worker-1", "compose": "a", "project": "shschool", "memMB": 200, "pr": {"number": 1, "state": "merged"}},
            {"name": "b-web-1", "compose": "b", "project": "shschool", "memMB": 900, "pr": {"number": 2, "state": "open"}},
            {"name": "dev-db", "compose": "dev", "project": "shschool", "memMB": 250, "pr": None},
        ]
        self.projects = cm.group(ROWS, self.owner, R, gpu={20}, containers=self.containers)

    def by(self, pid):
        return next(p for p in self.projects if p["id"] == pid)

    def test_totals_free_and_flags(self):
        s = self.by("salma")
        self.assertEqual((s["commitMB"], s["count"]), (8300, 3))
        self.assertEqual(s["freeMB"], 7900)   # الحفيد (400/150) ليس خاملًا
        self.assertEqual(s["procs"][0]["name"], "python.exe")
        lm = self.by("lmstudio")
        self.assertTrue(lm["gpu"] and lm["keep"])
        self.assertEqual(lm["freeMB"], 5900)  # كرت الشاشة يُحسب قابلًا للتحرير، والحاسبة تستثنيه بـ keep
        self.assertEqual(self.by("shschool")["containers"], {"count": 4, "memMB": 1650})
        self.assertEqual(self.projects[-1]["id"], "other")   # غير المصنّف آخرًا

    def test_stale_compose_only_finished_prs(self):
        st = cm.stale_compose(self.containers)
        self.assertEqual([(g["compose"], g["memMB"], g["names"]) for g in st], [("a", 500, ["a-web-1", "a-worker-1"])])

    def test_plan_skips_keep_claude_other_and_adds_docker(self):
        pl = cm.plan(ST, self.projects, 40, self.containers)
        self.assertEqual(pl["needGB"], 10.0)
        ids = [(s["kind"], s["id"]) for s in pl["steps"]]
        self.assertEqual(ids, [("idle", "salma"), ("docker", "shschool")])
        self.assertNotIn("lmstudio", [s["id"] for s in pl["steps"]])
        self.assertFalse(pl["reachable"])
        self.assertAlmostEqual(pl["idleGB"], round((7900 + 500) / 1024, 2))
        self.assertEqual(pl["pagefileGB"], 25.0)   # 30 / 0.40 - 50
        self.assertEqual(pl["steps"][1]["pr"], 1)

    def test_plan_stops_once_enough(self):
        pl = cm.plan({"usedGB": 22.0, "limitGB": 50.0}, self.projects, 40, self.containers)
        self.assertEqual(pl["needGB"], 2.0)
        self.assertEqual([s["id"] for s in pl["steps"]], ["salma"])
        self.assertTrue(pl["reachable"])

    def test_plan_already_met(self):
        pl = cm.plan({"usedGB": 10.0, "limitGB": 50.0}, self.projects, 40)
        self.assertTrue(pl["met"])
        self.assertEqual(pl["needGB"], 0.0)

    def test_plan_without_limit(self):
        self.assertIsNone(cm.plan({}, self.projects))


class Docker(unittest.TestCase):
    def test_parse_mem_units(self):
        self.assertAlmostEqual(cm.parse_mem("161.1MiB"), 161.1)
        self.assertAlmostEqual(cm.parse_mem("1.5GiB"), 1536.0)
        self.assertAlmostEqual(cm.parse_mem("512KiB"), 0.5)
        self.assertIsNone(cm.parse_mem("n/a"))

    def test_parse_ps_and_stats(self):
        ps = "web-1\tproj\tC:\\w\\r1\tUp 5 minutes\nfront\t\t\tUp 2 hours\n\nbad line\n"
        self.assertEqual(cm.parse_ps(ps), [{"name": "web-1", "compose": "proj", "dir": "C:\\w\\r1", "status": "Up 5 minutes"},
                                            {"name": "front", "compose": "", "dir": "", "status": "Up 2 hours"}])
        self.assertEqual(cm.parse_stats("web-1\t261.2MiB / 768MiB\nx\t--\n"), {"web-1": 261.2})

    def test_container_project(self):
        self.assertEqual(cm.container_project({"name": "w", "dir": r"C:\Users\o\delivery_manager_work_wt\runner-1"}, R), "shschool")
        self.assertEqual(cm.container_project({"name": "schoolos-main-preview-front", "dir": ""}, R), "shschool")
        self.assertEqual(cm.container_project({"name": "redis", "dir": r"C:\other"}, R), "other")


class Rules(unittest.TestCase):
    def test_file_overrides_when_valid(self):
        path = os.path.join(TMP, "rules.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"id": "x", "name": "س", "cmd": ["\\x\\"]}], f)
        self.assertEqual(cm.load_rules(path)[0]["id"], "x")

    def test_invalid_or_missing_file_falls_back(self):
        path = os.path.join(TMP, "bad.json")
        for bad in ('{"id": 1}', "[]", '[{"id": "x", "name": "y", "cmd": "not-a-list"}]', '[{"id": "x", "name": "y", "keep": "yes"}]', "not json"):
            with open(path, "w", encoding="utf-8") as f:
                f.write(bad)
            self.assertIs(cm.load_rules(path), cm.DEFAULT_RULES, bad)
        self.assertIs(cm.load_rules(os.path.join(TMP, "missing.json")), cm.DEFAULT_RULES)


class Payload(unittest.TestCase):
    def test_payload_with_injected_state(self):
        docker = {"state": "ok", "containers": [{"name": "a-web-1", "compose": "a", "project": "shschool", "memMB": 300,
                                                 "pr": {"number": 7, "state": "closed"}}]}
        p = cm.payload(40, rows=ROWS, st=ST, rules=R, gpu={20}, docker=docker)
        self.assertEqual((p["usedGB"], p["limitGB"], p["pct"]), (30.0, 50.0, 60.0))
        self.assertEqual(p["projects"][0]["id"], "salma")
        self.assertEqual(p["stale"][0]["compose"], "a")
        self.assertEqual(p["plan"]["target"], 40)
        json.dumps(p, allow_nan=False)

    def test_docker_unavailable_keeps_projects(self):
        p = cm.payload(40, rows=ROWS, st=ST, rules=R, gpu=set(), docker={"state": "unavailable", "why": "Docker لا يعمل"})
        self.assertEqual(p["docker"]["state"], "unavailable")
        self.assertEqual(p["stale"], [])


class DockerStopRoute(unittest.TestCase):
    """POST /api/docker-stop عبر routes_destructive الحقيقي بوحدات وهمية: المجهول 409، والمعروف 428 ثم الإيقاف بعد التأكيد."""

    def setUp(self):
        fakes = {name: types.SimpleNamespace() for name in ("db", "errlog", "insight", "procclose", "secure", "space", "winguard")}
        fakes["errlog"].swallowed = lambda: None
        fakes["secure"].is_admin = lambda: False
        fakes["db"]._lock = __import__("threading").Lock()
        self.logged = []
        fakes["db"].conn = lambda: types.SimpleNamespace(execute=lambda *a: self.logged.append(a), commit=lambda: None)
        self.patcher = patch.dict(sys.modules, fakes)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        sys.modules["paths"].__dict__.update(ENGINE="", MCCFG="", POLICY="", PROT_ENGINE_DIR="", PWSH="", TIER_TASK="", TIERS={})
        sys.modules.pop("routes_destructive", None)
        import routes_destructive
        self.rd = routes_destructive
        self.addCleanup(sys.modules.pop, "routes_destructive", None)
        cm._state["docker"] = (0.0, {"state": "ok", "containers": [{"name": "a-web-1", "compose": "a"}, {"name": "a-worker-1", "compose": "a"}]})
        self.addCleanup(cm._state.__setitem__, "docker", (0.0, None))

    def test_unknown_or_bad_project_rejected(self):
        self.assertEqual(self.rd.post("/api/docker-stop", {"project": "zzz"})[0], 409)
        self.assertEqual(self.rd.post("/api/docker-stop", {"project": 5})[0], 400)

    def test_confirm_then_stop(self):
        calls = []
        with patch.object(cm.shutil, "which", return_value="docker"), patch.object(cm, "_run", lambda args, **k: (calls.append(args), (0, ""))[1]):
            code, body = self.rd.post("/api/docker-stop", {"project": "a"})
            self.assertEqual(code, 428)
            self.assertEqual(calls, [])                       # لا إيقاف قبل التأكيد
            code, body = self.rd.post("/api/docker-stop", {"project": "a", "confirm": body["confirm"]})
        self.assertEqual(code, 200)
        self.assertEqual(calls, [["docker", "stop", "a-web-1", "a-worker-1"]])
        self.assertEqual(body["containers"], ["a-web-1", "a-worker-1"])
        self.assertEqual(self.logged[0][1][1], "dockerstop")


if __name__ == "__main__":
    unittest.main(verbosity=1)
