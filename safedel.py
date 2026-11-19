"""الحذف والنقل الآمنان داخل جذر مسموح (مكتبة بايثون القياسية فقط).

الخادم يعمل بصلاحية المسؤول، ومجلدات التنظيف (Temp وذاكرات المستخدم والحجر) يكتب فيها أي برنامج عادي. فإن حُذف ملف
بمساره النصي بعد سرد طويل، أمكن لبرنامج عادي أن يزرع بين السرد والحذف نقطة إعادة توجيه (junction أو symlink) في مكوّن
من المسار فيوجّه الحذف بصلاحية المسؤول إلى خارج المجلد (TOCTOU). لذلك كل حذف أو نقل يمرّ من هنا:

1. الجذر نفسه يُفتح لحظة البدء بمقبض لا يتبع إعادة التوجيه، ويجب أن يطابق مساره الحقيقي (من المقبض) مساره المكتوب؛
   فإن كان الجذر أو أحد آبائه نقطة إعادة توجيه رُفضت العملية كلها.
2. لحظة الحذف: كل مكوّن بين الجذر والهدف يُفحص بـ os.lstat، ويُرفض إن كان نقطة إعادة توجيه أو لم يكن مجلدًا.
3. الهدف يُفتح بمقبض بـ FILE_FLAG_OPEN_REPARSE_POINT (فإن كان هو نفسه رابطًا فُتح الرابط لا هدفه)، ثم يُقرأ مساره
   الحقيقي من المقبض (GetFinalPathNameByHandleW، وهو مسار الكائن الفعلي على القرص بلا أي إعادة توجيه) ويجب أن يقع
   داخل الجذر الحقيقي. والحذف نفسه بالمقبض (FileDispositionInfo)، فلا يُعاد تفسير المسار بعد الفحص.
4. النقل (الحجر والاسترجاع) بالمقبض أيضًا: المصدر مقبض مفحوص، والوجهة اسم داخل مقبض مجلد مفحوص (RootDirectory)، ولا
   يُستبدل ما هو موجود.

نقطة إعادة التوجيه نفسها تُحذف كرابط ولا يُتبع محتواها أبدًا. والمجلد لا يُحذف إلا فارغًا (رمز الخطأ 145 كما في rmdir).
الأخطاء OSError بـ winerror كالمعتاد (2 غير موجود، 5 مرفوض، 32 مستخدم، 145 غير فارغ) فلا يتغيّر تعامل المستدعين معها.
"""
import ctypes
import errno
import os
import stat

REPARSE = 0x400   # FILE_ATTRIBUTE_REPARSE_POINT: junction وsymlink ونقطة تركيب


class UnsafePath(OSError):
    """مسار فيه نقطة إعادة توجيه أو يقع خارج الجذر: لا يُلمس."""

    def __init__(self, msg):
        super().__init__(errno.EACCES, msg)


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


def inside(path, root, allow_equal=False):
    """path داخل root بالمكوّنات (لا بالبادئة النصية)."""
    try:
        p, r = _norm(path), _norm(root)
        if p == r:
            return allow_equal
        return os.path.commonpath((p, r)) == r
    except ValueError:
        return False


def is_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & REPARSE)


def check_chain(path, root):
    """كل مكوّن موجود من الجذر (شاملًا) إلى أب الهدف مجلد حقيقي لا نقطة إعادة توجيه. الهدف نفسه قد يكون رابطًا."""
    path, root = os.path.abspath(path), os.path.abspath(root)
    if not inside(path, root):
        raise UnsafePath("path outside its root")
    cur = os.path.dirname(path)
    while True:
        info = os.lstat(cur)
        if is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise UnsafePath("path component is a reparse point")
        if _norm(cur) == _norm(root):
            return path
        cur = os.path.dirname(cur)


