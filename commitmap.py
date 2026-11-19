"""خريطة التزام الذاكرة: لمن يذهب الالتزام وكيف ينزل إلى الهدف (صفحة الذاكرة).

    projects  تجميع التزام العمليات حسب المشروع بقواعد مطابقة على اسم البرنامج وسطر أمره (الابن يرث مشروع أبيه)
    idle      وسم «الحجز الخامل»: التزام كبير أضعاف مجموعة العمل، أو عملية تستعمل كرت الشاشة (CUDA يُحتسب في الالتزام)
    docker    حاويات Docker: مشروعها من مجلد compose، وذاكرتها داخل WSL، وحالة طلب الدمج الذي تخدمه (gh)
    plan      كم يلزم تحريره للنزول تحت الهدف، وما يبلغه تحرير الحجز الخامل، والبديل: ملف ترحيل أكبر (يخفض النسبة لا الاستهلاك)

القراءة كلها بصلاحية المستخدم. ما يزيد على 300 ms (nvidia-smi وdocker وgit وgh) يُجمع في خيط خلفي يُطلقه الطلب حين تقدم
النتيجة، فلا ينتظر الطلب ولا يعمل الجامع إلا حين تُفتح الصفحة. الخادم المرتفع لا يشغّل docker وgit وgh في مستودعات المستخدم
أبدًا (البند 9 من المجلس 19/11: إعداداتها وإضافاتها يكتبها المستخدم): يُطلق المهمة الثابتة المحدودة PROBE_TASK التي تشغّل
`commitmap.py --probe` من الكود المحمي وتكتب PROBE_FILE، ويقرأ الملف بتحقق صارم. nvidia-smi وdocker stop بمسار مطلق محمي.
القواعد في المستودع عامة؛ مشاريع المالك في commit_projects.json غير المودَع (البند 5: لا أسماء مشاريع أو جهات في المستودع
العام) وتُقدَّم على العامة. الدوال النقية (classify/assign/is_idle/group/plan/parse_*) تُختبر معزولة.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import commitinfo
import secure
from paths import CNW, ROOT

TARGET_PCT = 40                 # هدف المالك (7/11/2026): التزام دون 40%
IDLE_RATIO = 4.0                # الالتزام ≥ 4 أضعاف مجموعة العمل
IDLE_MIN_MB = 300               # وما دون ذلك لا يستحق الوسم
GPU_TTL, DOCKER_TTL, PR_TTL = 30, 60, 600
_HOME = os.environ.get("USERPROFILE") or os.path.expanduser("~")
# ملف مشاريع المالك: بجانب الكود (التطوير)، وإلا في مجلد المستودع (النشر المحمي لا ينسخ غير المودَع). قراءته آمنة من الخادم
# المرتفع: أسماء وأنماط مطابقة يتحقق منها _valid_rule، لا شيء منها يُنفَّذ.
RULES_FILES = (os.path.join(ROOT, "commit_projects.json"), os.path.join(_HOME, "Documents", "Scripts", "MonitorWidget", "commit_projects.json"))
OTHER = {"id": "other", "name": "غير مصنّف"}
PROBE_TASK = "MonitorWidget-CommitProbe"
PROBE_FILE = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(_HOME, "AppData", "Local"), "MonitorWidget", "commit_probe.json")
PROBE_MAX_BYTES = 256 * 1024
PROBE_WAIT_S = 45
_SYS = os.environ.get("SystemRoot") or r"C:\Windows"
SCHTASKS = os.path.join(_SYS, "System32", "schtasks.exe")
NVIDIA_SMI = (os.path.join(_SYS, "System32", "nvidia-smi.exe"), r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe")
DOCKER = (r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",)
DOCKER_CFG = os.path.join(secure.SECURE_DIR, "dockercfg")   # إعداد docker فارغ محمي للمرتفع: لا إضافات ولا سياقات من ~\.docker

# أول قاعدة مطابقة تفوز. exe: أسماء برامج بالضبط؛ cmd: مقاطع من سطر الأمر؛ dirs/containers: لحاويات Docker (مجلد compose أو الاسم)؛
# keep: مشروع لا يدخل خطوات الحاسبة لأن تحريره يُلغى تلقائيًا أو يعطّل غيره (يبقى ظاهرًا في الجدول)
DEFAULT_RULES = [
    {"id": "lmstudio", "name": "نماذج LM Studio (مشتركة)", "exe": ["llama-server.exe", "lm studio.exe", "lms.exe"], "cmd": ["\\.lmstudio\\"],
     "note": "مشتركة بين عدة مشاريع؛ قد تعيد مهامها تحميل النموذج بعد إخراجه", "keep": True},
    {"id": "monitor", "name": "مرصد الجهاز", "cmd": ["\\monitorwidget\\"]},
    {"id": "wsl", "name": "WSL وDocker", "exe": ["vmmemwsl", "vmmem", "wslservice.exe", "wslhost.exe", "wsl.exe", "docker desktop.exe",
                                                "com.docker.backend.exe", "com.docker.build.exe", "docker.exe", "wslrelay.exe"],
     "note": "التزام الآلة الافتراضية كلها؛ ذاكرة كل حاوية داخلها في جدول الحاويات"},
    {"id": "claude", "name": "تطبيق Claude", "exe": ["claude.exe"], "cmd": [".claude\\shell-snapshots"]},
    {"id": "browser", "name": "Edge وWebView2", "exe": ["msedge.exe", "msedgewebview2.exe"],
     "note": "نوافذ التطبيق والمتصفح؛ يتحرر بإغلاق التبويبات والنوافذ غير اللازمة", "keep": True},
    {"id": "device", "name": "برامج الجهاز والتعريفات (Lenovo وNahimic)", "keep": True,
     "cmd": ["\\programdata\\lenovo\\", "nahimic", "\\driverstore\\filerepository\\"],
     "note": "خدمات الشركة المصنّعة والتعريفات؛ تُخفَّض بتعطيل بدئها لا بإغلاقها من هنا"},
    {"id": "windows", "name": "ويندوز والنظام", "keep": True,
     "exe": ["explorer.exe", "msmpeng.exe", "dwm.exe", "startmenuexperiencehost.exe", "searchhost.exe", "searchindexer.exe",
             "shellexperiencehost.exe", "textinputhost.exe", "sihost.exe", "svchost.exe", "system", "registry", "memory compression",
             "csrss.exe", "winlogon.exe", "lsass.exe", "fontdrvhost.exe", "ctfmon.exe", "runtimebroker.exe", "taskhostw.exe",
             "widgets.exe", "nissrv.exe", "securityhealthservice.exe", "spoolsv.exe", "audiodg.exe",
             "systemsettings.exe", "applicationframehost.exe", "appactions.exe", "dllhost.exe", "conhost.exe", "unsecapp.exe",
             "useroobebroker.exe", "defendersessionhelper.exe", "windowspackagemanagerserver.exe"],
     "note": "عمليات النظام؛ لا تُقفل ولا تدخل خطوات الحاسبة"},
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


def load_rules(path=None):
    """قواعد المالك من أول ملف صالح (قائمة غير فارغة، معرّفاتها لا تصطدم بالعامة) ثم العامة؛ وإلا العامة وحدها."""
    reserved = {r["id"] for r in DEFAULT_RULES} | {OTHER["id"]}
    for p in ((path,) if path else RULES_FILES):
        try:
            with open(p, encoding="utf-8-sig") as f:
                rules = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(rules, list) and rules and all(_valid_rule(r) and r["id"] not in reserved for r in rules):
            return rules + DEFAULT_RULES
    return DEFAULT_RULES


def classify(row, rules):
    """معرّف المشروع لصف عملية {name, cmd}، أو None إن لم تطابقه قاعدة."""
    name, cmd = str(row.get("name") or "").lower(), str(row.get("cmd") or "").lower()
    for r in rules:
        if name in [x.lower() for x in r.get("exe", [])] or any(x.lower() in cmd for x in r.get("cmd", []) if x):
            return r["id"]
    return None


def assign(rows, rules, depth=6):
    """pid → معرّف المشروع: القاعدة أولًا، وإلا يرث مشروع أقرب أب مصنّف (خادم النطق ابن أداة الإملاء)، وإلا other."""
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
def _run(args, timeout=20, cwd=None, env=None):
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, cwd=cwd, env=env, creationflags=CNW, check=False)
        return r.returncode, r.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def _tool(fixed, name, elevated):
    """مسار أداة: أول مسار مطلق ثابت موجود (مجلدات لا يكتبها إلا المسؤول)؛ وPATH مقبول بصلاحية المستخدم وحدها."""
    for p in fixed:
        if os.path.isfile(p):
            return p
    return None if elevated else shutil.which(name)


def _gpu_pids():
    """معرّفات العمليات التي تستعمل كرت الشاشة (nvidia-smi يعمل بلا مسؤول)."""
    exe = _tool(NVIDIA_SMI, "nvidia-smi", secure.is_admin())
    if not exe:
        return set()
    code, out = _run([exe, "--query-compute-apps=pid", "--format=csv,noheader"], timeout=10)
    return {int(x) for x in re.findall(r"\d+", out)} if code == 0 else set()


def _pr_for_dir(d):
    """{number, state, url} لطلب الدمج الذي يخدمه مجلد الحاوية: الفرع، وإلا التزام HEAD. None إن لم يُعرف.
    بصلاحية المستخدم وحدها (المجس المحدود أو خادم غير مرتفع): المستودع وإعدادات git وgh يكتبها المستخدم."""
    if secure.is_admin():
        return None
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


def collect_docker():
    """الحاويات وذاكرتها وطلب الدمج لكل مجلد compose، بلا مشروع (يُحسب عند العرض). بصلاحية المستخدم وحدها."""
    if secure.is_admin():
        return {"state": "unavailable", "why": "القراءة المباشرة ممنوعة بصلاحية المسؤول"}
    exe = _tool(DOCKER, "docker", False)
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
        cached = _state["prs"].get(c["dir"])
        if c["dir"] and (cached is None or now - cached[0] > PR_TTL):
            _state["prs"][c["dir"]] = (now, _pr_for_dir(c["dir"]))
        c["pr"] = _state["prs"].get(c["dir"], (0, None))[1] if c["dir"] else None
        c["prTs"] = _state["prs"].get(c["dir"], (0, None))[0] if c["dir"] else 0
    return {"state": "ok", "containers": rows}


def _s(v, n=400):
    return v[:n] if isinstance(v, str) else ""


def parse_probe(raw):
    """نتيجة المجس (نص JSON كتبه برنامج بصلاحية المستخدم) → حالة docker بحقول محدّدة الأنواع والأطوال؛ None إن لم تصحّ."""
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("ts"), (int, float)) or d.get("state") not in ("ok", "unavailable"):
        return None
    if d["state"] != "ok":
        return {"state": "unavailable", "why": _s(d.get("why"), 200) or "Docker لا يعمل"}, float(d["ts"])
    rows = d.get("containers")
    if not isinstance(rows, list) or len(rows) > 500:
        return None
    out = []
    for c in rows:
        if not isinstance(c, dict) or not _s(c.get("name")):
            return None
        mem = c.get("memMB")
        pr = c.get("pr")
        if isinstance(pr, dict) and isinstance(pr.get("number"), int) and not isinstance(pr.get("number"), bool):
            url = _s(pr.get("url"))
            pr = {"number": pr["number"], "state": _s(pr.get("state"), 20).lower(), "url": url if url.startswith("https://github.com/") else None}
        else:
            pr = None
        out.append({"name": _s(c["name"], 200), "compose": _s(c.get("compose"), 200), "dir": _s(c.get("dir")), "status": _s(c.get("status"), 100),
                    "memMB": int(mem) if isinstance(mem, (int, float)) and not isinstance(mem, bool) and 0 <= mem < 10**7 else 0, "pr": pr})
    return {"state": "ok", "containers": out}, float(d["ts"])


def _read_probe():
    try:
        with open(PROBE_FILE, "rb") as f:
            raw = f.read(PROBE_MAX_BYTES + 1)
    except OSError:
        return None
    return parse_probe(raw.decode("utf-8", "replace")) if len(raw) <= PROBE_MAX_BYTES else None


def _docker_via_probe():
    """الخادم المرتفع: يُطلق المهمة الثابتة المحدودة وينتظر نتيجة أحدث من الإطلاق (في الخيط الخلفي)."""
    t0 = time.time()
    code, _ = _run([SCHTASKS, "/run", "/tn", PROBE_TASK], timeout=15)
    if code != 0:
        return {"state": "unavailable", "why": "مهمة القراءة المحدودة غير مسجّلة؛ أعد النشر المحمي"}
    deadline = t0 + PROBE_WAIT_S
    while time.time() < deadline:
        got = _read_probe()
        if got and got[1] >= t0 - 1:
            return got[0]
        time.sleep(1)
    got = _read_probe()
    return got[0] if got else {"state": "unavailable", "why": "لم تُجب مهمة القراءة المحدودة"}


def _docker(rules):
    snap = _docker_via_probe() if secure.is_admin() else collect_docker()
    for c in snap.get("containers", []) if snap.get("state") == "ok" else []:
        c["project"] = container_project(c, rules)
    return snap


def probe_main():
    """نقطة دخول المهمة المحدودة: تكتب PROBE_FILE ذريًّا. ترفض العمل مرتفعة (لا git ولا gh في مستودع المستخدم بصلاحية مسؤول)."""
    if secure.is_admin():
        return 2
    try:   # كل تشغيل عملية جديدة: ذاكرة PR_TTL تُحمل في الملف نفسه (prTs لكل مجلد) فلا يُستدعى gh كل دقيقة
        with open(PROBE_FILE, encoding="utf-8") as f:
            for c in json.load(f).get("containers", []):
                if isinstance(c, dict) and isinstance(c.get("dir"), str) and isinstance(c.get("prTs"), (int, float)):
                    _state["prs"][c["dir"]] = (float(c["prTs"]), c.get("pr"))
    except (OSError, ValueError, AttributeError):
        pass
    snap = collect_docker()
    snap["ts"] = time.time()
    os.makedirs(os.path.dirname(PROBE_FILE), exist_ok=True)
    tmp = PROBE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, PROBE_FILE)
    return 0


def _refresh(rules):
    try:
        now = time.time()
        if now - _state["gpu"][0] > GPU_TTL:
            _state["gpu"] = (time.time(), _gpu_pids())
        if now - _state["docker"][0] > DOCKER_TTL:
            _state["docker"] = (time.time(), _docker(rules))
    except Exception:   # خيط خلفي: لا يُسقط الخادم، والعلامة busy تُرفع في finally
        _state["docker"] = (time.time(), {"state": "unavailable", "why": "تعذّرت القراءة"})
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


def _docker_cmd():
    """(exe، معاملات قبل الأمر، البيئة) لـ docker. المرتفع: مسار مطلق في Program Files، وإعداد فارغ محمي بدل ~\\.docker
    (إضافات CLI وخطّافاتها وسياقاته يكتبها المستخدم)، وبيئة بلا DOCKER_*؛ فيتصل بأنبوب docker_engine الافتراضي."""
    if not secure.is_admin():
        return _tool(DOCKER, "docker", False), [], None
    exe = _tool(DOCKER, "docker", True)
    try:
        os.makedirs(DOCKER_CFG, exist_ok=True)
    except OSError:
        return None, [], None
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("DOCKER_")}
    return exe, ["--config", DOCKER_CFG], env


def compose_stop(project):
    """يوقف (stop لا حذف) حاويات مشروع compose واحد معروف في آخر قراءة. (ok, detail)."""
    snap = _state["docker"][1] or {}
    names = [c["name"] for c in snap.get("containers", []) if c.get("compose") == project]
    exe, args, env = _docker_cmd()
    if not names or not exe:
        return False, "not_found"
    code, _ = _run([exe, *args, "stop", *names], timeout=120, env=env)
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


if __name__ == "__main__" and sys.argv[1:] == ["--probe"]:
    sys.exit(probe_main())
