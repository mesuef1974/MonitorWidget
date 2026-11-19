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

# قواعد مالك مصطنعة بأسماء عامة (البند 5: لا أسماء مشاريع حقيقية في المستودع العام)، كما يقرؤها load_rules من commit_projects.json
OWNER_RULES = [
    {"id": "dictate", "name": "أداة الإملاء", "cmd": ["\\dictatetool\\", "\\ttsserver\\"]},
    {"id": "proja", "name": "المشروع أ", "cmd": ["proja_mvp", "proja_work"], "dirs": ["d:\\proja_mvp", "\\proja_work"],
     "containers": ["proja", "appos", "preview-slot"]},
    {"id": "projb", "name": "المشروع ب", "cmd": ["مشروع المسابقة"], "dirs": ["مشروع المسابقة"]},
    {"id": "projc", "name": "المنصة ج", "cmd": ["manage.py runserver 127.0.0.1:8777", "manage.py neo_worker"]},
    {"id": "usage", "name": "أداة الاستخدام", "cmd": ["\\usage_tool\\"]},
    {"id": "rewards", "name": "برنامج المكافآت", "cmd": ["\\rewards\\", "run_vlm.py"], "dirs": ["\\rewards"]},
]
R = OWNER_RULES + cm.DEFAULT_RULES


def row(pid, name, commit, ws, cmd="", ppid=0):
    return {"pid": pid, "ppid": ppid, "name": name, "commitMB": commit, "wsMB": ws, "cmd": cmd}


ROWS = [
    row(10, "pythonw.exe", 2800, 80, r'"C:\Users\o\Documents\Scripts\DictateTool\.venv\Scripts\pythonw.exe" dictate.pyw'),
    row(11, "python.exe", 5100, 110, r"python.exe -X utf8 C:\Users\o\Documents\Scripts\TtsServer\server.py", ppid=10),
    row(12, "python.exe", 400, 150, "python.exe -c pass", ppid=11),                     # حفيد غير مطابق: يرث أداة الإملاء
    row(20, "llama-server.exe", 5900, 830, r"C:\Users\o\.lmstudio\extensions\llama-server.exe --model x"),
    row(30, "vmmemWSL", 3700, 2400),
    row(40, "claude.exe", 700, 500),
    row(50, "unknownapp.exe", 400, 300),
    row(60, "pythonw.exe", 90, 60, r"pythonw.exe C:\Users\o\Documents\Scripts\MonitorWidget\server.py"),
]
ST = {"usedGB": 30.0, "limitGB": 50.0, "pct": 60.0}