if os.name == "nt":
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    _k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _k32.GetLongPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    _k32.GetLongPathNameW.restype = wintypes.DWORD
    _k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k32.SetFileInformationByHandle.restype = wintypes.BOOL
    _k32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    _k32.GetFileInformationByHandle.restype = wintypes.BOOL
    _nt = ctypes.WinDLL("ntdll")
    _nt.NtSetInformationFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG, ctypes.c_int]
    _nt.NtSetInformationFile.restype = ctypes.c_long
    _nt.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
    _nt.RtlNtStatusToDosError.restype = wintypes.ULONG

    _INVALID = wintypes.HANDLE(-1).value
    _DELETE, _READ_ATTR, _SYNC = 0x10000, 0x80, 0x100000
    _ADD_FILE, _ADD_SUBDIR, _TRAVERSE = 0x2, 0x4, 0x20
    _SHARE_ALL = 7
    _OPEN_EXISTING = 3
    _BACKUP, _OPEN_REPARSE = 0x02000000, 0x00200000
    _FILE_DISPOSITION_INFO, _FILE_DISPOSITION_INFO_EX = 4, 21
    _DISPOSITION_DELETE, _DISPOSITION_POSIX = 0x1, 0x2
    _NT_FILE_RENAME_INFORMATION = 10   # NtSetInformationFile: يقبل اسمًا نسبيًا إلى RootDirectory (SetFileInformationByHandle يرفضه بالخطأ 87)

    class _IoStatus(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_void_p)]

    class _UnicodeString(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_void_p)]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE), ("ObjectName", ctypes.POINTER(_UnicodeString)),
                    ("Attributes", wintypes.ULONG), ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]

    _nt.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                 wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG]
    _nt.NtCreateFile.restype = ctypes.c_long

    class _ByHandle(ctypes.Structure):
        _fields_ = [("attrs", wintypes.DWORD), ("c", wintypes.FILETIME), ("a", wintypes.FILETIME), ("w", wintypes.FILETIME),
                    ("vol", wintypes.DWORD), ("size_hi", wintypes.DWORD), ("size_lo", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("idx_hi", wintypes.DWORD), ("idx_lo", wintypes.DWORD)]

    class _RenameInfo(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("RootDirectory", wintypes.HANDLE), ("FileNameLength", wintypes.DWORD),
                    ("FileName", wintypes.WCHAR * 1)]

    def _err(code=None):
        code = ctypes.get_last_error() if code is None else code
        return ctypes.WinError(code)

    def _open(path, access):
        h = _k32.CreateFileW(path, access, _SHARE_ALL, None, _OPEN_EXISTING, _BACKUP | _OPEN_REPARSE, None)
        if h is None or h == _INVALID:
            raise _err()
        return h

    def _final(h):
        buf = ctypes.create_unicode_buffer(1024)
        n = _k32.GetFinalPathNameByHandleW(h, buf, len(buf), 0)
        if n >= len(buf):
            buf = ctypes.create_unicode_buffer(n + 1)
            n = _k32.GetFinalPathNameByHandleW(h, buf, len(buf), 0)
        if not n:
            raise _err()
        p = buf.value
        if p.startswith("\\\\?\\UNC\\"):
            return "\\\\" + p[8:]
        return p.removeprefix("\\\\?\\")

    def _info(h):
        bi = _ByHandle()
        if not _k32.GetFileInformationByHandle(h, ctypes.byref(bi)):
            raise _err()
        return bi

    def _long(path):
        buf = ctypes.create_unicode_buffer(1024)
        n = _k32.GetLongPathNameW(path, buf, len(buf))
        if n >= len(buf):
            buf = ctypes.create_unicode_buffer(n + 1)
            n = _k32.GetLongPathNameW(path, buf, len(buf))
        if not n:
            raise _err()
        return buf.value

    def _root_real(root):
        """المسار الحقيقي للجذر من مقبضه؛ يُرفض الجذر إن كان هو أو أحد آبائه نقطة إعادة توجيه (يختلف المسار الحقيقي عن المكتوب)."""
        root = os.path.abspath(root)
        h = _open(root, _READ_ATTR | _SYNC)
        try:
            bi = _info(h)
            if bi.attrs & REPARSE or not bi.attrs & 0x10:
                raise UnsafePath("root is a reparse point or not a folder")
            real = _final(h)
        finally:
            _k32.CloseHandle(h)
        if _norm(real) != _norm(_long(root)):
            raise UnsafePath("root reached through a reparse point")
        return real

    class Root:
        """جذر مسموح: يُتحقق منه مرة عند الإنشاء، ثم remove/move لكل عنصر تحته. صالح للاستعمال في حلقة طويلة."""

        def __init__(self, root):
            self.root = os.path.abspath(root)
            self.real = _root_real(self.root)

        def _leaf(self, path, access, allow_root=False):
            """يفتح الهدف نفسه (لا ما يشير إليه) بعد فحص السلسلة، ويتحقق من أن مساره الحقيقي داخل الجذر الحقيقي."""
            path = os.path.abspath(path)
            if allow_root and _norm(path) == _norm(self.root):
                h = _open(path, access)
            else:
                check_chain(path, self.root)
                h = _open(path, access)
            try:
                real = _final(h)
                if not inside(real, self.real, allow_equal=allow_root):
                    raise UnsafePath("opened object lies outside its root")
            except BaseException:
                _k32.CloseHandle(h)
                raise
            return h

        def remove(self, path):
            """يحذف ملفًا أو مجلدًا فارغًا أو نقطة إعادة توجيه (كرابط) داخل الجذر، بالمقبض."""
            h = self._leaf(path, _DELETE | _READ_ATTR | _SYNC)
            try:
                # POSIX: الاسم يختفي فورًا ولو بقي الملف مفتوحًا بمشاركة الحذف، فيُحذف أبوه الفارغ بعده مباشرة.
                # لا IGNORE_READONLY: ملف للقراءة فقط يُرفض كما في os.remove. نظام ملفات لا يدعمه (87/50): الصنف القديم.
                flags = wintypes.DWORD(_DISPOSITION_DELETE | _DISPOSITION_POSIX)
                if not _k32.SetFileInformationByHandle(h, _FILE_DISPOSITION_INFO_EX, ctypes.byref(flags), 4):
                    code = ctypes.get_last_error()
                    if code not in (50, 87):
                        raise _err(code)
                    flag = ctypes.c_ubyte(1)
                    if not _k32.SetFileInformationByHandle(h, _FILE_DISPOSITION_INFO, ctypes.byref(flag), 1):
                        raise _err()
            finally:
                _k32.CloseHandle(h)

        def makedirs(self, path):
            """ينشئ ما ينقص من مجلدات path داخل الجذر، كل مكوّن باسمه نسبيًا إلى مقبض أبيه المفحوص (NtCreateFile مع
            RootDirectory وFILE_OPEN_REPARSE_POINT)، فلا يُنشأ شيء عبر نقطة إعادة توجيه زُرعت في الطريق. مكوّن موجود يُقبل
            إن كان مجلدًا حقيقيًا داخل الجذر فقط."""
            path = os.path.abspath(path)
            if not inside(path, self.root, allow_equal=True):
                raise UnsafePath("folder outside its root")
            rel = os.path.relpath(path, self.root)
            parts = [] if rel == "." else rel.split(os.sep)
            cur = self.root
            for name in parts:
                if not name or name in (".", ".."):
                    raise UnsafePath("bad folder name")
                hp = self._leaf(cur, _READ_ATTR | _ADD_SUBDIR | _TRAVERSE | _SYNC, allow_root=True)
                try:
                    bi = _info(hp)
                    if bi.attrs & REPARSE or not bi.attrs & 0x10:
                        raise UnsafePath("parent folder is a reparse point")
                    raw = ctypes.create_unicode_buffer(name)
                    us = _UnicodeString(len(name) * 2, len(name) * 2, ctypes.cast(raw, ctypes.c_void_p))
                    oa = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), hp, ctypes.pointer(us), 0x40, None, None)
                    h, iosb = wintypes.HANDLE(), _IoStatus()
                    st = _nt.NtCreateFile(ctypes.byref(h), 0x1 | _SYNC, ctypes.byref(oa), ctypes.byref(iosb), None, 0x80, _SHARE_ALL,
                                          2, 0x1 | 0x20 | _OPEN_REPARSE, None, 0)   # FILE_CREATE، FILE_DIRECTORY_FILE|SYNCHRONOUS
                    if st >= 0:
                        _k32.CloseHandle(h)
                    elif st & 0xFFFFFFFF != 0xC0000035:   # STATUS_OBJECT_NAME_COLLISION: موجود، يُفحص أدناه
                        raise _err(_nt.RtlNtStatusToDosError(st))
                finally:
                    _k32.CloseHandle(hp)
                cur = os.path.join(cur, name)
                hc = self._leaf(cur, _READ_ATTR | _SYNC)
                try:
                    bi = _info(hc)
                    if bi.attrs & REPARSE or not bi.attrs & 0x10:
                        raise UnsafePath("existing component is a reparse point or a file")
                finally:
                    _k32.CloseHandle(hc)
            return path

        def identity(self, path):
            """(المجلد/الحجم، رقم الملف) للكائن المفتوح نفسه، لمقارنة الهوية قبل النقل."""
            h = self._leaf(path, _READ_ATTR | _SYNC)
            try:
                bi = _info(h)
                return bi.vol, (bi.idx_hi << 32) | bi.idx_lo
            finally:
                _k32.CloseHandle(h)

        def move(self, src, dst_root, dst):
            """ينقل src (داخل هذا الجذر) إلى dst داخل dst_root دون استبدال ما هو موجود؛ الوجهة اسم داخل مقبض مجلدها المفحوص."""
            if not isinstance(dst_root, Root):
                dst_root = Root(dst_root)
            dst = os.path.abspath(dst)
            if not inside(dst, dst_root.root):
                raise UnsafePath("destination outside its root")
            name = os.path.basename(dst)
            if not name or name in (".", "..") or "\\" in name or "/" in name:
                raise UnsafePath("bad destination name")
            hs = self._leaf(src, _DELETE | _READ_ATTR | _SYNC)
            try:
                hd = dst_root._leaf(os.path.dirname(dst), _READ_ATTR | _ADD_FILE | _ADD_SUBDIR | _TRAVERSE | _SYNC, allow_root=True)
                try:
                    bi = _info(hd)
                    if bi.attrs & REPARSE or not bi.attrs & 0x10:
                        raise UnsafePath("destination folder is a reparse point")
                    raw = name.encode("utf-16-le")
                    size = ctypes.sizeof(_RenameInfo) + len(raw)
                    buf = ctypes.create_string_buffer(size)
                    info = _RenameInfo.from_buffer(buf)
                    info.Flags = 0            # ReplaceIfExists = FALSE
                    info.RootDirectory = hd
                    info.FileNameLength = len(raw)
                    ctypes.memmove(ctypes.addressof(buf) + _RenameInfo.FileName.offset, raw, len(raw))
                    iosb = _IoStatus()
                    st = _nt.NtSetInformationFile(hs, ctypes.byref(iosb), buf, size, _NT_FILE_RENAME_INFORMATION)
                    if st < 0:
                        raise _err(_nt.RtlNtStatusToDosError(st))
                finally:
                    _k32.CloseHandle(hd)
            finally:
                _k32.CloseHandle(hs)

