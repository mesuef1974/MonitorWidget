# -*- coding: utf-8 -*-
"""Local-LLM explainer for the monitor (LM Studio, OpenAI-compatible server on 127.0.0.1 only).
Read-only by design: it explains numbers that health.py already computed; it never decides or acts.
Nothing leaves the machine. If LM Studio is off, every other feature of the monitor is unaffected."""
import json, os, re, shutil, subprocess, time, urllib.request, urllib.error
from datetime import datetime

ROOT = "http://127.0.0.1:1234"
BASE = ROOT + "/v1"
COMMIT_GATE = 85          # above this commit % we refuse to load a model: it would add to the pressure
TIMEOUT = 150             # first call may include LM Studio loading the model
PREFERRED_MODEL = "qwen/qwen3-8b"   # يُستعمل فقط إن لم يكن نموذج محمّلًا: تحميل نموذج ثانٍ يملأ بطاقة 8 GB ويبطئ الجميع

SYSTEM = (
    "أنت تعيد صياغة حقائق تقرير صحة جهاز ويندوز بجملة عربية واضحة. لكل بند اكتب سطراً واحداً بالصيغة «المفتاح: جملة» "
    "(المفتاح بالإنجليزية كما أُعطي، ثم نقطتان). الجملة من 25 كلمة أو أقل وتعتمد على «الحقائق» فقط. "
    "ممنوع تقديم نصائح أو خطوات، وممنوع ذكر أي رقم أو اسم أو سبب غير موجود في المدخلات، "
    "وممنوع التخمين عن أسباب لم تُذكر. إن كانت المعطيات قليلة اكتب جملة قصيرة تصف ما هو موجود فقط. "
    "لا تستخدم رموز تنسيق."
)

# the next step is chosen by code, never by the model: it must point to a page that really exists
STEP = {
    "mem": "افتح صفحة «العمليات والشلات» وراجع أكبر العمليات.",
    "disk": "افتح صفحة «أين ذهبت المساحة».",
    "tasks": "افتح صفحة «الجدولة» وراجع المهمة الفاشلة.",
    "backup": "افتح صفحة «الجدولة» وراجع نتيجة آخر نسخة احتياطية.",
    "hw": "افتح صفحة «صحة الجهاز» وراجع قسم العتاد.",
    "sec": "افتح صفحة «صحة الجهاز» وراجع قسم الأمان.",
    "therm": "افتح صفحة «الطاقة والحرارة».",
}


def _req(path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _loaded():
    """النماذج المحمّلة فعلًا في LM Studio؛ /v1/models يعرض كل المنزَّل فلا يكفي لمعرفتها."""
    try:
        with urllib.request.urlopen(ROOT + "/api/v0/models", timeout=5) as resp:
            rows = json.loads(resp.read().decode("utf-8")).get("data") or []
    except (OSError, ValueError):
        return []
    return [m.get("id", "") for m in rows if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm")]


def _model():
    loaded = _loaded()
    if loaded:
        return PREFERRED_MODEL if PREFERRED_MODEL in loaded else loaded[0]
    ids = [m.get("id", "") for m in (_req("/models").get("data") or [])]
    ids = [i for i in ids if i and "embed" not in i.lower()]
    if not ids:
        raise RuntimeError("no model")
    return PREFERRED_MODEL if PREFERRED_MODEL in ids else ids[0]


def _clean(s):
    s = re.sub(r"[A-Za-z]:\\[^\s،,)]*", "[مسار]", str(s))   # never send file paths
    s = re.sub(r"\s*\(فُحص قبل \d+ د\)", "", s)
    return s[:240]


def health(parts, metrics, extra=None):
    """parts = health.score()['parts']; metrics = native_metrics(). Returns {ok, text, model, ms} or {ok False, err}."""
    t0 = time.time()
    try:
        if (metrics or {}).get("commitPct", 0) >= COMMIT_GATE:
            return {"ok": False, "err": "التزام الذاكرة مرتفع جداً (%s%%). لن أشغّل نموذجاً الآن." % metrics.get("commitPct")}
        weak = [p for p in parts if p["pts"] < p["max"]]
        if not weak:
            return {"ok": True, "text": "كل البنود كاملة الدرجة. لا شيء يحتاج شرحاً.", "model": "-", "ms": 0}
        lines = ["%s | %s | الحقائق: %s" % (p["key"], p["label"], _clean(p["why"])) for p in weak]
        model = _model()
        out = _req("/chat/completions", {
            "model": model, "temperature": 0, "max_tokens": 500, "ttl": 300,   # LM Studio ejects the model 5 min after the last call
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": "البنود (المفتاح | الاسم | الحقائق):\n" + "\n".join(lines)
                          + ("\n\nسياق من القياسات:\n" + "\n".join(extra) if extra else "") + "\n/no_think"}]})
        txt = out["choices"][0]["message"]["content"]
        txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S)
        txt = re.sub(r"[*#`]+", "", txt)
        try:
            with open(__file__.replace("explain.py", "explain_last.txt"), "w", encoding="utf-8") as fh:
                fh.write(txt)   # raw model reply, for debugging the parser
        except Exception:
            pass
        said = {}
        for ln in txt.splitlines():
            t = ln.strip().lstrip("-•0123456789.) ").strip()
            t = t.replace("：", ":").replace("|", ":")
            if ":" not in t:
                continue
            head, _, rest = t.partition(":")
            rest = rest.strip()
            if ":" in rest and rest.split(":", 1)[0].strip() in ("%s" % p["label"] for p in weak):
                rest = rest.split(":", 1)[1].strip()
            head = head.strip().lower()
            for p in weak:
                if head == p["key"] or head == p["label"].lower() or p["label"].lower() in head:
                    if rest.strip():
                        said.setdefault(p["key"], rest.strip())
        res = []
        for p in weak:
            res.append("%s: %s\nالخطوة: %s" % (p["label"], said.get(p["key"]) or ("%s من %s. %s" % (p["pts"], p["max"], _clean(p["why"]))), STEP.get(p["key"], "افتح صفحة «صحة الجهاز».")))
        return {"ok": True, "text": "\n\n".join(res), "model": model, "ms": int((time.time() - t0) * 1000),
                "fallback": sum(1 for p in weak if not said.get(p["key"])), "total": len(weak)}
    except urllib.error.URLError:
        return {"ok": False, "err": "LM Studio غير مفعّل. شغّل خادمه المحلي (Developer ثم Start Server) وحمّل نموذجاً."}
    except RuntimeError as e:
        return {"ok": False, "err": "لا نموذج محمّل في LM Studio." if str(e) == "no model" else str(e)[:160]}
    except Exception as e:
        return {"ok": False, "err": "تعذّر الشرح: " + str(e)[:160]}