class Classify(unittest.TestCase):
    def test_rules_by_command_and_exe(self):
        self.assertEqual(cm.classify(ROWS[0], R), "dictate")
        self.assertEqual(cm.classify(ROWS[1], R), "dictate")
        self.assertEqual(cm.classify(ROWS[3], R), "lmstudio")
        self.assertEqual(cm.classify(ROWS[4], R), "wsl")
        self.assertEqual(cm.classify(ROWS[5], R), "claude")
        self.assertEqual(cm.classify(ROWS[7], R), "monitor")
        self.assertIsNone(cm.classify(ROWS[6], R))

    def test_arabic_folder_matches(self):
        r = row(1, "python.exe", 10, 10, r"python D:\مشروع المسابقة\backend\knowledge\model_startup.py")
        self.assertEqual(cm.classify(r, R), "projb")

    def test_unclassified_groups_found_live_on_8_11(self):
        cases = {
            "projc": [row(1, "python.exe", 70, 70, r"python.exe manage.py runserver 127.0.0.1:8777"),
                      row(2, "python.exe", 70, 70, r"python.exe manage.py neo_worker"),
                      row(3, "pwsh.exe", 30, 10, "pwsh.exe -NoExit -Command python manage.py neo_worker")],
            "rewards": [row(4, "python.exe", 60, 40, r"C:\Users\o\.venvs\guide_extract\Scripts\python.exe run_vlm.py lesson_3_1 d220")],
            "usage": [row(5, "pythonw.exe", 70, 2, r"pythonw.exe C:\Users\o\usage_tool\src\serve.py")],
            "device": [row(6, "NahimicSvc64.exe", 60, 8, r"C:\WINDOWS\System32\NahimicSvc64.exe /start all"),
                       row(7, "MessagingPlugin.exe", 60, 40, r"C:\ProgramData\Lenovo\Udc\Hosts\x64\MessagingPlugin.exe x"),
                       row(8, "ipf_helper.exe", 2, 8, r"C:\WINDOWS\System32\DriverStore\FileRepository\ipf_cpu.inf\ipf_helper.exe")],
            "windows": [row(9, "SystemSettings.exe", 90, 59), row(10, "conhost.exe", 8, 5), row(11, "DllHost.exe", 9, 26)],
            "wsl": [row(12, "wslrelay.exe", 2, 10)],
            "claude": [row(13, "bash.exe", 6, 8, r'bash.exe -c "source C:\Users\o\.claude\shell-snapshots\snapshot-bash.sh"')],
        }
        for proj, rows in cases.items():
            for r in rows:
                self.assertEqual(cm.classify(r, R), proj, r["name"] + " " + r["cmd"][:40])
        keep = {x["id"] for x in R if x.get("keep")}
        self.assertIn("device", keep)
        self.assertNotIn("projc", keep)
        self.assertIsNone(cm.classify(row(20, "python.exe", 1, 1, "python.exe -c pass"), R))

    def test_children_inherit_parent_project_and_unknown_is_other(self):
        owner = cm.assign(ROWS, R)
        self.assertEqual(owner[12], "dictate")
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
            {"name": "a-web-1", "compose": "a", "project": "proja", "memMB": 300, "pr": {"number": 1, "state": "merged"}},
            {"name": "a-worker-1", "compose": "a", "project": "proja", "memMB": 200, "pr": {"number": 1, "state": "merged"}},
            {"name": "b-web-1", "compose": "b", "project": "proja", "memMB": 900, "pr": {"number": 2, "state": "open"}},
            {"name": "dev-db", "compose": "dev", "project": "proja", "memMB": 250, "pr": None},
        ]
        self.projects = cm.group(ROWS, self.owner, R, gpu={20}, containers=self.containers)

    def by(self, pid):
        return next(p for p in self.projects if p["id"] == pid)

    def test_totals_free_and_flags(self):
        s = self.by("dictate")
        self.assertEqual((s["commitMB"], s["count"]), (8300, 3))
        self.assertEqual(s["freeMB"], 7900)   # الحفيد (400/150) ليس خاملًا
        self.assertEqual(s["procs"][0]["name"], "python.exe")
        lm = self.by("lmstudio")
        self.assertTrue(lm["gpu"] and lm["keep"])
        self.assertEqual(lm["freeMB"], 5900)  # كرت الشاشة يُحسب قابلًا للتحرير، والحاسبة تستثنيه بـ keep
        self.assertEqual(self.by("proja")["containers"], {"count": 4, "memMB": 1650})
        self.assertEqual(self.projects[-1]["id"], "other")   # غير المصنّف آخرًا

    def test_stale_compose_only_finished_prs(self):
        st = cm.stale_compose(self.containers)
        self.assertEqual([(g["compose"], g["memMB"], g["names"]) for g in st], [("a", 500, ["a-web-1", "a-worker-1"])])

    def test_plan_skips_keep_claude_other_and_adds_docker(self):
        pl = cm.plan(ST, self.projects, 40, self.containers)
        self.assertEqual(pl["needGB"], 10.0)
        ids = [(s["kind"], s["id"]) for s in pl["steps"]]
        self.assertEqual(ids, [("idle", "dictate"), ("docker", "proja")])
        self.assertNotIn("lmstudio", [s["id"] for s in pl["steps"]])
        self.assertFalse(pl["reachable"])
        self.assertAlmostEqual(pl["idleGB"], round((7900 + 500) / 1024, 2))
        self.assertEqual(pl["pagefileGB"], 25.0)   # 30 / 0.40 - 50
        self.assertEqual(pl["steps"][1]["pr"], 1)

    def test_plan_stops_once_enough(self):
        pl = cm.plan({"usedGB": 22.0, "limitGB": 50.0}, self.projects, 40, self.containers)
        self.assertEqual(pl["needGB"], 2.0)
        self.assertEqual([s["id"] for s in pl["steps"]], ["dictate"])
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
        self.assertEqual(cm.container_project({"name": "w", "dir": r"C:\Users\o\proja_work_wt\runner-1"}, R), "proja")
        self.assertEqual(cm.container_project({"name": "appos-main-preview-front", "dir": ""}, R), "proja")
        self.assertEqual(cm.container_project({"name": "redis", "dir": r"C:\other"}, R), "other")