else:   # غير ويندوز (الاختبارات المحمولة فقط): فحص السلسلة ثم الحذف بالمسار
    class Root:
        def __init__(self, root):
            self.root = os.path.abspath(root)
            info = os.lstat(self.root)
            if is_reparse(info) or not stat.S_ISDIR(info.st_mode) or _norm(os.path.realpath(self.root)) != _norm(self.root):
                raise UnsafePath("root reached through a reparse point")
            self.real = self.root

        def remove(self, path):
            path = check_chain(path, self.root)
            info = os.lstat(path)
            if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                os.rmdir(path)
            else:
                os.unlink(path)

        def makedirs(self, path):
            path = os.path.abspath(path)
            if not inside(path, self.root, allow_equal=True):
                raise UnsafePath("folder outside its root")
            if _norm(path) != _norm(self.root):
                check_chain(path, self.root)
            os.makedirs(path, exist_ok=True)
            return path

        def identity(self, path):
            info = os.lstat(check_chain(path, self.root))
            return info.st_dev, info.st_ino

        def move(self, src, dst_root, dst):
            if not isinstance(dst_root, Root):
                dst_root = Root(dst_root)
            src = check_chain(src, self.root)
            dst = os.path.abspath(dst)
            if not inside(dst, dst_root.root):
                raise UnsafePath("destination outside its root")
            if _norm(os.path.dirname(dst)) != _norm(dst_root.root):
                check_chain(os.path.join(os.path.dirname(dst), "x"), dst_root.root)
            if os.path.isdir(src):
                raise OSError("atomic directory move requires Windows")
            os.link(src, dst, follow_symlinks=False)
            os.unlink(src)


def remove(path, root):
    """حذف عنصر واحد داخل root (للاستعمال المفرد؛ في الحلقات أنشئ Root مرة واحدة)."""
    Root(root).remove(path)


def remove_tree(path, root):
    """يحذف شجرة داخل root من الأعمق إلى الأعلى، كل عنصر عبر Root.remove؛ نقاط إعادة التوجيه تُحذف كروابط ولا يُنزل فيها.
    أي فشل يُرفع (لا يُتجاهل)."""
    guard = root if isinstance(root, Root) else Root(root)
    path = os.path.abspath(path)
    info = os.lstat(path)
    if stat.S_ISDIR(info.st_mode) and not is_reparse(info):
        with os.scandir(path) as it:
            entries = [e.path for e in it]
        for e in entries:
            remove_tree(e, guard)
    guard.remove(path)
