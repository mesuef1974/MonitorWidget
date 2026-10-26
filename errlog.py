"""Records exceptions that the monitor deliberately keeps running past, so failures stay visible.
Writes monitor.log (rotating, about 1 MB x 3). Never raises."""
import logging, os, traceback
from logging.handlers import RotatingFileHandler

_log = logging.getLogger("monitor")
if not _log.handlers:
    try:
        _h = RotatingFileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.log"),
                                 maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        _h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        _log.addHandler(_h)
        _log.setLevel(logging.WARNING)
    except Exception:
        pass


def swallowed():
    """Call from inside an except block that intentionally continues."""
    try:
        import sys
        ex = sys.exc_info()[1]
        # Temp-file races (file vanished / locked by another process) are routine: keep them out of the warning stream.
        lvl = logging.DEBUG if isinstance(ex, (FileNotFoundError, PermissionError)) else logging.WARNING
        _log.log(lvl, "swallowed exception:\n%s", traceback.format_exc())
    except Exception:
        pass