def _root(path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(ROOT + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=30) as resp:
        raw = resp.read().decode("utf-8") or "{}"
        return json.loads(raw)


def _lms():
    for c in (shutil.which("lms"), os.path.join(os.path.expanduser("~"), ".lmstudio", "bin", "lms.exe")):
        if c and os.path.exists(c):
            return c
    return None


def unload():
    """Eject every model currently loaded in LM Studio so its VRAM is released. Touches nothing else."""
    ids = []

    def walk(o):
        if isinstance(o, dict):
            li = o.get("loaded_instances")
            if isinstance(li, list):
                for it in li:
                    i = it.get("id") if isinstance(it, dict) else it
                    if i:
                        ids.append(str(i))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    try:
        try:
            walk(_root("/api/v1/models"))
        except urllib.error.HTTPError:
            pass
        done = 0
        for i in ids:
            try:
                _root("/api/v1/models/unload", {"instance_id": i}); done += 1
            except urllib.error.HTTPError:
                break
        if ids and done == len(ids):
            return {"ok": True, "text": "أُخرج %d نموذج من LM Studio وتحررت ذاكرة الكرت." % done}
        lms = _lms()   # fallback: the official CLI, if installed
        if lms:
            r = subprocess.run([lms, "unload", "--all"], capture_output=True, timeout=30, creationflags=0x08000000)
            if r.returncode == 0:
                return {"ok": True, "text": "أُخرجت النماذج المحمّلة (عبر lms)."}
        if not ids:
            return {"ok": True, "text": "لا نموذج محمّل في LM Studio."}
        return {"ok": False, "err": "تعذّر التفريغ آلياً. افتح LM Studio واضغط Eject بجانب النموذج."}
    except urllib.error.URLError:
        return {"ok": True, "text": "LM Studio غير مشغّل، فلا نموذج لتفريغه."}
    except Exception as e:
        return {"ok": False, "err": "تعذّر التفريغ: " + str(e)[:140]}


# ---------------------------------------------------------------- log error explainer
ERR_SYSTEM = (
    "تشرح أخطاء برنامج مراقبة ويندوز مكتوب بلغة بايثون لمستخدم تقني. اكتب جملة عربية واحدة بسيطة تقول ماذا حدث. "
    "اقتبس أسماء المتغيرات والدوال كما هي حرفياً من نص الخطأ ولا تترجمها ولا تخترع غيرها. "
    "ممنوع الحكم على أن الخطأ مستمر أو عابر أو يحتاج تدخلاً، وممنوع اقتراح حلول. "
    "إن لم يتضح المعنى من النص فقل: لا يتضح السبب من نص الخطأ. لا تستخدم رموز تنسيق."
)
_HEAD = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}):\d{2},\d+ ")


