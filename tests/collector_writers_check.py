"""البند 33 من المجلس 19/11: كاتب واحد لكل جدول، ولا pwsh في دورة المجمّع كل 300 ث.
samples يكتبها الوكيل (j_sample) وgpu يكتبها gpuwatch.job؛ المجمّع يكتب procs وgpu_procs وigpu فقط،
وذاكرة GPU لكل عملية تُقرأ مع عدّادات المدمج في تشغيل typeperf واحد بدل Get-Counter في pwsh."""
import ast
import csv
import io
import json
import os
import re
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GPU_FUNCS = {"parse_gpu_counters", "nvidia_luid", "gpu_procs_from", "_pid_names", "read_gpu_counters", "_igpu_from"}
NV, IG = "0x00000000_0x0000d1b4", "0x00000000_0x0000e2c5"


def extract(fname, names, assigns=(), **ns):
    tree = ast.parse((ROOT / fname).read_text(encoding="utf-8-sig"))
    body = [n for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") in assigns for t in n.targets)]
    body += [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    env = {"os": os, "re": re, "json": json, "time": time, **ns}
    exec(compile(ast.Module(body=body, type_ignores=[]), "isolated-" + fname, "exec"), env)
    return env


def typeperf_csv():
    """عيّنة بصيغة typeperf: عمود الوقت ثم عدّادات المحرّكات وذاكرة المحوّلين وذاكرة العمليات."""
    h = "\\\\PC\\"
    cols = [
        (h + "GPU Engine(pid_10_luid_%s_phys_0_eng_0_engtype_3D)\\Utilization Percentage" % IG.upper(), 30.0),
        (h + "GPU Engine(pid_10_luid_%s_phys_0_eng_1_engtype_VideoDecode)\\Utilization Percentage" % IG, 5.0),
        (h + "GPU Engine(pid_20_luid_%s_phys_0_eng_0_engtype_3D)\\Utilization Percentage" % NV, 60.0),
        (h + "GPU Adapter Memory(luid_%s_phys_0)\\Dedicated Usage" % NV, 3000 * 1048576),
        (h + "GPU Adapter Memory(luid_%s_phys_0)\\Shared Usage" % NV, 100 * 1048576),
        (h + "GPU Adapter Memory(luid_%s_phys_0)\\Dedicated Usage" % IG, 128 * 1048576),
        (h + "GPU Adapter Memory(luid_%s_phys_0)\\Shared Usage" % IG, 700 * 1048576),
        (h + "GPU Process Memory(pid_20_luid_%s_phys_0)\\Dedicated Usage" % NV, 1500 * 1048576),   # ollama
        (h + "GPU Process Memory(pid_30_luid_%s_phys_0)\\Dedicated Usage" % NV, 400 * 1048576),    # chrome
        (h + "GPU Process Memory(pid_31_luid_%s_phys_0)\\Dedicated Usage" % NV, 200 * 1048576),    # chrome (عملية ثانية)
        (h + "GPU Process Memory(pid_40_luid_%s_phys_0)\\Dedicated Usage" % NV, 900 * 1048576),    # dwm: نظام
        (h + "GPU Process Memory(pid_50_luid_%s_phys_0)\\Dedicated Usage" % NV, 10 * 1048576),     # دون 20 MB
        (h + "GPU Process Memory(pid_60_luid_%s_phys_0)\\Dedicated Usage" % IG, 300 * 1048576),    # على المدمج
        (h + "GPU Process Memory(pid_70_luid_%s_phys_0)\\Dedicated Usage" % NV, 9000 * 1048576),   # أكبر من المستعمل كله
    ]
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["(PDH-CSV 4.0)"] + [c for c, _ in cols])
    w.writerow(["11/20/2026 10:00:00.000"] + [" " for _ in cols])
    w.writerow(["11/20/2026 10:00:01.000"] + [str(v) for _, v in cols])
    return out.getvalue()


PIDN = {10: "Teams", 20: "ollama", 30: "chrome", 31: "chrome", 40: "dwm", 50: "tiny", 60: "igpuapp", 70: "liar"}
TASKLIST = "\n".join('"%s.exe","%d","Console","1","1,000 K"' % (n, p) for p, n in PIDN.items())


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append(args)
        out = typeperf_csv() if args[0] == "typeperf" else TASKLIST if args[0] == "tasklist" else ""
        return types.SimpleNamespace(stdout=out.encode("utf-8"), returncode=0)


def gpu_env(run, topo):
    return extract("health.py", GPU_FUNCS, ("GPU_COUNTERS", "_SYSTEM_GPU_PROCS", "CNW"),
                   subprocess=types.SimpleNamespace(run=run), igpu_topology_cached=lambda: topo,
                   errlog=types.SimpleNamespace(swallowed=lambda: None),
                   _read_igpu_ps=lambda _v: run(["pwsh", "-Command", "fallback"]) and {"ps": True})


class GpuCounters(unittest.TestCase):
    def test_parse_keeps_process_memory_out_of_adapter_memory(self):
        rows = list(csv.reader(typeperf_csv().splitlines()))
        ns = gpu_env(FakeRun(), None)
        luids, pmem = ns["parse_gpu_counters"](rows[0], rows[-1], PIDN)
        self.assertEqual(set(luids), {NV, IG})
        self.assertEqual(luids[NV]["ded"], 3000)          # لا تطغى عليه أعمدة GPU Process Memory المنتهية بالنمط نفسه
        self.assertEqual(luids[IG]["sh"], 700)
        self.assertAlmostEqual(luids[IG]["eng"]["3d"], 30.0)
        self.assertEqual(luids[IG]["procs"], {"Teams": 35.0})
        self.assertEqual(pmem[(20, NV)], 1500)
        self.assertEqual(pmem[(60, IG)], 300)

    def test_process_list_is_nvidia_only_filtered_and_merged_by_name(self):
        rows = list(csv.reader(typeperf_csv().splitlines()))
        ns = gpu_env(FakeRun(), None)
        luids, pmem = ns["parse_gpu_counters"](rows[0], rows[-1], PIDN)
        nv = ns["nvidia_luid"](luids, 3010)
        self.assertEqual(nv, NV)
        procs = ns["gpu_procs_from"](pmem, PIDN, nv, 3010)
        self.assertEqual(procs, [("ollama", 1500), ("chrome", 600)])
        self.assertEqual(ns["gpu_procs_from"](pmem, PIDN, nv, None), [])   # بلا صف gpu حديث لا قائمة عمليات

    def test_one_typeperf_run_and_no_powershell(self):
        run = FakeRun()
        ns = gpu_env(run, {"present": True, "forcedDiscrete": ["game.exe"]})
        ig, procs = ns["read_gpu_counters"](3010)
        tools = [c[0] for c in run.calls]
        self.assertEqual(tools.count("typeperf"), 1)
        self.assertNotIn("pwsh", " ".join(map(str, tools)).lower())
        tp = next(c for c in run.calls if c[0] == "typeperf")
        self.assertIn(r"\GPU Process Memory(*)\Dedicated Usage", tp)
        self.assertEqual(ig["util3d"], 30.0)
        self.assertEqual(ig["shared_mb"], 700)
        self.assertEqual(ig["forcedDiscrete"], ["game.exe"])
        self.assertEqual(procs[0], ("ollama", 1500))

    def test_no_igpu_and_no_nvidia_runs_nothing(self):
        run = FakeRun()
        topo = {"present": False, "installed": []}
        ig, procs = gpu_env(run, topo)["read_gpu_counters"](None)
        self.assertIs(ig, topo)
        self.assertEqual((procs, run.calls), ([], []))

    def test_no_igpu_still_reads_nvidia_processes(self):
        run = FakeRun()
        topo = {"present": False, "installed": []}
        ig, procs = gpu_env(run, topo)["read_gpu_counters"](3010)
        self.assertIs(ig, topo)
        self.assertEqual(procs[0], ("ollama", 1500))


class CollectorWriters(unittest.TestCase):
    def run_main(self, vram=3010):
        tree = ast.parse((ROOT / "collector.py").read_text(encoding="utf-8-sig"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        calls = []
        now = time.time()
        rec = lambda name: (lambda *a, **k: calls.append((name, a)))
        health = types.SimpleNamespace(
            ADMIN_PROBE="unused", add_procs=rec("procs"), read_procs=lambda: [("x.exe", 1, 50, 50)],
            latest_gpu_vram=lambda: vram, read_gpu_counters=lambda v: ({"present": True, "v": v}, [("ollama", 1500)]),
            add_gpu_procs=rec("gpu_procs"), add_igpu=rec("igpu"), add_gpu=rec("gpu"),
            get_hw=lambda _: (now, {}), score=lambda *a: {}, save_daily=rec("daily"))
        db = types.SimpleNamespace(bootstrap=lambda: None, import_all=lambda: None, add_sample=rec("sample"),
                                   get_json=lambda _: {"ts": now, "tasks": []}, prune_if_due=lambda: None)
        ns = {"acquire_instance": lambda: True, "release_instance": lambda: None, "db": db, "health": health,
              "snapshot": types.SimpleNamespace(native_metrics=lambda: {"ramPct": 50}),
              "space": types.SimpleNamespace(scan_if_due=lambda *_: None), "time": time, "log": rec("log"),
              "os": types.SimpleNamespace(path=types.SimpleNamespace(exists=lambda _: False)),
              "errlog": types.SimpleNamespace(swallowed=rec("swallowed"))}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "collector-main", "exec"), ns)
        ns["main"](raise_errors=True)
        return calls

    def test_collector_does_not_write_samples_or_gpu(self):
        names = [c[0] for c in self.run_main()]
        self.assertNotIn("sample", names)
        self.assertNotIn("gpu", names)
        self.assertEqual([n for n in names if n in ("procs", "gpu_procs", "igpu", "daily")], ["procs", "gpu_procs", "igpu", "daily"])

    def test_vram_for_adapter_matching_comes_from_gpuwatch_row(self):
        calls = self.run_main(vram=2048)
        self.assertEqual(next(a for n, a in calls if n == "igpu")[1]["v"], 2048)

    def test_each_table_has_one_writer_in_the_source(self):
        """samples: agent.j_sample وحده؛ gpu: gpuwatch.job وحده (اختبارات المستودع والبيانات الوهمية مستثناة)."""
        writers = {"samples": set(), "gpu": set()}
        for f in ROOT.glob("*.py"):
            src = f.read_text(encoding="utf-8-sig")
            for table, pat in (("samples", r"\bdb\.add_sample\("), ("gpu", r"\bhealth\.add_gpu\(")):
                if re.search(pat, src):
                    writers[table].add(f.name)
        self.assertEqual(writers, {"samples": {"agent.py"}, "gpu": {"gpuwatch.py"}})


if __name__ == "__main__":
    unittest.main()
