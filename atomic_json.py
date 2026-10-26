"""Atomic JSON transactions shared by cooperating MonitorWidget processes.

The persistent sidecar lock is never unlinked (unlinking would create two lock
identities). Readers see either the old or new complete document. Other writers
must use this transaction protocol too; arbitrary external editors cannot be
made transactional by a lock they do not acquire.
"""
import contextlib
import json
import os
import stat
import tempfile
import threading
import time

_locks = {}
_locks_guard = threading.Lock()


@contextlib.contextmanager
def locked(path, timeout=10):
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    with _locks_guard:
        lock = _locks.setdefault(key, threading.RLock())
    if not lock.acquire(timeout=timeout):
        raise TimeoutError("configuration is busy")
    fh = None
    acquired = False
    try:
        fh = open(key + ".mwlock", "a+b")
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"\0"); fh.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("configuration is busy")
                time.sleep(0.025)
        yield
    finally:
        try:
            if fh is not None:
                try:
                    if acquired:
                        fh.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                finally:
                    fh.close()
        finally:
            lock.release()


def read(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    return raw.startswith(b"\xef\xbb\xbf"), json.loads(raw.decode("utf-8-sig"))


def _preserve_permissions(source, target):
    os.chmod(target, stat.S_IMODE(os.stat(source).st_mode))
    if os.name != "nt":
        return
    # os.replace installs the temporary file's ACL. Preserve the old DACL as
    # well as permission bits, rather than accidentally widening a private file
    # to its parent directory's inherited permissions.
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    get = api.GetFileSecurityW
    get.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    get.restype = wintypes.BOOL
    put = api.SetFileSecurityW
    put.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
    put.restype = wintypes.BOOL
    control = api.GetSecurityDescriptorControl
    control.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD))
    control.restype = wintypes.BOOL
    needed = wintypes.DWORD()
    get(source, 4, None, 0, ctypes.byref(needed))  # DACL_SECURITY_INFORMATION
    if not needed.value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(needed.value)
    if not get(source, 4, descriptor, needed, ctypes.byref(needed)):
        raise ctypes.WinError(ctypes.get_last_error())
    flags, revision = wintypes.WORD(), wintypes.DWORD()
    if not control(descriptor, ctypes.byref(flags), ctypes.byref(revision)):
        raise ctypes.WinError(ctypes.get_last_error())
    # Preserve whether inheritance was protected on the original DACL.
    info = 4 | (0x80000000 if flags.value & 0x1000 else 0x20000000)
    if not put(target, info, descriptor):
        raise ctypes.WinError(ctypes.get_last_error())


def write(path, bom, obj):
    """Caller holds locked(path) when this is part of a read/modify/write."""
    data = json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    path = os.path.abspath(os.fspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".mw-json-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write((b"\xef\xbb\xbf" if bom else b"") + data)
            fh.flush(); os.fsync(fh.fileno())
        if os.path.exists(path):
            _preserve_permissions(path, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def transaction(path, before_write=None):
    with locked(path):
        bom, obj = read(path)
        before = json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False)
        yield bom, obj
        if json.dumps(obj, sort_keys=True, ensure_ascii=False, allow_nan=False) != before:
            if before_write:
                before_write(path)
            write(path, bom, obj)
