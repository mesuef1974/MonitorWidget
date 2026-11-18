"""Disk collector logic on synthetic readings (any OS): stalls, sleep, wall-clock jumps, step-failure resilience."""
import os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
r = subprocess.run([sys.executable, "-X", "utf8", "-B", os.path.join(ROOT, "diskio_collect.py"), "--selftest"],
                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
print(r.stdout, end=""); print(r.stderr, end="")
sys.exit(r.returncode)
