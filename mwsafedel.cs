// mwsafedel.cs: shared by elevated_clean.ps1 (wintemp/swdist) and winold_delete.ps1 (Windows.old).
// Copied to the protected folder by install_secure_*.ps1 with its SHA256 pinned, loaded from there with Add-Type -Path.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

public static class MwSafeDel {
    const uint DELETE = 0x10000, READ_ATTR = 0x80, SYNC = 0x100000, SHARE_ALL = 7, OPEN_EXISTING = 3;
    const uint BACKUP = 0x02000000, OPEN_REPARSE = 0x00200000, ATTR_DIR = 0x10, ATTR_REPARSE = 0x400;
    const uint WRITE_ATTR = 0x100, ATTR_READONLY = 0x1;

    [StructLayout(LayoutKind.Sequential)]
    struct ByHandle { public uint Attrs; public uint c1, c2, a1, a2, w1, w2, Vol, SizeHi, SizeLo, Links, IdxHi, IdxLo; }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr sa, uint disp, uint flags, IntPtr tmpl);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern uint GetFinalPathNameByHandleW(SafeFileHandle h, StringBuilder buf, uint len, uint flags);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetFileInformationByHandle(SafeFileHandle h, out ByHandle info);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetFileInformationByHandle(SafeFileHandle h, int cls, ref uint info, uint size);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetFileInformationByHandle(SafeFileHandle h, int cls, ref byte info, uint size);

    [StructLayout(LayoutKind.Sequential)]
    struct BasicInfo { public long Creation, LastAccess, LastWrite, Change; public uint Attrs; uint pad; }
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetFileInformationByHandle(SafeFileHandle h, int cls, ref BasicInfo info, uint size);

    [StructLayout(LayoutKind.Sequential)]
    struct Luid { public uint Low; public int High; }
    [StructLayout(LayoutKind.Sequential)]
    struct TokPriv1 { public uint Count; public Luid Id; public uint Attrs; }
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool OpenProcessToken(IntPtr proc, uint access, out SafeAccessTokenHandle tok);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool LookupPrivilegeValueW(string sys, string name, out Luid id);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool AdjustTokenPrivileges(SafeAccessTokenHandle tok, bool disableAll, ref TokPriv1 state, uint len, IntPtr prev, IntPtr retLen);
    [DllImport("kernel32.dll")]
    static extern IntPtr GetCurrentProcess();

    // يفعّل SeBackupPrivilege وSeRestorePrivilege في رمز العملية (موجودان معطّلين في رمز المسؤول المرتفع). مع BACKUP في CreateFileW
    // يمنحان السرد والحذف مهما كانت ACL، فلا حاجة إلى takeown/icacls اللذين يتبعان الوصلات إلى خارج الجذر (1300 = غير ممنوح).
    public static void EnableBackupRestore() {
        SafeAccessTokenHandle tok;
        if (!OpenProcessToken(GetCurrentProcess(), 0x20 | 0x8, out tok)) throw new Win32Exception(Marshal.GetLastWin32Error());
        using (tok) {
            foreach (var name in new[] { "SeBackupPrivilege", "SeRestorePrivilege" }) {
                var tp = new TokPriv1 { Count = 1, Attrs = 0x2 };
                if (!LookupPrivilegeValueW(null, name, out tp.Id)) throw new Win32Exception(Marshal.GetLastWin32Error());
                if (!AdjustTokenPrivileges(tok, false, ref tp, 0, IntPtr.Zero, IntPtr.Zero)) throw new Win32Exception(Marshal.GetLastWin32Error());
                int code = Marshal.GetLastWin32Error();
                if (code != 0) throw new Win32Exception(code, name + " not granted");
            }
        }
    }

    // مسار ممتد: pwsh لا يعلن longPathAware، فـ CreateFileW يفشل (3) لما تجاوز 260 حرفًا بلا \\?\ (و\\?\UNC\ للشبكي).
    static string Ext(string p) {
        p = Path.GetFullPath(p);
        if (p.StartsWith(@"\\?\") || p.StartsWith(@"\\.\")) return p;
        if (p.StartsWith(@"\\")) return @"\\?\UNC\" + p.Substring(2);
        return @"\\?\" + p;
    }

    static SafeFileHandle Open(string path, uint access) {
        var h = CreateFileW(Ext(path), access, SHARE_ALL, IntPtr.Zero, OPEN_EXISTING, BACKUP | OPEN_REPARSE, IntPtr.Zero);
        if (h.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
        return h;
    }

    static string Final(SafeFileHandle h) {
        var sb = new StringBuilder(1024);
        uint n = GetFinalPathNameByHandleW(h, sb, (uint)sb.Capacity, 0);
        if (n >= sb.Capacity) { sb = new StringBuilder((int)n + 1); n = GetFinalPathNameByHandleW(h, sb, (uint)sb.Capacity, 0); }
        if (n == 0) throw new Win32Exception(Marshal.GetLastWin32Error());
        string p = sb.ToString();
        if (p.StartsWith(@"\\?\UNC\")) return @"\\" + p.Substring(8);
        return p.StartsWith(@"\\?\") ? p.Substring(4) : p;
    }

    static string Norm(string p) { return Path.GetFullPath(p).TrimEnd('\\'); }

    static bool Inside(string path, string root) {
        return path.StartsWith(Norm(root) + "\\", StringComparison.OrdinalIgnoreCase);
    }

    public static string RootReal(string root) {
        root = Norm(root);
        using (var h = Open(root, READ_ATTR | SYNC)) {
            ByHandle bi;
            if (!GetFileInformationByHandle(h, out bi)) throw new Win32Exception(Marshal.GetLastWin32Error());
            if ((bi.Attrs & ATTR_REPARSE) != 0 || (bi.Attrs & ATTR_DIR) == 0) throw new IOException("root is a reparse point or not a folder");
            string real = Final(h);
            if (!string.Equals(Norm(real), root, StringComparison.OrdinalIgnoreCase)) throw new IOException("root reached through a reparse point");
            return real;
        }
    }

    // يحذف ملفًا أو مجلدًا فارغًا أو رابطًا (كرابط) داخل الجذر؛ realRoot من RootReal.
    public static void Remove(string root, string realRoot, string path) {
        root = Norm(root); path = Path.GetFullPath(path);
        if (!Inside(path, root)) throw new IOException("path outside its root");
        for (string cur = Path.GetDirectoryName(path); ; cur = Path.GetDirectoryName(cur)) {
            var a = File.GetAttributes(cur);   // سمات المكوّن نفسه، لا ما يشير إليه
            if ((a & FileAttributes.ReparsePoint) != 0 || (a & FileAttributes.Directory) == 0) throw new IOException("path component is a reparse point");
            if (string.Equals(Norm(cur), root, StringComparison.OrdinalIgnoreCase)) break;
        }
        using (var h = Open(path, DELETE | READ_ATTR | WRITE_ATTR | SYNC)) {
            if (!Inside(Final(h), realRoot)) throw new IOException("opened object lies outside its root");
            MarkDelete(h);
        }
    }

    // يعلّم المفتوح بمقبضه للحذف. DELETE | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE: ملفات القراءة فقط في مجلدات النظام
    // كانت تُحذف بـ Remove-Item -Force.
    static void MarkDelete(SafeFileHandle h) {
        uint flags = 0x1 | 0x2 | 0x10;
        if (SetFileInformationByHandle(h, 21, ref flags, 4)) return;
        int code = Marshal.GetLastWin32Error();
        if (code != 87 && code != 50) throw new Win32Exception(code);
        // الصنف القديم لا يقبل IGNORE_READONLY: تُزال سمة القراءة فقط بالمقبض نفسه أولًا
        ByHandle bi;
        if (!GetFileInformationByHandle(h, out bi)) throw new Win32Exception(Marshal.GetLastWin32Error());
        if ((bi.Attrs & ATTR_READONLY) != 0) {
            var basic = new BasicInfo();
            basic.Attrs = bi.Attrs & ~ATTR_READONLY;
            if (basic.Attrs == 0) basic.Attrs = 0x80;   // FILE_ATTRIBUTE_NORMAL
            if (!SetFileInformationByHandle(h, 0, ref basic, (uint)Marshal.SizeOf(typeof(BasicInfo)))) throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        byte del = 1;
        if (!SetFileInformationByHandle(h, 4, ref del, 1)) throw new Win32Exception(Marshal.GetLastWin32Error());
    }

    // يحذف المجلد كله (Windows.old): كل ملف، وكل رابط كرابط دون النزول فيه أو المساس بهدفه، ثم المجلدات من الأعمق، ثم الجذر.
    // لا تغيير لمالك ولا ACL: السرد والحذف بـ BACKUP بعد EnableBackupRestore. afterList للاختبار. يعيد {المحذوف، بايتاته، المتروك}.
    public static long[] Purge(string root, Action afterList) {
        root = Norm(root);
        string real = RootReal(root);
        var items = new List<KeyValuePair<string, long>>();
        var dirs = new List<string>();
        var stack = new Stack<string>();
        long skipped = 0;
        stack.Push(root);
        while (stack.Count > 0) {
            string d = stack.Pop();
            IEnumerable<FileSystemInfo> entries;
            try { entries = new List<FileSystemInfo>(new DirectoryInfo(d).EnumerateFileSystemInfos()); } catch (Exception) { skipped++; continue; }
            foreach (var e in entries) {
                bool link = (e.Attributes & FileAttributes.ReparsePoint) != 0, dir = (e.Attributes & FileAttributes.Directory) != 0;
                if (dir && !link) { dirs.Add(e.FullName); stack.Push(e.FullName); }
                else items.Add(new KeyValuePair<string, long>(e.FullName, dir ? 0 : ((FileInfo)e).Length));
            }
        }
        if (afterList != null) afterList();
        long n = 0, bytes = 0;
        foreach (var f in items) {
            try { Remove(root, real, f.Key); n++; bytes += f.Value; } catch (Exception) { skipped++; }
        }
        dirs.Sort((x, y) => y.Length.CompareTo(x.Length));
        foreach (var d in dirs) {
            try { Remove(root, real, d); } catch (Exception) { }   // يبقى إن بقي فيه شيء متروك (عُدّ أعلاه)
        }
        try {
            using (var h = Open(root, DELETE | READ_ATTR | WRITE_ATTR | SYNC)) {
                ByHandle bi;
                if (!GetFileInformationByHandle(h, out bi)) throw new Win32Exception(Marshal.GetLastWin32Error());
                if ((bi.Attrs & ATTR_REPARSE) != 0 || (bi.Attrs & ATTR_DIR) == 0) throw new IOException("root changed into a reparse point");
                if (!string.Equals(Norm(Final(h)), Norm(real), StringComparison.OrdinalIgnoreCase)) throw new IOException("root moved");
                MarkDelete(h);
            }
        } catch (Exception) { }   // غير فارغ: المستدعي يرى الجذر باقيًا
        return new long[] { n, bytes, skipped };
    }

    // يسرد الملفات الأقدم من cutUtc والمجلدات دون النزول في أي رابط، ثم (afterList للاختبار) يحذف كلًّا بـ Remove.
    // يعيد {عدد المحذوف، بايتاته، المتروك}.
    public static long[] Clean(string root, DateTime cutUtc, Action afterList) {
        root = Norm(root);
        string real = RootReal(root);
        var files = new List<KeyValuePair<string, long>>();
        var dirs = new List<string>();
        var stack = new Stack<string>();
        stack.Push(root);
        while (stack.Count > 0) {
            string d = stack.Pop();
            IEnumerable<FileSystemInfo> entries;
            try { entries = new List<FileSystemInfo>(new DirectoryInfo(d).EnumerateFileSystemInfos()); } catch (Exception) { continue; }
            foreach (var e in entries) {
                if ((e.Attributes & FileAttributes.ReparsePoint) != 0) continue;
                if ((e.Attributes & FileAttributes.Directory) != 0) { dirs.Add(e.FullName); stack.Push(e.FullName); }
                else if (e.LastWriteTimeUtc < cutUtc) files.Add(new KeyValuePair<string, long>(e.FullName, ((FileInfo)e).Length));
            }
        }
        if (afterList != null) afterList();
        long n = 0, bytes = 0, skipped = 0;
        foreach (var f in files) {
            try { Remove(root, real, f.Key); n++; bytes += f.Value; } catch (Exception) { skipped++; }
        }
        dirs.Sort((x, y) => y.Length.CompareTo(x.Length));
        foreach (var d in dirs) {
            try { Remove(root, real, d); } catch (Exception) { }   // غير فارغ (145) أو قيد الاستخدام: متوقع
        }
        return new long[] { n, bytes, skipped };
    }
}