class Rules(unittest.TestCase):
    def test_owner_file_precedes_generic_rules(self):
        path = os.path.join(TMP, "rules.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"id": "x", "name": "س", "cmd": ["\\x\\"]}], f)
        rules = cm.load_rules(path)
        self.assertEqual([r["id"] for r in rules], ["x"] + [r["id"] for r in cm.DEFAULT_RULES])

    def test_repo_carries_no_owner_projects(self):
        ids = {r["id"] for r in cm.DEFAULT_RULES}
        self.assertEqual(ids, {"lmstudio", "monitor", "wsl", "claude", "browser", "device", "windows"})

    def test_default_search_uses_first_valid_file(self):
        a, b = os.path.join(TMP, "a.json"), os.path.join(TMP, "b.json")
        with open(b, "w", encoding="utf-8") as f:
            json.dump([{"id": "y", "name": "ص"}], f)
        with patch.object(cm, "RULES_FILES", (a, b)):
            self.assertEqual(cm.load_rules()[0]["id"], "y")

    def test_invalid_or_missing_file_falls_back(self):
        path = os.path.join(TMP, "bad.json")
        for bad in ('{"id": 1}', "[]", '[{"id": "x", "name": "y", "cmd": "not-a-list"}]', '[{"id": "x", "name": "y", "keep": "yes"}]', "not json",
                    '[{"id": "windows", "name": "يطغى على قاعدة عامة"}]', '[{"id": "other", "name": "محجوز"}]'):
            with open(path, "w", encoding="utf-8") as f:
                f.write(bad)
            self.assertIs(cm.load_rules(path), cm.DEFAULT_RULES, bad)
        self.assertIs(cm.load_rules(os.path.join(TMP, "missing.json")), cm.DEFAULT_RULES)


class Payload(unittest.TestCase):
    def test_payload_with_injected_state(self):
        docker = {"state": "ok", "containers": [{"name": "a-web-1", "compose": "a", "project": "proja", "memMB": 300,
                                                 "pr": {"number": 7, "state": "closed"}}]}
        p = cm.payload(40, rows=ROWS, st=ST, rules=R, gpu={20}, docker=docker)
        self.assertEqual((p["usedGB"], p["limitGB"], p["pct"]), (30.0, 50.0, 60.0))
        self.assertEqual(p["projects"][0]["id"], "dictate")
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
        with patch.object(cm, "_docker_cmd", return_value=("docker", [], None)), patch.object(cm, "_run", lambda args, **k: (calls.append(args), (0, ""))[1]):
            code, body = self.rd.post("/api/docker-stop", {"project": "a"})
            self.assertEqual(code, 428)
            self.assertEqual(calls, [])                       # لا إيقاف قبل التأكيد
            code, body = self.rd.post("/api/docker-stop", {"project": "a", "confirm": body["confirm"]})
        self.assertEqual(code, 200)
        self.assertEqual(calls, [["docker", "stop", "a-web-1", "a-worker-1"]])
        self.assertEqual(body["containers"], ["a-web-1", "a-worker-1"])
        self.assertEqual(self.logged[0][1][1], "dockerstop")


class ElevatedNeverRunsUserTools(unittest.TestCase):
    """البند 9 من المجلس 19/11: الخادم المرتفع لا يشغّل git ولا gh ولا docker القرائي ولا أداة من PATH؛ يُطلق المهمة المحدودة."""

    def setUp(self):
        p = patch.object(cm.secure, "is_admin", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.calls = []

    def fake_run(self, args, **k):
        self.calls.append((args, k))
        return 0, ""

    def test_no_git_gh_or_direct_docker_when_elevated(self):
        with patch.object(cm, "_run", self.fake_run), patch.object(cm.shutil, "which", side_effect=AssertionError("PATH lookup")):
            self.assertIsNone(cm._pr_for_dir(TMP))
            self.assertEqual(cm.collect_docker()["state"], "unavailable")
            self.assertEqual(cm.probe_main(), 2)
        self.assertEqual(self.calls, [])

    def test_tool_resolution_ignores_path_when_elevated(self):
        with patch.object(cm.shutil, "which", return_value=r"C:\Users\o\bin\docker.exe"):
            self.assertIsNone(cm._tool((os.path.join(TMP, "missing.exe"),), "docker", True))
            self.assertEqual(cm._tool((os.path.join(TMP, "missing.exe"),), "docker", False), r"C:\Users\o\bin\docker.exe")

    def test_elevated_docker_stop_uses_protected_empty_config(self):
        exe = os.path.join(TMP, "docker.exe")
        open(exe, "w").close()
        cfg = os.path.join(TMP, "dockercfg")
        with patch.object(cm, "DOCKER", (exe,)), patch.object(cm, "DOCKER_CFG", cfg), patch.dict(os.environ, {"DOCKER_HOST": "tcp://x:1", "DOCKER_CONFIG": "C:\\u"}):
            got, args, env = cm._docker_cmd()
        self.assertEqual((got, args), (exe, ["--config", cfg]))
        self.assertFalse([k for k in env if k.upper().startswith("DOCKER_")])
        self.assertTrue(os.path.isdir(cfg))

    def test_elevated_refresh_triggers_fixed_task_and_reads_validated_file(self):
        probe = os.path.join(TMP, "probe.json")

        def run(args, **k):
            self.calls.append((args, k))
            with open(probe, "w", encoding="utf-8") as f:
                json.dump({"ts": __import__("time").time(), "state": "ok", "containers": [
                    {"name": "a-web-1", "compose": "a", "dir": r"D:\proja_mvp", "status": "Up", "memMB": 300,
                     "pr": {"number": 3, "state": "MERGED", "url": "javascript:alert(1)"}}]}, f)
            return 0, ""
        with patch.object(cm, "_run", run), patch.object(cm, "PROBE_FILE", probe):
            snap = cm._docker(R)
        self.assertEqual([c[0] for c in self.calls], [[cm.SCHTASKS, "/run", "/tn", cm.PROBE_TASK]])
        c = snap["containers"][0]
        self.assertEqual((c["project"], c["memMB"], c["pr"]), ("proja", 300, {"number": 3, "state": "merged", "url": None}))

    def test_missing_task_reports_unavailable(self):
        with patch.object(cm, "_run", lambda a, **k: (1, "")):
            self.assertEqual(cm._docker(R)["state"], "unavailable")


class ProbeMain(unittest.TestCase):
    """المجس المحدود: يكتب الملف، ويحمل ذاكرة طلبات الدمج بين تشغيلاته فلا يستدعي gh قبل PR_TTL."""

    def test_writes_file_and_reuses_pr_cache(self):
        probe = os.path.join(TMP, "probe_main.json")
        exe = os.path.join(TMP, "docker_probe.exe")
        open(exe, "w").close()
        ps = "w-1\tw\tC:\\w\tUp\n"
        prs = []

        def run(args, **k):
            return (0, ps) if args[1] == "ps" else (0, "w-1\t10MiB / 1GiB\n")
        with patch.object(cm.secure, "is_admin", return_value=False), patch.object(cm, "PROBE_FILE", probe), \
                patch.object(cm, "DOCKER", (exe,)), patch.object(cm, "_run", run), \
                patch.object(cm, "_pr_for_dir", lambda d: (prs.append(d), {"number": 9, "state": "open", "url": None})[1]):
            for _ in range(2):
                cm._state["prs"] = {}   # كل تشغيل عملية جديدة
                self.assertEqual(cm.probe_main(), 0)
        self.assertEqual(prs, ["C:\\w"])
        with open(probe, encoding="utf-8") as f:
            snap, _ = cm.parse_probe(f.read())
        self.assertEqual((snap["containers"][0]["memMB"], snap["containers"][0]["pr"]["number"]), (10, 9))


class ProbeParsing(unittest.TestCase):
    def test_rejects_malformed_or_oversized(self):
        for bad in ("not json", "[]", '{"ts": "x", "state": "ok"}', '{"ts": 1, "state": "ok", "containers": "x"}',
                    '{"ts": 1, "state": "ok", "containers": [{"name": 5}]}', '{"ts": 1, "state": "weird"}'):
            self.assertIsNone(cm.parse_probe(bad), bad)
        many = json.dumps({"ts": 1, "state": "ok", "containers": [{"name": "n"}] * 501})
        self.assertIsNone(cm.parse_probe(many))

    def test_coerces_fields(self):
        snap, ts = cm.parse_probe(json.dumps({"ts": 5, "state": "ok", "containers": [
            {"name": "n" * 999, "memMB": True, "pr": {"number": True}}, {"name": "m", "memMB": -4, "pr": "x"}]}))
        self.assertEqual(ts, 5.0)
        self.assertEqual([(len(c["name"]), c["memMB"], c["pr"]) for c in snap["containers"]], [(200, 0, None), (1, 0, None)])


if __name__ == "__main__":
    unittest.main(verbosity=1)
