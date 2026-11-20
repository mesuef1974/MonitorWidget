"""Recoverable quarantine transactions for local files (stdlib only).
SQLite and filesystem cannot commit atomically. A durable intent, UUID destination,
non-overwriting same-volume rename, and recovery under a process-shared mutex make
interrupted transitions explicit. No PID killing or expiring lease can steal live work.
The mutex serializes cooperating application processes, not a hostile local admin.
"""
import contextlib, ctypes, hashlib, json, os, stat, threading, time, uuid

import safedel

_locks = {}
_locks_guard = threading.Lock()


@contextlib.contextmanager
def operation_lock(root):
    key = hashlib.sha256(os.path.normcase(os.path.abspath(root)).encode("utf-8")).hexdigest()
    with _locks_guard:
        local = _locks.setdefault(key, threading.RLock())
    if not local.acquire(timeout=30):
        raise TimeoutError("quarantine operation busy")
    try:
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel.CreateMutexW.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.CreateMutexW(None, False, "Local\\MonitorWidget-Quarantine-" + key)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            acquired = False
            try:
                result = kernel.WaitForSingleObject(handle, 30000)
                if result not in (0, 0x80):  # acquired, or abandoned by a dead thread/process
                    raise TimeoutError("quarantine operation busy")
                acquired = True
                yield
            finally:
                if acquired:
                    kernel.ReleaseMutex(handle)
                kernel.CloseHandle(handle)
        else:
            import fcntl
            lockfile = os.path.join(root, ".operation.lock")
            with open(lockfile, "a+b") as handle:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("quarantine operation busy")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        local.release()


def _inside(path, root):
    try:
        return os.path.commonpath((os.path.normcase(path), os.path.normcase(root))) == os.path.normcase(root) and os.path.normcase(path) != os.path.normcase(root)
    except ValueError:
        return False


def _safe_path(path):
    """Reject symlinks/junctions in every existing component, including the leaf."""
    path = os.path.abspath(path)
    current = path
    while True:
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise OSError("quarantine path contains a reparse point")
        parent = os.path.dirname(current)
        if parent == current:
            return path
        current = parent


def _safe_tree(path):
    _safe_path(path)
    if os.path.isdir(path):
        deadline = time.monotonic() + 120
        for folder, dirs, files in os.walk(path, topdown=True, followlinks=False):
            for name in dirs + files:
                if time.monotonic() >= deadline:
                    raise TimeoutError("quarantine traversal deadline exceeded")
                _safe_path(os.path.join(folder, name))


PURGE_STARTED = "purge started"   # op_error prefix: deletion began inside this item, so a folder may be incomplete
RESTORED_KEEP_DAYS = 30


def _partial(row):
    return PURGE_STARTED in (row.get("op_error") or "")


def _makedirs(path, root):
    if os.name == "nt":
        safedel.Root(root).makedirs(path)
    else:
        os.makedirs(path, exist_ok=True)


def _identity(path):
    info = os.stat(path, follow_symlinks=False)
    return json.dumps([info.st_dev, info.st_ino])


def _rename_no_replace(src, dst, src_root, dst_root):
    """Atomic same-volume rename that fails if dst already exists. On Windows it runs by handle (safedel): the source is opened
    without following a reparse point and must really lie inside src_root, and the destination is a name inside a validated
    handle of its folder in dst_root, so a junction planted after the checks cannot redirect the move."""
    if os.name == "nt":
        safedel.Root(src_root).move(src, dst_root, dst)
    else:
        # Portable file fallback also never replaces a competing target. Directory
        # moves require an atomic no-replace API, so refuse rather than emulate it.
        if os.path.isdir(src):
            raise OSError("atomic directory quarantine requires Windows")
        os.link(src, dst, follow_symlinks=False)
        os.unlink(src)


