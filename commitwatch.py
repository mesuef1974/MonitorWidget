"""رصد قفزات الالتزام لكل عملية: يسجّل حدثًا حين يرتفع التزام عملية فجأة (حادثة 8011: حجز يتأرجح بين 52 و700 MB).

يعمل داخل agent.py كل 30 ثانية بصلاحية المستخدم (commitinfo.scan بلا سطر الأمر). لكل عملية (pid، اسم) تُحفظ قياسات آخر
WINDOW_S ثانية في الذاكرة؛ إن زاد آخر قياس عن أدنى قياس في النافذة بمقدار JUMP_MB سُجّل حدث في جدول events (مصدره commit)
باسم العملية ومشروعها، وبحدّ حدث واحد لكل اسم كل COOLDOWN_S. لا لوحة ولا ويدجت ولا كتابة غير جدول الأحداث.
العملية الجديدة لا قياس سابق لها فلا تُعدّ قفزة في أول قياس. الدوال update/find_spikes/emit نقية وتُختبر معزولة.
"""
import time

import commitinfo

INTERVAL = 30
WINDOW_S = 300
JUMP_MB = 300
COOLDOWN_S = 1800

_hist = {}   # (pid, name) -> [(ts, commitMB)]
_last = {}   # name -> وقت آخر حدث


def update(hist, rows, now, window=WINDOW_S):
    """يضيف قياس كل عملية، ويقصّ ما أقدم من النافذة، ويحذف العمليات التي اختفت."""
    seen = set()
    for r in rows:
        k = (r.get("pid"), str(r.get("name") or ""))
        seen.add(k)
        h = hist.setdefault(k, [])
        h.append((now, float(r.get("commitMB") or 0)))
        while h and now - h[0][0] > window:
            h.pop(0)
    for k in [k for k in hist if k not in seen]:
        del hist[k]


def find_spikes(hist, jump_mb=JUMP_MB):
    """قفزات: آخر قياس − أدنى قياس في النافذة ≥ jump_mb؛ تنازليًا بحجم القفزة."""
    out = []
    for (pid, name), h in hist.items():
        if len(h) < 2:
            continue
        cur, lo = h[-1][1], min(v for _, v in h)
        if cur - lo >= jump_mb:
            out.append({"pid": pid, "name": name, "from": lo, "to": cur, "delta": cur - lo, "secs": h[-1][0] - h[0][0]})
    out.sort(key=lambda s: (-s["delta"], s["name"].lower(), s["pid"] or 0))
    return out


def emit(spikes, now, last, add_event, project_of, cooldown=COOLDOWN_S):
    """يسجّل حدثًا لكل اسم لم يُسجَّل له حدث خلال cooldown؛ يعيد عدد الأحداث."""
    n = 0
    for s in spikes:
        prev = last.get(s["name"])
        if prev is not None and now - prev < cooldown:
            continue
        last[s["name"]] = int(now)
        proj = project_of(s["pid"]) or "غير مصنّف"
        add_event(now, "commit", "warn", "الالتزام: %s قفز %d MB" % (s["name"], round(s["delta"])),
                  "من %d إلى %d MB خلال نحو %d ثانية. المشروع: %s (pid %s)" % (round(s["from"]), round(s["to"]), round(s["secs"]), proj, s["pid"]))
        n += 1
    return n


def _project_of(pid):
    """اسم مشروع العملية بقواعد خريطة الالتزام (مسح واحد بسطر الأمر عند القفزة فقط)؛ None إن تعذّر."""
    try:
        import commitmap
        rows, _ = commitinfo.scan(with_cmd=True)
        rules = commitmap.load_rules()
        pid_proj = commitmap.assign(rows, rules).get(pid)
        names = {r["id"]: r["name"] for r in rules}
        names[commitmap.OTHER["id"]] = commitmap.OTHER["name"]
        return names.get(pid_proj)
    except Exception:
        return None


def job():
    import db
    now = time.time()
    rows, _ = commitinfo.scan()
    update(_hist, rows, now)
    sp = find_spikes(_hist)
    n = emit(sp, now, _last, db.add_event, _project_of) if sp else 0
    return "%d عملية%s" % (len(rows), (" | قفزة %s +%d MB" % (sp[0]["name"], round(sp[0]["delta"]))) if sp else "") + (" | حدث" if n else "")