def _entries(root):
    out = []
    for name in ("monitor.log.1", "monitor.log"):   # older first
        try:
            with open(os.path.join(root, name), encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        cur = None
        for ln in lines:
            m = _HEAD.match(ln)
            if m:
                if cur:
                    out.append(cur)
                cur = {"date": m.group(1), "time": m.group(2), "body": [ln[m.end():]]}
            elif cur is not None:
                cur["body"].append(ln)
        if cur:
            out.append(cur)
    return out


def _parse(e, mine):
    body = e["body"]
    if not body or not body[0].startswith("swallowed exception"):
        return None
    exc = ""
    for ln in reversed(body):
        if ln.strip() and not ln.startswith((" ", "\t")) and not ln.startswith("Traceback"):
            exc = ln.strip()
            break
    if not exc:
        return None
    frames = []
    for i, ln in enumerate(body):
        m = re.match(r'\s*File "([^"]+)", line (\d+), in (\S+)', ln)
        if m:
            code = body[i + 1].strip() if i + 1 < len(body) and not body[i + 1].lstrip().startswith("File") else ""
            frames.append((re.split(r"[\\/]", m.group(1))[-1], m.group(3), code[:120]))
    ours = [f for f in frames if f[0] in mine] or frames
    where = ours[-1] if ours else ("?", "?", "")
    typ = exc.split(":", 1)[0].split(".")[-1]
    return {"sig": "%s@%s:%s" % (typ, where[0], where[1]), "typ": typ, "exc": _clean(exc), "where": where,
            "date": e["date"], "time": e["time"]}


ERR_FRESH_HOURS = 72  # خطأ لم يتكرر منذ أكثر من هذا يُعدّ منتهيًا فيُخفى من اللوحة (يبقى في monitor.log)


def _age_hours(g, now):
    try:
        return (now - datetime.strptime(g["date"] + " " + g["time"], "%Y-%m-%d %H:%M")).total_seconds() / 3600
    except Exception:
        return None


def errors(metrics, root, budget=4, now=None):
    """Explain the distinct errors in monitor.log seen within ERR_FRESH_HOURS. Each is explained once and cached on disk."""
    mine = {f for f in os.listdir(root) if f.endswith(".py")}
    now = now or datetime.now()
    groups = {}
    for e in _entries(root):
        r = _parse(e, mine)
        if r:
            g = groups.setdefault(r["sig"], dict(r, n=0))
            g["n"] += 1
            g["date"], g["time"] = r["date"], r["time"]
    if not groups:
        return {"ok": True, "text": "لا أخطاء مسجّلة في monitor.log. هذا هو المطلوب."}
    for g in groups.values():
        g["age"] = _age_hours(g, now)
    stale = sum(1 for g in groups.values() if g["age"] is not None and g["age"] >= ERR_FRESH_HOURS)
    groups = {k: g for k, g in groups.items() if g["age"] is None or g["age"] < ERR_FRESH_HOURS}
    hidden = "أُخفي %d نوعًا أقدم لم يتكرر منذ أكثر من %d أيام (باقية في monitor.log)." % (stale, ERR_FRESH_HOURS // 24) if stale else ""
    if not groups:
        return {"ok": True, "text": "لا أخطاء خلال آخر %d أيام. هذا هو المطلوب.%s" % (ERR_FRESH_HOURS // 24, "\n\n" + hidden if hidden else "")}
    cpath = os.path.join(root, "explain_cache.json")
    try:
        with open(cpath, encoding="utf-8") as fh:
            cache = json.load(fh)
    except Exception:
        cache = {}
    items = sorted(groups.values(), key=lambda g: (g["date"], g["time"]), reverse=True)[:8]
    note = ""
    todo = [g for g in items if "v2|" + g["sig"] not in cache][:budget]
    if todo and (metrics or {}).get("commitPct", 0) >= COMMIT_GATE:
        note = "التزام الذاكرة مرتفع، فلم أشغّل النموذج."
        todo = []
    if todo:
        try:
            model = _model()
            for g in todo:
                w = g["where"]
                msg = "الخطأ: %s\nمكانه: %s ← %s ← %s" % (g["exc"], w[0], w[1], w[2])
                out = _req("/chat/completions", {
                    "model": model, "temperature": 0, "max_tokens": 250, "ttl": 300,
                    "messages": [{"role": "system", "content": ERR_SYSTEM}, {"role": "user", "content": msg + "\n/no_think"}]})
                txt = out["choices"][0]["message"]["content"]
                txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S)
                cache["v2|" + g["sig"]] = {"text": re.sub(r"[*#`]+", "", txt).strip(), "ts": int(time.time())}
            tmp = cpath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, ensure_ascii=False)
            os.replace(tmp, cpath)
        except urllib.error.URLError:
            note = "LM Studio غير مشغّل، فعرضتُ الأخطاء بلا شرح جديد."
        except Exception as ex:
            note = "تعذّر الشرح لبعض الأخطاء: " + str(ex)[:100]
    lines = []
    for g in items:
        c = cache.get("v2|" + g["sig"])
        age = g["age"]
        st = "" if age is None else "ظهر خلال الساعة الأخيرة" if age < 1 else "قبل %d ساعة" % int(age)
        lines.append("%s — %d مرة، آخرها %s %s (%s)\n%s" % (g["typ"], g["n"], g["date"], g["time"], st, c["text"] if c else "لم يُشرح بعد. اضغط الزر مرة أخرى."))
    text = "\n\n".join(lines) + ("\n\n" + note if note else "") + ("\n\n" + hidden if hidden else "") + "\n\nتفسير آلي من نموذج صغير، قد يخطئ."
    return {"ok": True, "text": text}
