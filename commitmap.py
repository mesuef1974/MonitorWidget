"""خريطة التزام الذاكرة: لمن يذهب الالتزام وكيف ينزل إلى الهدف (صفحة الذاكرة).

    projects  تجميع التزام العمليات حسب المشروع بقواعد مطابقة على اسم البرنامج وسطر أمره (الابن يرث مشروع أبيه)
    idle      وسم «الحجز الخامل»: التزام كبير أضعاف مجموعة العمل، أو عملية تستعمل كرت الشاشة (CUDA يُحتسب في الالتزام)
    docker    حاويات Docker: مشروعها من مجلد compose، وذاكرتها داخل WSL، وحالة طلب الدمج الذي تخدمه (gh)
    plan      كم يلزم تحريره للنزول تحت الهدف، وما يبلغه تحرير الحجز الخامل، والبديل: ملف ترحيل أكبر (يخفض النسبة لا الاستهلاك)

القراءة كلها بصلاحية المستخدم. ما يزيد على 300 ms (nvidia-smi وdocker وgit وgh) يُجمع في خيط خلفي يُطلقه الطلب حين تقدم
النتيجة، فلا ينتظر الطلب ولا يعمل الجامع إلا حين تُفتح الصفحة. القواعد الافتراضية لمشاريع المالك، ويستبدلها ملف
commit_projects.json بجانب التطبيق إن وُجد وصحّ. الدوال النقية (classify/assign/is_idle/group/plan/parse_*) تُختبر معزولة.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

import commitinfo
from paths import CNW, ROOT

TARGET_PCT = 40                 # هدف المالك (7/11/2026): التزام دون 40%
IDLE_RATIO = 4.0                # الالتزام ≥ 4 أضعاف مجموعة العمل
IDLE_MIN_MB = 300               # وما دون ذلك لا يستحق الوسم
GPU_TTL, DOCKER_TTL, PR_TTL = 30, 60, 600
RULES_FILE = os.path.join(ROOT, "commit_projects.json")
OTHER = {"id": "other", "name": "غير مصنّف"}

# أول قاعدة مطابقة تفوز. exe: أسماء برامج بالضبط؛ cmd: مقاطع من سطر الأمر؛ dirs/containers: لحاويات Docker (مجلد compose أو الاسم)؛
# keep: مشروع لا يدخل خطوات الحاسبة لأن تحريره يُلغى تلقائيًا أو يعطّل غيره (يبقى ظاهرًا في الجدول)
DEFAULT_RULES = [
    {"id": "salma", "name": "سلمى: الإملاء والنطق", "cmd": ["\\voicedictate\\", "\\silmatts\\"]},
    {"id": "shschool", "name": "مشروع مدرسة الشحانية", "cmd": ["shschool_mvp", "delivery_manager_work"],
     "dirs": ["d:\\shschool_mvp", "\\delivery_manager_work"], "containers": ["shschool", "schoolos", "preview-slot"]},
    {"id": "future", "name": "مشروع علوم المستقبل", "cmd": ["مسابقة علوم المستقبل"], "dirs": ["مسابقة علوم المستقبل"]},
    {"id": "lmstudio", "name": "نماذج LM Studio (مشتركة)", "exe": ["llama-server.exe", "lm studio.exe", "lms.exe"], "cmd": ["\\.lmstudio\\"],
     "note": "مشتركة بين Bionic والمسابقة وسلمى والمرصد؛ مهمة المسابقة تعيد تحميل النموذج كل 5 دقائق", "keep": True},
    {"id": "monitor", "name": "مرصد الجهاز", "cmd": ["\\monitorwidget\\"]},
    {"id": "railway", "name": "برنامج Railway للمكافآت", "cmd": ["\\railway\\"], "dirs": ["\\railway"]},
    {"id": "wsl", "name": "WSL وDocker", "exe": ["vmmemwsl", "vmmem", "wslservice.exe", "wslhost.exe", "wsl.exe", "docker desktop.exe",
                                                "com.docker.backend.exe", "com.docker.build.exe", "docker.exe"],
     "note": "التزام الآلة الافتراضية كلها؛ ذاكرة كل حاوية داخلها في جدول الحاويات"},
    {"id": "claude", "name": "تطبيق Claude", "exe": ["claude.exe"]},
]

_state = {"gpu": (0.0, set()), "docker": (0.0, None), "prs": {}, "busy": False}
_lock = threading.Lock()


# ------------------------------------------------------------------ القواعد والتصنيف (نقية)
def _valid_rule(r):
    if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not isinstance(r.get("name"), str):
        return False
    if not isinstance(r.get("keep", False), bool):
        return False
    return all(isinstance(r.get(k, []), list) and all(isinstance(x, str) for x in r.get(k, [])) for k in ("exe", "cmd", "dirs", "containers"))


def load_rules(path=RULES_FILE):
    """قواعد الملف إن كان قائمة صالحة غير فارغة، وإلا الافتراضية."""
    try:
        with open(path, encoding="utf-8") as f:
            rules = json.load(f)
        if isinstance(rules, list) and rules and all(_valid_rule(r) for r in rules):
            return rules
    except (OSError, ValueError):
        pass
    return DEFAULT_RULES


def classify(row, rules):
    """معرّف المشروع لصف عملية {name, cmd}، أو None إن لم تطابقه قاعدة."""
    name, cmd = str(row.get("name") or "").lower(), str(row.get("cmd") or "").lower()
    for r in rules:
        if name in [x.lower() for x in r.get("exe", [])] or any(x.lower() in cmd for x in r.get("cmd", []) if x):
            return r["id"]
    return None


def assign(rows, rules, depth=6):
    """pid → معرّف المشروع: القاعدة أولًا، وإلا يرث مشروع أقرب أب مصنّف (خادم سلمى ابن أداة الإملاء)، وإلا other."""
    by_pid = {r["pid"]: r for r in rows}
    direct = {r["pid"]: classify(r, rules) for r in rows}
    out = {}
    for r in rows:
        pid, seen, p = r["pid"], 0, r
        proj = direct[pid]
        while proj is None and seen < depth:
            p = by_pid.get(p.get("ppid"))
            if p is None or p["pid"] == pid:
                break
            proj, seen = direct.get(p["pid"]), seen + 1
        out[pid] = proj or OTHER["id"]
    return out


def is_idle(row):
    """حجز كبير لا تستعمله العملية الآن: التزامها ≥ IDLE_MIN_MB و≥ IDLE_RATIO أضعاف مجموعة عملها."""
    c, ws = float(row.get("commitMB") or 0), float(row.get("wsMB") or 0)
    return c >= IDLE_MIN_MB and c >= IDLE_RATIO * max(ws, 1.0)


def group(rows, owner, rules, gpu=frozenset(), containers=(), top=5):
    """مشاريع مرتّبة تنازليًا بالالتزام: المجموع ومجموعة العمل والقابل للتحرير (الخامل أو كرت الشاشة) وأعلى العمليات وحاوياتها."""
    meta = {r["id"]: r for r in rules}
    meta[OTHER["id"]] = OTHER
    acc = {}
    for r in rows:
        pid = r["pid"]
        g = acc.setdefault(owner.get(pid, OTHER["id"]), {"commitMB": 0.0, "wsMB": 0.0, "freeMB": 0.0, "procs": [], "gpu": False})
        idle, on_gpu = is_idle(r), pid in gpu
        g["commitMB"] += float(r.get("commitMB") or 0)
        g["wsMB"] += float(r.get("wsMB") or 0)
        if idle or on_gpu:
            g["freeMB"] += float(r.get("commitMB") or 0)
        g["gpu"] = g["gpu"] or on_gpu
        g["procs"].append({"pid": pid, "name": str(r.get("name") or ""), "commitMB": int(round(float(r.get("commitMB") or 0))),
                           "wsMB": int(round(float(r.get("wsMB") or 0))), "idle": idle, "gpu": on_gpu})
    cont = {}
    for c in containers:
        k = c.get("project") or OTHER["id"]
        cont.setdefault(k, {"count": 0, "memMB": 0.0})
        cont[k]["count"] += 1
        cont[k]["memMB"] += float(c.get("memMB") or 0)
    out = []
    for pid_ in set(acc) | set(cont):
        g = acc.get(pid_, {"commitMB": 0.0, "wsMB": 0.0, "freeMB": 0.0, "procs": [], "gpu": False})
        m = meta.get(pid_, {"id": pid_, "name": pid_})
        procs = sorted(g["procs"], key=lambda p: (-p["commitMB"], p["name"].lower(), p["pid"]))
        out.append({"id": pid_, "name": m["name"], "note": m.get("note", ""), "keep": bool(m.get("keep")), "commitMB": int(round(g["commitMB"])), "wsMB": int(round(g["wsMB"])),
                    "freeMB": int(round(g["freeMB"])), "gpu": g["gpu"], "count": len(procs), "procs": procs[:top],
                    "containers": {"count": cont.get(pid_, {}).get("count", 0), "memMB": int(round(cont.get(pid_, {}).get("memMB", 0)))}})
    return sorted(out, key=lambda p: (p["id"] == OTHER["id"], -p["commitMB"], p["name"]))


DONE_PR = ("merged", "closed")


def stale_compose(containers):
    """مشاريع compose التي انتهى طلب دمجها (مدموج أو مغلق): {compose, project, memMB, pr, names} بالأكبر أولًا."""
    acc = {}
    for c in containers:
        pr = c.get("pr") or {}
        if c.get("compose") and pr.get("state") in DONE_PR:
            g = acc.setdefault(c["compose"], {"compose": c["compose"], "project": c.get("project"), "memMB": 0, "pr": pr, "names": []})
            g["memMB"] += int(c.get("memMB") or 0)
            g["names"].append(c["name"])
    return sorted(acc.values(), key=lambda g: (-g["memMB"], g["compose"]))


def plan(st, projects, target=TARGET_PCT, containers=()):
    """كم يلزم تحريره للنزول تحت الهدف، وخطوات بالأكبر أولًا حتى يكفي: الحجز الخامل لكل مشروع، ثم حاويات طلبات منتهية
    (ذاكرتها داخل WSL تعود إلى ويندوز تدريجيًا). other وتطبيق Claude والمشاريع keep لا تدخل الخطوات. والبديل: ملف ترحيل أكبر."""
    used, limit = float(st.get("usedGB") or 0), float(st.get("limitGB") or 0)
    if limit <= 0:
        return None
    need = max(0.0, used - limit * target / 100.0)
    cands = [{"kind": "idle", "id": p["id"], "name": p["name"], "mb": p["freeMB"]}
             for p in projects if p["freeMB"] > 0 and p["id"] not in (OTHER["id"], "claude") and not p.get("keep")]
    cands += [{"kind": "docker", "id": g["project"], "name": g["compose"], "mb": g["memMB"], "pr": g["pr"].get("number")}
              for g in stale_compose(containers) if g["memMB"] > 0]
    cands.sort(key=lambda c: -c["mb"])
    steps, got = [], 0.0
    for c in cands:
        if got >= need and steps:
            break
        got += c["mb"] / 1024.0
        steps.append({**{k: v for k, v in c.items() if k != "mb"}, "freeGB": round(c["mb"] / 1024.0, 2), "afterPct": round(100.0 * (used - got) / limit, 1)})
    idle_total = sum(c["mb"] for c in cands) / 1024.0
    return {"target": target, "needGB": round(need, 2), "met": need <= 0, "steps": steps, "idleGB": round(idle_total, 2),
            "reachable": idle_total >= need, "bestPct": round(100.0 * max(0.0, used - idle_total) / limit, 1),
            "pagefileGB": round(max(0.0, used / (target / 100.0) - limit), 1)}


# ------------------------------------------------------------------ Docker (نقية للتحليل)
_UNITS = {"b": 1 / 1048576, "kb": 1 / 1024, "kib": 1 / 1024, "mb": 1, "mib": 1, "gb": 1024, "gib": 1024}


def parse_mem(text):
    """«161.1MiB» أو «1.2GiB» → ميغابايت؛ None إن لم يُفهم."""
    m = re.match(r"\s*([\d.]+)\s*([a-zA-Z]+)", text or "")
    if not m or m.group(2).lower() not in _UNITS:
        return None
    return float(m.group(1)) * _UNITS[m.group(2).lower()]


def parse_ps(text):
    """أسطر «اسم\\tمشروع compose\\tمجلده\\tالحالة» من docker ps."""
    out = []
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 4 and parts[0].strip():
            out.append({"name": parts[0].strip(), "compose": parts[1].strip(), "dir": parts[2].strip(), "status": parts[3].strip()})
    return out


def parse_stats(text):
    """أسطر «اسم\\tالاستعمال / الحدّ» من docker stats → {الاسم: ميغابايت}."""
    out = {}
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            mb = parse_mem(parts[1].split("/")[0])
            if mb is not None:
                out[parts[0].strip()] = mb
    return out


def container_project(c, rules):
    """مشروع الحاوية: مجلد compose أولًا ثم اسم الحاوية أو مشروعها."""
    d, n = (c.get("dir") or "").lower(), ((c.get("name") or "") + " " + (c.get("compose") or "")).lower()
    for r in rules:
        if any(x.lower() in d for x in r.get("dirs", []) if x) or any(x.lower() in n for x in r.get("containers", []) if x):
            return r["id"]
    return OTHER["id"]


# ------------------------------------------------------------------ الجامعون (تُستدعى في الخيط الخلفي)
def _run(args, timeout=20, cwd=None):
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, cwd=cwd, creationflags=CNW, check=False)
        return r.returncode, r.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def _gpu_pids():
    """معرّفات العمليات التي تستعمل كرت الشاشة (nvidia-smi يعمل بلا مسؤول)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return set()
    code, out = _run([exe, "--query-compute-apps=pid", "--format=csv,noheader"], timeout=10)
    return {int(x) for x in re.findall(r"\d+", out)} if code == 0 else set()


