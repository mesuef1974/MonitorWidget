"""Pin and preload this application's SQLite DLL, without modifying Python.

Call ensure_sqlite() before importing sqlite3 (directly or through app modules).
No network access, fallback, runtime extraction, or global DLL installation.
The literal pin is the trust anchor; manifest.json is deployment provenance only.
"""
import ctypes
import hashlib
import importlib
import os
from pathlib import Path
import sys
import threading

EXPECTED_VERSION = "3.54.1"
EXPECTED_DLL_SHA256 = "4b59d3cded9dc1608544160f3b4b10ac974c6ac0d6ffc23064e5f4c8dbd05aea"
RUNTIME_DIR = Path(__file__).resolve().parent / "runtime" / "sqlite"
DLL_PATH = RUNTIME_DIR / "sqlite3.dll"
_lock = threading.RLock()
_dll = None  # Retain the native module handle until process exit.
_sqlite = None


def _verified_dll_path():
    if len(EXPECTED_DLL_SHA256) != 64:
        raise RuntimeError("SQLite runtime has no reviewed SHA-256 pin")
    try:
        actual = hashlib.sha256(DLL_PATH.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuntimeError("Required private SQLite runtime is missing or unreadable") from exc
    if actual != EXPECTED_DLL_SHA256:
        raise RuntimeError("Private SQLite runtime SHA-256 mismatch; startup refused")
    return str(DLL_PATH.resolve())


def _loaded_dll_path():
    """Identify the SQLite DLL already mapped in this process, if any."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_module = kernel.GetModuleHandleW
    get_module.argtypes = (wintypes.LPCWSTR,)
    get_module.restype = ctypes.c_void_p
    handle = get_module("sqlite3.dll")
    if not handle:
        return None
    get_name = kernel.GetModuleFileNameW
    get_name.argtypes = (ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD)
    get_name.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_name(handle, buffer, len(buffer))
    if not length or length >= len(buffer) - 1:
        raise RuntimeError("Cannot identify the SQLite DLL already loaded")
    return str(Path(buffer.value).resolve())


def ensure_sqlite():
    """Return stdlib sqlite3 backed by the pinned private runtime, or refuse.

    This is idempotent inside one process. Every fresh process/restart verifies
    the file again. A pre-existing import of another SQLite runtime is rejected;
    changing a loaded DLL in place cannot be made safe by Python monkeypatching.
    """
    global _dll, _sqlite
    with _lock:
        if _sqlite is not None:
            return _sqlite
        if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise RuntimeError("The pinned SQLite runtime requires 64-bit Windows Python")
        if "_sqlite3" in sys.modules:
            raise RuntimeError("SQLite was imported before its private runtime; restart with ensure_sqlite first")
        path = _verified_dll_path()
        loaded = _loaded_dll_path()
        if loaded and os.path.normcase(loaded) != os.path.normcase(path):
            raise RuntimeError("A different SQLite DLL is already loaded; startup refused")
        # LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS.
        # Use an absolute verified path rather than PATH/current-directory search.
        try:
            dll = ctypes.WinDLL(path, winmode=0x00000100 | 0x00001000)
        except OSError as exc:
            raise RuntimeError("Cannot load the verified private SQLite runtime") from exc
        dll.sqlite3_libversion.argtypes = ()
        dll.sqlite3_libversion.restype = ctypes.c_char_p
        version = dll.sqlite3_libversion().decode("ascii")
        if version != EXPECTED_VERSION:
            raise RuntimeError("Private SQLite native version does not match the reviewed release")
        _dll = dll
        sqlite = importlib.import_module("sqlite3")
        if sqlite.sqlite_version != EXPECTED_VERSION:
            raise RuntimeError("Python did not bind to the required private SQLite version")
        actual_path = _loaded_dll_path()
        if not actual_path or os.path.normcase(actual_path) != os.path.normcase(path):
            raise RuntimeError("Python bound to an unexpected SQLite DLL path")
        _sqlite = sqlite
        return sqlite


def runtime_info():
    """Safe provenance for diagnostics; includes no connection data or secrets."""
    sqlite = ensure_sqlite()
    return {"version": sqlite.sqlite_version, "dll_path": str(DLL_PATH),
            "dll_sha256": EXPECTED_DLL_SHA256, "scope": "MonitorWidget process only"}
