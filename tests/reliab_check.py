"""Reliability panel logic (any OS): provider-scoped queries, rel_events history, failure is not «no incidents»."""
import os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
r = subprocess.run([sys.executable, "-X", "utf8", "-B", os.path.join(ROOT, "reliab.py"), "--selftest"],
                   cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
print(r.stdout, end=""); print(r.stderr, end="")
sys.exit(r.returncode)