def _pr_for_dir(d):
    """{number, state, url} لطلب الدمج الذي يخدمه مجلد الحاوية: الفرع، وإلا التزام HEAD. None إن لم يُعرف."""
    git, gh = shutil.which("git"), shutil.which("gh")
    if not (git and gh and d and os.path.isdir(d)):
        return None
    _, branch = _run([git, "-C", d, "rev-parse", "--abbrev-ref", "HEAD"], timeout=10)
    _, sha = _run([git, "-C", d, "rev-parse", "HEAD"], timeout=10)
    branch, sha = branch.strip(), sha.strip()
    query = ["--head", branch] if branch and branch != "HEAD" else (["--search", sha] if sha else None)
    if not query:
        return None
    code, out = _run([gh, "pr", "list", "--state", "all", "--limit", "1", "--json", "number,state,url", *query], timeout=30, cwd=d)
    try:
        rows = json.loads(out) if code == 0 else []
    except ValueError:
        rows = []
    return {"number": rows[0].get("number"), "state": str(rows[0].get("state", "")).lower(), "url": rows[0].get("url")} if rows else None


def _docker(rules):
    exe = shutil.which("docker")
    if not exe:
        return {"state": "unavailable", "why": "docker غير مثبت"}
    code, ps = _run([exe, "ps", "--format", '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.project.working_dir"}}\t{{.Status}}'])
    if code != 0:
        return {"state": "unavailable", "why": "Docker لا يعمل"}
    rows = parse_ps(ps)
    _, st = _run([exe, "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"], timeout=40)
    mem = parse_stats(st)
    now = time.time()
    for c in rows:
        c["memMB"] = int(round(mem.get(c["name"], 0)))
        c["project"] = container_project(c, rules)
        cached = _state["prs"].get(c["dir"])
        if c["dir"] and (cached is None or now - cached[0] > PR_TTL):
            _state["prs"][c["dir"]] = (now, _pr_for_dir(c["dir"]))
        c["pr"] = _state["prs"].get(c["dir"], (0, None))[1] if c["dir"] else None
    return {"state": "ok", "containers": rows}