class QuarantineStore:
    def __init__(self, db, root, connection, allowed_origins, days=7):
        self.db, self.connection = db, connection
        self.root = os.path.abspath(root)
        self.origins = tuple(os.path.abspath(p) for p in allowed_origins)
        self.days = days

    @contextlib.contextmanager
    def _operation(self):
        _safe_path(self.root)
        os.makedirs(self.root, exist_ok=True)
        _safe_path(self.root)
        with operation_lock(self.root):
            self._ensure_schema()
            self._recover()
            yield

    def _ensure_schema(self):
        with self.db._lock:
            c = self.connection()
            columns = {r[1] for r in c.execute("PRAGMA table_info(quarantine)")}
            for name, kind in (("op_uuid", "TEXT"), ("op_updated", "INTEGER"), ("op_error", "TEXT"), ("op_identity", "TEXT")):
                if name not in columns:
                    c.execute("ALTER TABLE quarantine ADD COLUMN " + name + " " + kind)
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_quarantine_op_uuid ON quarantine(op_uuid) WHERE op_uuid IS NOT NULL")
            c.commit()

    def _rows(self, query, args=()):
        with self.db._lock:
            return [dict(row) for row in self.connection().execute(query, args)]

    def _mark(self, qid, status, expected=None, error=None):
        with self.db._lock:
            c = self.connection()
            sql = "UPDATE quarantine SET status=?,op_updated=?,op_error=? WHERE id=?"
            args = [status, int(time.time()), error, qid]
            if expected is not None:
                sql += " AND status=?"
                args.append(expected)
            changed = c.execute(sql, args).rowcount
            c.commit()
            return changed == 1

    def _claim(self, row, status):
        """Atomic state reservation from the row's current state (held, or failed for a retry); also bind legacy rows to their file identity."""
        path = self._qpath(row["qpath"])
        identity = _identity(path) if os.path.lexists(path) else row.get("op_identity")
        if row.get("op_identity") and identity != row["op_identity"]:
            raise OSError("quarantined object changed")
        with self.db._lock:
            c = self.connection()
            changed = c.execute("UPDATE quarantine SET status=?,op_identity=COALESCE(op_identity,?),op_uuid=COALESCE(op_uuid,?),op_updated=?,op_error=NULL WHERE id=? AND status=?", (status, identity, uuid.uuid4().hex, int(time.time()), row["id"], row.get("status") or "held")).rowcount
            c.commit()
            return changed == 1

    def _qpath(self, path):
        path = os.path.abspath(path)
        if not _inside(path, self.root):
            raise OSError("quarantine destination outside its root")
        return _safe_path(path)

    def _original(self, path):
        path = os.path.abspath(path)
        if not any(_inside(path, root) for root in self.origins) or _inside(path, self.root):
            raise OSError("original path outside allowed disposable roots")
        return _safe_path(path)

    def _origin_root(self, path):
        for root in self.origins:
            if _inside(path, root):
                return root
        raise OSError("original path outside allowed disposable roots")

    def _matches(self, row, path):
        return not row.get("op_identity") or row["op_identity"] == _identity(path)

    def _recover(self):
        # The kernel lock is owned for the ENTIRE filesystem operation. Acquiring
        # it proves no cooperating live owner is still moving/restoring/purging.
        for row in self._rows("SELECT * FROM quarantine WHERE status IN ('moving','restoring','purging')"):
            try:
                qpath = self._qpath(row["qpath"])
                original = self._original(row["orig"])
                qexists, oexists = os.path.lexists(qpath), os.path.lexists(original)
                status = row["status"]
                if status == "moving":
                    if qexists and not oexists and self._matches(row, qpath):
                        self._mark(row["id"], "held")
                    else:
                        self._mark(row["id"], "failed", error="interrupted move; original preserved or manual reconciliation required")
                elif status == "restoring":
                    if not qexists and oexists and self._matches(row, original):
                        self._mark(row["id"], "restored")
                    elif qexists and self._matches(row, qpath):
                        self._mark(row["id"], "held", error="interrupted restore; quarantined copy preserved")
                    else:
                        self._mark(row["id"], "failed", error="interrupted restore requires reconciliation")
                elif not qexists:
                    self._mark(row["id"], "purged")
                elif self._matches(row, qpath):
                    self._mark(row["id"], "held", error=PURGE_STARTED + ": interrupted; remaining content preserved")
                else:
                    self._mark(row["id"], "failed", error="quarantined object changed")
            except OSError as exc:
                self._mark(row["id"], "failed", error=((PURGE_STARTED + ": ") if row["status"] == "purging" else "") + str(exc)[:180])

    def move(self, path, action_id=None, size=lambda path: os.path.getsize(path)):
        with self._operation():
            original = self._original(path)
            _safe_tree(original)
            identity = _identity(original)
            if os.stat(original).st_dev != os.stat(self.root).st_dev:
                raise OSError("cross-volume quarantine refused; atomic move required")
            operation = uuid.uuid4().hex
            destination = self._qpath(os.path.join(self.root, operation, os.path.basename(original)))
            amount, now = size(original), int(time.time())
            with self.db._lock:
                c = self.connection()
                cur = c.execute("INSERT INTO quarantine(ts,orig,qpath,bytes,expires,status,action_id,op_uuid,op_updated,op_identity) VALUES(?,?,?,?,?,'moving',?,?,?,?)", (now, original, destination, amount, now + self.days*86400, action_id, operation, now, identity))
                c.commit()
                qid = cur.lastrowid
            try:
                _makedirs(os.path.dirname(destination), self.root)   # by handle: never created through a planted junction
                _safe_path(destination)
                if _identity(original) != identity:
                    raise OSError("source changed before quarantine move")
                _rename_no_replace(original, destination, self._origin_root(original), self.root)
            except OSError as exc:
                self._mark(qid, "failed", expected="moving", error=str(exc)[:200])
                raise
            if _identity(destination) != identity:
                self._mark(qid, "failed", expected="moving", error="moved object identity mismatch; manual reconciliation required")
                raise OSError("moved object identity mismatch; content preserved")
            self._mark(qid, "held", expected="moving")
            return qid

    def listing(self):
        with self._operation():
            return self._rows("SELECT id,ts,orig,qpath,bytes,expires,status,op_error FROM quarantine WHERE status IN ('held','failed','moving','restoring','purging') ORDER BY ts DESC")

    def restore(self, qid):
        try:
            with self._operation():
                # failed: a purge that could not delete (file in use) leaves the content in place; it stays restorable
                rows = self._rows("SELECT * FROM quarantine WHERE id=? AND status IN ('held','failed')", (qid,))
                if not rows:
                    return {"ok": False, "error": "عنصر الحجر غير متاح للاسترجاع"}
                row = rows[0]
                source, target = self._qpath(row["qpath"]), self._original(row["orig"])
                _safe_tree(source)
                if os.path.lexists(target):
                    return {"ok": False, "error": "المسار الأصلي مشغول؛ لم يُستبدل"}
                if not os.path.exists(source) or not self._matches(row, source):
                    return {"ok": False, "error": "عنصر الحجر مفقود أو تغير"}
                if _partial(row) and os.path.isdir(source):
                    # a purge already started deleting inside this folder: what is left may be incomplete
                    return {"ok": False, "partial": True, "error": "بدأ تفريغ هذا المجلد سابقًا؛ المحتوى قد يكون ناقصًا فلم يُسترجع"}
                _makedirs(os.path.dirname(target), self._origin_root(target))   # after the checks, by handle
                _safe_path(target)
                if not self._claim(row, "restoring"):
                    return {"ok": False, "error": "العنصر مشغول"}
                try:
                    _rename_no_replace(source, target, self.root, self._origin_root(target))
                except OSError as exc:
                    self._mark(qid, "held", expected="restoring", error=str(exc)[:200])
                    return {"ok": False, "error": "تعذر الاسترجاع دون استبدال؛ المحتوى محفوظ في الحجر"}
                self._mark(qid, "restored", expected="restoring")
                return {"ok": True}
        except OSError:
            return {"ok": False, "error": "تعذر الاسترجاع: مسار غير آمن أو العملية مشغولة"}

    def restored_paths(self, days=None):
        """{normalized original path: identity or None} of items the owner restored in the last `days` days. The cleanup skips
        such a file only while it is the same object (identity), since a restored file keeps its old modification time and would
        otherwise be quarantined again within the hour; a new file with the same name is cleaned as usual."""
        cut = int(time.time()) - (RESTORED_KEEP_DAYS if days is None else days) * 86400
        rows = self._rows("SELECT orig,op_identity FROM quarantine WHERE status='restored' AND COALESCE(op_updated,ts)>=?", (cut,))
        return {os.path.normcase(os.path.abspath(r["orig"])): r["op_identity"] for r in rows if r["orig"]}

    @staticmethod
    def is_restored(path, kept):
        """path is a file the owner restored (the same object; a legacy row without identity counts within the time window)."""
        key = os.path.normcase(os.path.abspath(path))
        if key not in kept:
            return False
        if not kept[key]:
            return True
        try:
            return _identity(path) == kept[key]
        except OSError:
            return False

    def held_bytes(self):
        return sum(r["bytes"] or 0 for r in self._rows("SELECT bytes FROM quarantine WHERE status='held'"))

    def _over_cap(self, cap_bytes):
        """Held rows not yet expired, oldest first, that must go so what stays held fits under cap_bytes (the size cap, item 13 of
        the 19/11 council: the 7-day hold reached 20 GB on C). Expired rows are purged anyway, so they are not counted here."""
        now = int(time.time())
        rows = self._rows("SELECT * FROM quarantine WHERE status='held' AND expires>? ORDER BY ts ASC, id ASC", (now,))
        excess = sum(r["bytes"] or 0 for r in rows) - cap_bytes
        out = []
        for r in rows:
            if excess <= 0:
                break
            out.append(r)
            excess -= r["bytes"] or 0
        return out

    def purge(self, force=False, cap_bytes=None):
        """Permanently delete held items whose hold has expired; force=True (explicit owner action) ignores the expiry.
        cap_bytes: also delete the oldest held items, before their hold ends, until what stays held fits under the cap.
        A failed row whose content is still in the quarantine is retried (a file in use at the last purge); one whose content
        is gone is left to _sweep."""
        count, amount, capped = 0, 0, 0
        with self._operation():
            rows = self._rows("SELECT * FROM quarantine WHERE status IN ('held','failed')" + ("" if force else " AND expires<=?"), () if force else (int(time.time()),))
            if not force and cap_bytes is not None and cap_bytes >= 0:
                extra = self._over_cap(cap_bytes)
                capped = len(extra)
                rows += extra
            guard = safedel.Root(self.root)
            for row in rows:
                started = _partial(row)
                try:
                    path = self._qpath(row["qpath"])
                    if row["status"] == "failed" and not os.path.lexists(path):
                        continue
                    # no textual _safe_tree here: remove_tree goes by handle and removes a planted link as a link, so a junction
                    # planted inside a held item must not block its purge forever
                    if os.path.exists(path) and not self._matches(row, path):
                        raise OSError("quarantined object changed")
                    if not self._claim(row, "purging"):
                        continue
                    started = True
                    if os.path.lexists(path):
                        safedel.remove_tree(path, guard)  # by handle, links removed as links; never ignore deletion failures
                    self._mark(row["id"], "purged", expected="purging")
                    count += 1
                    amount += row["bytes"] or 0
                except OSError as exc:
                    self._mark(row["id"], "failed", error=((PURGE_STARTED + ": ") if started else "") + str(exc)[:180])
            dirs, rows = self._sweep()
        return f"purged {count} ({amount / 2**20:.1f} MB), over cap {capped}, empty dirs {dirs}, failed rows {rows}"

    def _sweep(self):
        """After a purge: remove the empty per-operation folders it left behind, and the failed rows older than a day with nothing in
        the quarantine: an in-use file refused the move (WinError 32), or a purge already deleted the content. No state is left to
        reconcile whether or not the original is still in place (a temp file often is gone by then, and such rows stayed forever).
        rmdir refuses a folder that holds anything, so nothing quarantined is ever touched."""
        dirs = rows = 0
        guard = safedel.Root(self.root)
        for name in os.listdir(self.root):
            path = os.path.join(self.root, name)
            try:
                info = os.lstat(path)
                if stat.S_ISDIR(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                    guard.remove(path)   # empty folder only (by handle)
                    dirs += 1
            except OSError:
                pass
        cut = int(time.time()) - 86400
        for row in self._rows("SELECT id,orig,qpath FROM quarantine WHERE status='failed' AND COALESCE(op_updated,ts)<?", (cut,)):
            try:
                qpath = os.path.abspath(row["qpath"])
                if _inside(qpath, self.root) and not os.path.lexists(qpath):
                    with self.db._lock:
                        c = self.connection()
                        rows += c.execute("DELETE FROM quarantine WHERE id=? AND status='failed'", (row["id"],)).rowcount
                        c.commit()
            except OSError:
                pass
        return dirs, rows
