"""Restore a backup into a disposable service-read sandbox; never opens the live DB.
This measures DB/service-read recovery only: external task definitions, secrets,
configuration files and quarantined content need a separate recovery exercise.
"""
import argparse, glob, json, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", help="explicit backup file to validate")
    parser.add_argument("--backup-dir", default=os.path.join(HERE, "backups"))
    parser.add_argument("--sandbox-parent", default=os.path.join(HERE, "tests", ".sandboxes"))
    args = parser.parse_args()
    files = sorted(glob.glob(os.path.join(args.backup_dir, "monitor-*.db")), key=os.path.getmtime)
    if not args.backup and not files:
        print("FAIL required backup missing")
        return 1
    src = os.path.abspath(args.backup or files[-1])
    started = time.monotonic()
    os.makedirs(args.sandbox_parent, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mw_restore_", dir=args.sandbox_parent) as sandbox:
        shutil.copy2(src, os.path.join(sandbox, "monitor.db"))
        # Import only copied modules: logs, migrations and DB writes stay here.
        for name in os.listdir(HERE):
            if name.endswith(".py"):
                shutil.copy2(os.path.join(HERE, name), os.path.join(sandbox, name))
        runtime = os.path.join(sandbox, "runtime", "sqlite")
        os.makedirs(runtime)
        for name in ("sqlite3.dll", "manifest.json"):
            shutil.copy2(os.path.join(HERE, "runtime", "sqlite", name), os.path.join(runtime, name))
        child = r'''import sys
sys.path.insert(0,sys.argv[1])
from sqlite_runtime import ensure_sqlite
ensure_sqlite()
import json,sqlite3,time
import db,dbview,infra
c=db.bootstrap()
check=c.execute("PRAGMA integrity_check").fetchone()[0]
assert check == "ok",check
assert not c.execute("PRAGMA foreign_key_check").fetchall()
info=db.info()
history=db.history("all")
view=dbview.overview()
ops=infra.overview(c)
assert info["schema"] == ops["db"]["schema"] == view["schema"]
assert "series" in history and "tables" in view
last=c.execute("SELECT max(ts) FROM samples").fetchone()[0]
print(json.dumps({"integrity":check,"schema":info["schema"],"tables":len(view["tables"]),"service_reads":["db.info","db.history","dbview.overview","infra.overview"],"last_sample":last,"rpo_observed_seconds":max(0,int(time.time()-last)) if last else None}))
c.close()
'''
        run = subprocess.run([sys.executable, "-I", "-B", "-c", child, sandbox], cwd=sandbox,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if run.returncode:
            print("FAIL isolated restore/service reads", run.stderr[-1500:])
            return 1
        data = json.loads(run.stdout.strip().splitlines()[-1])
        data["rto_observed_seconds"] = round(time.monotonic() - started, 3)
        data["backup"] = os.path.basename(src)
        data["scope"] = "database + isolated service reads; not full Windows/task/file recovery"
        print("PASS isolated restore", json.dumps(data, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