def _refresh(rules):
    try:
        now = time.time()
        if now - _state["gpu"][0] > GPU_TTL:
            _state["gpu"] = (time.time(), _gpu_pids())
        if now - _state["docker"][0] > DOCKER_TTL:
            _state["docker"] = (time.time(), _docker(rules))
    finally:
        with _lock:
            _state["busy"] = False


def _kick(rules):
    """يُطلق الجامع الخلفي إن تقادمت نتيجة ولم يكن يعمل؛ لا ينتظره."""
    now = time.time()
    stale = now - _state["gpu"][0] > GPU_TTL or now - _state["docker"][0] > DOCKER_TTL
    with _lock:
        if not stale or _state["busy"]:
            return
        _state["busy"] = True
    threading.Thread(target=_refresh, args=(rules,), daemon=True, name="commitmap").start()


def compose_stop(project):
    """يوقف (stop لا حذف) حاويات مشروع compose واحد معروف في آخر قراءة. (ok, detail)."""
    snap = _state["docker"][1] or {}
    names = [c["name"] for c in snap.get("containers", []) if c.get("compose") == project]
    exe = shutil.which("docker")
    if not names or not exe:
        return False, "not_found"
    code, _ = _run([exe, "stop", *names], timeout=120)
    _state["docker"] = (0.0, _state["docker"][1])   # تُعاد القراءة في الطلب التالي
    return code == 0, ",".join(names)


