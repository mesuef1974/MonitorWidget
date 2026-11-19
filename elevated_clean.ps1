# elevated_clean.ps1 — تنظيفات تحتاج المسؤول من مهام مجدولة مرتفعة ثابتة فقط (MonitorWidget-Clean-WinTemp / -SwDist / -Dism).
# يُنسخ إلى المجلد المحمي بواسطة install_secure_*.ps1 ويعمل من هناك؛ الخادم (بصلاحية عادية) يطلق المهمة بـ schtasks /run ويقرأ ملف النتيجة.
# القائمة البيضاء: النوع من ثلاثة فقط، المسارات ثابتة هنا، لا وسيطة مسار من الخادم، لا تتبّع لروابط، ولا /ResetBase في DISM.
param([Parameter(Mandatory)][ValidateSet('wintemp', 'swdist', 'dism')][string]$Kind,
      [string]$Result = 'C:\ProgramData\MonitorWidget\out\clean_result.json')
$ErrorActionPreference = 'Stop'
$Roots = @{ wintemp = @('C:\Windows\Temp'); swdist = @('C:\Windows\SoftwareDistribution\Download') }
$MinDays = @{ wintemp = 3; swdist = 0 }
# نظير safedel.py: الجذر يُتحقق من مساره الحقيقي من مقبضه؛ لحظة الحذف يُرفض أي مكوّن بين الجذر والهدف يحمل ReparsePoint،
# والهدف يُفتح بـ FILE_FLAG_OPEN_REPARSE_POINT ويجب أن يقع مساره الحقيقي (من المقبض) داخل الجذر، والحذف بالمقبض نفسه
# (FileDispositionInfoEx بـ POSIX، ثم الصنف القديم عند 87/50). نقطة إعادة التوجيه لا يُنزل فيها ولا تُحذف هنا.
$SafeDelCs = @'
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

    static SafeFileHandle Open(string path, uint access) {
        var h = CreateFileW(path, access, SHARE_ALL, IntPtr.Zero, OPEN_EXISTING, BACKUP | OPEN_REPARSE, IntPtr.Zero);
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
        using (var h = Open(path, DELETE | READ_ATTR | SYNC)) {
            if (!Inside(Final(h), realRoot)) throw new IOException("opened object lies outside its root");
            uint flags = 0x1 | 0x2;   // DELETE | POSIX_SEMANTICS
            if (!SetFileInformationByHandle(h, 21, ref flags, 4)) {
                int code = Marshal.GetLastWin32Error();
                if (code != 87 && code != 50) throw new Win32Exception(code);
                byte del = 1;
                if (!SetFileInformationByHandle(h, 4, ref del, 1)) throw new Win32Exception(Marshal.GetLastWin32Error());
            }
        }
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
'@
$t0 = Get-Date
$o = [ordered]@{ ts = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); kind = $Kind; state = 'error'; ok = $false; mb = 0; files = 0; skipped = 0; freeBeforeGB = 0; freeAfterGB = 0; error = $null }
function Save { $o | ConvertTo-Json -Compress | Set-Content -LiteralPath $Result -Encoding UTF8 }
function FreeGB { [math]::Round((Get-PSDrive C).Free / 1GB, 2) }
try {
    $id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'يلزم تشغيل مرتفع (المهمة المجدولة)' }
    $o.state = 'running'; Save
    $o.freeBeforeGB = FreeGB
    if ($Kind -eq 'dism') {
        # StartComponentCleanup فقط؛ لا /ResetBase كي يبقى التراجع عن التحديثات ممكنًا
        $p = Start-Process -FilePath "$env:SystemRoot\System32\Dism.exe" -ArgumentList '/Online', '/Cleanup-Image', '/StartComponentCleanup' -Wait -PassThru -WindowStyle Hidden
        $o.ok = ($p.ExitCode -eq 0)
        if (-not $o.ok) { $o.error = ('DISM أعاد الرمز {0}' -f $p.ExitCode) }
    } else {
        # الحذف بالمقبض (MwSafeDel، نظير safedel.py): Windows\Temp يكتب فيه أي مستخدم، فمجلد يُحوَّل junction بعد السرد كان
        # يوجّه Remove-Item المرتفع إلى System32. pwsh 7 يترجم Add-Type في الذاكرة فلا ملفات ترجمة في مجلد قابل للكتابة.
        Add-Type -TypeDefinition $SafeDelCs -Language CSharp
        $cut = (Get-Date).AddDays(-$MinDays[$Kind]).ToUniversalTime()
        foreach ($root in $Roots[$Kind]) {
            if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
            try {
                # الملفات أولًا (الأقدم من الحد)، ثم المجلدات الفارغة من الأعمق؛ ما هو قيد الاستخدام أو المرفوض يُترك ويُعدّ متروكًا
                $r = [MwSafeDel]::Clean($root, $cut, $null)
                $o.files += $r[0]; $o.mb += $r[1] / 1MB; $o.skipped += $r[2]
            } catch {
                $m = 'الجذر غير آمن: ' + $_.Exception.Message
                $o.skipped++; $o.error = $m.Substring(0, [Math]::Min(120, $m.Length))
            }
        }
        $o.ok = $true
    }
    $o.freeAfterGB = FreeGB
    if ($Kind -eq 'dism') { $o.mb = [math]::Max(0, ($o.freeAfterGB - $o.freeBeforeGB) * 1024) }
    $o.mb = [math]::Round($o.mb, 1)
    $o.state = if ($o.ok) { 'done' } else { 'error' }
} catch {
    $msg = '' + $_.Exception.Message
    $o.state = 'error'; $o.ok = $false; $o.error = $msg.Substring(0, [Math]::Min(120, $msg.Length))
}
$o.secs = [int]((Get-Date) - $t0).TotalSeconds
Save
