r"""Owner helper: python set_backup_dir.py E:\MWBackup   (empty string clears it). Validates the folder, then stores the
setting the same way space._setting_set does (JSON-encoded value). Safe to run while the server is up (SQLite WAL)."""

from sqlite_runtime import ensure_sqlite
ensure_sqlite()
import json, os, sqlite3, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    if len(sys.argv) != 2:
        print("usage: python set_backup_dir.py <folder on another disk | \"\">"); return 2
    d = sys.argv[1].strip()
    if d:
        d = os.path.abspath(d)
        if os.path.splitdrive(d)[0].upper() == os.path.splitdrive(HERE)[0].upper():
            print("refused: this is the same disk as the database; choose another disk"); return 1
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".mw_probe")
        open(probe, "w").close(); os.remove(probe)
    c = sqlite3.connect(os.path.join(HERE, "monitor.db"), timeout=15)
    c.execute("INSERT INTO settings(key,value,ts) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
              ("backup.extra_dir", json.dumps(d), int(time.time())))
    c.commit(); c.close()
    print("backup.extra_dir =", repr(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