def known_compose(project):
    snap = _state["docker"][1] or {}
    return isinstance(project, str) and any(c.get("compose") == project for c in snap.get("containers", []))


# ------------------------------------------------------------------ الحمولة
def payload(target=TARGET_PCT, rows=None, st=None, rules=None, gpu=None, docker=None):
    """حمولة GET /api/commit-map. المعاملات للحقن في الاختبارات؛ بدونها تُقرأ الحالة الحية ويُطلق الجامع الخلفي."""
    rules = rules or load_rules()
    if rows is None:
        rows, _ = commitinfo.scan(with_cmd=True)
        _kick(rules)
    st = st if st is not None else (commitinfo.status() or {})
    gpu = _state["gpu"][1] if gpu is None else gpu
    docker = _state["docker"][1] if docker is None else docker
    owner = assign(rows, rules)
    containers = (docker or {}).get("containers", []) if (docker or {}).get("state") == "ok" else []
    projects = group(rows, owner, rules, gpu, containers)
    out = {"ok": True, "now": int(time.time())}
    out.update({k: st.get(k) for k in ("usedGB", "limitGB", "pct")})
    out.update({"projects": projects, "plan": plan(st, projects, target, containers), "stale": stale_compose(containers), "gpuKnown": bool(gpu) or _state["gpu"][0] > 0,
                "docker": docker or {"state": "pending"}})
    return out
