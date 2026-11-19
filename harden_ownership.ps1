# تصحيح ما كشفه فحص الإقلاع (bootaudit.py، البند 11 من المجلس 19/11): ملفات نظام ومكتبات تحمّلها عمليات مرتفعة يملكها
# حساب المستخدم العادي. المالك يملك WRITE_DAC ضمنًا، فأي برنامج بصلاحية المستخدم يمنح نفسه الكتابة ثم يستبدل الملف فيعمل
# مسؤولًا أو SYSTEM. تغيير المالك لا يمسّ قائمة الصلاحيات (DACL): من كان يكتب ما زال يكتب، ويسقط فقط حق تغيير الصلاحيات.
#
#   بلا وسائط          : معاينة فقط (لا يغيّر شيئًا): عدد العناصر التي يملكها المستخدم في كل جذر وعيّنة منها.
#   -Apply             : ينقل ملكيتها إلى مجموعة المسؤولين (S-1-5-32-544)، ويكتب سجل تراجع في المجلد المحمي.
#   -LimitTask <أسماء> : مهام مجدولة بصلاحية Highest تُجعل Limited (سكربتها في مجلد المستخدم ولا يحتاج مسؤولًا).
#   -Nahimic           : يشمل ProgramData\A-Volute: الملكية، ثم إعادة حساب الصلاحيات الموروثة للعناصر التي فيها قيد صريح
#                        للمستخدم (CREATOR OWNER تحوّل عند الإنشاء إلى «المستخدم: كامل»). اختياري لأن Nahimic محمية بقرار المالك.
#   -Undo <ملف.tsv>    : يعيد المالك والصلاحيات ومستوى المهام كما سجّلها -Apply.
# يطلب UAC مرة واحدة ويعمل بلا نافذة؛ النتيجة في ProgramData\MonitorWidget\deploy\harden_last.txt وتُطبع هنا.
param([switch]$Apply, [string[]]$LimitTask = @(), [switch]$Nahimic, [string]$Undo = '', [string]$UserSid = '')
$ErrorActionPreference = 'Stop'
$state = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MonitorWidget\deploy'
$resultFile = Join-Path $state 'harden_last.txt'

$id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', ('"' + $PSCommandPath + '"'),
        '-UserSid', [Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
    if ($Apply) { $argv += '-Apply' }
    if ($Nahimic) { $argv += '-Nahimic' }
    if ($Undo) { $argv += @('-Undo', ('"' + $Undo + '"')) }
    foreach ($t in $LimitTask) { $argv += @('-LimitTask', ('"' + $t + '"')) }
    try { $p = Start-Process (Get-Process -Id $PID).Path -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList $argv }
    catch { Write-Error 'رُفض طلب صلاحية المسؤول؛ لم يتغيّر شيء.' -ErrorAction Continue; exit 1 }
    if (Test-Path -LiteralPath $resultFile) { Get-Content -LiteralPath $resultFile -Encoding UTF8 }
    exit $p.ExitCode
}
# المستخدم المقصود يأتي من العملية غير المرتفعة (القيمة نفسها في UAC العادي، وتختلف إن رُفعت بحساب مسؤول آخر)
$userSid = if ($UserSid) { (New-Object Security.Principal.SecurityIdentifier($UserSid)).Value } else { [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
if ($userSid -in @('S-1-5-18', 'S-1-5-32-544')) { throw 'المستخدم المقصود لا يكون SYSTEM ولا المسؤولين.' }
if (-not (Test-Path -LiteralPath $state)) { New-Item -ItemType Directory -Path $state -Force | Out-Null }

Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
using Microsoft.Win32.SafeHandles;

public static class MwOwner {
    const uint READ_CONTROL = 0x20000, WRITE_DAC = 0x40000, WRITE_OWNER = 0x80000, SHARE_ALL = 7, OPEN_EXISTING = 3;
    const uint BACKUP = 0x02000000, OPEN_REPARSE = 0x00200000, SE_FILE_OBJECT = 1, OWNER_INFO = 1;
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr sa, uint disp, uint flags, IntPtr tmpl);
    [DllImport("advapi32.dll")]
    static extern uint GetSecurityInfo(SafeFileHandle h, uint type, uint info, out IntPtr owner, IntPtr g, IntPtr d, IntPtr s, out IntPtr sd);
    [DllImport("advapi32.dll")]
    static extern uint SetSecurityInfo(SafeFileHandle h, uint type, uint info, byte[] owner, IntPtr g, IntPtr d, IntPtr s);
    [DllImport("kernel32.dll")]
    static extern IntPtr LocalFree(IntPtr p);
    [StructLayout(LayoutKind.Sequential)] struct Luid { public uint Low; public int High; }
    [StructLayout(LayoutKind.Sequential)] struct TokPriv1 { public uint Count; public Luid Id; public uint Attrs; }
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool OpenProcessToken(IntPtr proc, uint access, out SafeAccessTokenHandle tok);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool LookupPrivilegeValueW(string sys, string name, out Luid id);
    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool AdjustTokenPrivileges(SafeAccessTokenHandle tok, bool disableAll, ref TokPriv1 state, uint len, IntPtr prev, IntPtr retLen);
    [DllImport("kernel32.dll")]
    static extern IntPtr GetCurrentProcess();

    // SeBackup وSeRestore موجودان معطّلين في رمز المسؤول: مع BACKUP في CreateFileW يمنحان قراءة الملكية وتغييرها إلى أي SID مهما كانت ACL
    public static void EnablePrivileges() {
        SafeAccessTokenHandle tok;
        if (!OpenProcessToken(GetCurrentProcess(), 0x20 | 0x8, out tok)) throw new Win32Exception(Marshal.GetLastWin32Error());
        using (tok) {
            foreach (var name in new[] { "SeBackupPrivilege", "SeRestorePrivilege", "SeTakeOwnershipPrivilege" }) {
                var tp = new TokPriv1 { Count = 1, Attrs = 0x2 };
                if (!LookupPrivilegeValueW(null, name, out tp.Id)) throw new Win32Exception(Marshal.GetLastWin32Error());
                if (!AdjustTokenPrivileges(tok, false, ref tp, 0, IntPtr.Zero, IntPtr.Zero)) throw new Win32Exception(Marshal.GetLastWin32Error());
                int code = Marshal.GetLastWin32Error();
                if (code != 0) throw new Win32Exception(code, name + " not granted");
            }
        }
    }

    static SafeFileHandle Open(string path, uint access) {
        // OPEN_REPARSE: الوصلة نفسها لا هدفها، فلا يُغيَّر مالك شيء خارج الجذر
        string p = path.StartsWith(@"\\") ? path : @"\\?\" + path;
        var h = CreateFileW(p, access, SHARE_ALL, IntPtr.Zero, OPEN_EXISTING, BACKUP | OPEN_REPARSE, IntPtr.Zero);
        if (h.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
        return h;
    }

    public static string GetOwner(string path) {
        using (var h = Open(path, READ_CONTROL)) {
            IntPtr owner, sd;
            uint rc = GetSecurityInfo(h, SE_FILE_OBJECT, OWNER_INFO, out owner, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, out sd);
            if (rc != 0) throw new Win32Exception((int)rc);
            try { return new SecurityIdentifier(owner).Value; } finally { LocalFree(sd); }
        }
    }

    public static void SetOwner(string path, string sid) {
        var s = new SecurityIdentifier(sid);
        var bytes = new byte[s.BinaryLength];
        s.GetBinaryForm(bytes, 0);
        using (var h = Open(path, WRITE_OWNER | READ_CONTROL)) {
            uint rc = SetSecurityInfo(h, SE_FILE_OBJECT, OWNER_INFO, bytes, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);
            if (rc != 0) throw new Win32Exception((int)rc);
        }
    }
}
'@
[MwOwner]::EnablePrivileges()

$admins = 'S-1-5-32-544'
$log = New-Object System.Collections.Generic.List[string]
function Say([string]$m) { $log.Add(('{0:HH:mm:ss} {1}' -f (Get-Date), $m)) }
$failed = 0

function Set-TaskLevel([string]$name, [string]$level) {
    $t = Get-ScheduledTask -TaskName $name
    $p = $t.Principal
    $np = New-ScheduledTaskPrincipal -UserId $p.UserId -LogonType $p.LogonType -RunLevel $level
    Set-ScheduledTask -TaskName $name -TaskPath $t.TaskPath -Principal $np | Out-Null
}

try {
    if ($Undo) {
        foreach ($line in Get-Content -LiteralPath $Undo -Encoding UTF8) {
            $kind, $path, $value = $line -split "`t", 3
            try {
                switch ($kind) {
                    'owner' { [MwOwner]::SetOwner($path, $value) }
                    'sddl' { $acl = Get-Acl -LiteralPath $path; $acl.SetSecurityDescriptorSddlForm($value, 'Access'); Set-Acl -LiteralPath $path -AclObject $acl }
                    'task' { Set-TaskLevel $path $value }
                }
            } catch { $failed++; Say ("تعذّر التراجع عن {0} {1}: {2}" -f $kind, $path, $_.Exception.Message) }
        }
        Say ("التراجع من {0} انتهى؛ الإخفاقات: {1}." -f $Undo, $failed)
    } else {

    # الجذور: ما كشفه الفحص (DriverStore وLenovo وملفات System32/SysWOW64 المباشرة) ومثله في Program Files
    $roots = @(
        @('C:\Windows\System32\DriverStore\FileRepository', $true),
        @('C:\Windows\Lenovo', $true),
        @('C:\Windows\System32', $false),
        @('C:\Windows\SysWOW64', $false),
        @('C:\Program Files', $true),
        @('C:\Program Files (x86)', $true))
    if ($Nahimic) { $roots += , @('C:\ProgramData\A-Volute', $true) }

    $undoFile = Join-Path $state ('harden_undo_{0:yyyyMMdd_HHmmss}.tsv' -f (Get-Date))
    $undoLines = New-Object System.Collections.Generic.List[string]
    $total = 0
    foreach ($r in $roots) {
        $root, $deep = $r
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        $opt = New-Object IO.EnumerationOptions
        $opt.RecurseSubdirectories = $deep; $opt.IgnoreInaccessible = $true; $opt.AttributesToSkip = [IO.FileAttributes]::ReparsePoint
        $items = @([IO.Directory]::EnumerateFileSystemEntries($root, '*', $opt))
        if ($deep) { $items = @($root) + $items }
        $mine = New-Object System.Collections.Generic.List[string]
        foreach ($p in $items) {
            try { if ([MwOwner]::GetOwner($p) -eq $userSid) { $mine.Add($p) } } catch { }
        }
        $total += $mine.Count
        Say ("{0}: {1} عنصرًا، يملك المستخدم منها {2}." -f $root, $items.Count, $mine.Count)
        if (-not $Apply) { $mine | Select-Object -First 5 | ForEach-Object { Say ("   " + $_) }; continue }
        $done = 0
        foreach ($p in $mine) {
            try { [MwOwner]::SetOwner($p, $admins); $undoLines.Add("owner`t$p`t$userSid"); $done++ }
            catch { $failed++; if ($failed -le 20) { Say ("   تعذّر: {0}: {1}" -f $p, $_.Exception.Message) } }
        }
        Say ("   نُقلت ملكية {0} إلى المسؤولين." -f $done)
    }

    if ($Nahimic) {
        # القيود الموروثة من CREATOR OWNER تحمل SID المستخدم حرفيًّا؛ إعادة الحساب بعد تغيير المالك تجعلها للمسؤولين
        $nahimicRoot = 'C:\ProgramData\A-Volute'
        $opt = New-Object IO.EnumerationOptions
        $opt.RecurseSubdirectories = $true; $opt.IgnoreInaccessible = $true; $opt.AttributesToSkip = [IO.FileAttributes]::ReparsePoint
        $withUser = @(foreach ($p in [IO.Directory]::EnumerateFileSystemEntries($nahimicRoot, '*', $opt)) {
            $acl = Get-Acl -LiteralPath $p
            if (@($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]) | Where-Object { $_.IdentityReference.Value -eq $userSid }).Count) { , @($p, $acl.GetSecurityDescriptorSddlForm('Access')) }
        })
        Say ("{0}: {1} عنصرًا فيه قيد صريح للمستخدم." -f $nahimicRoot, $withUser.Count)
        if ($Apply) {
            foreach ($w in $withUser) {
                $undoLines.Add("sddl`t$($w[0])`t$($w[1])")
                & icacls.exe $w[0] /reset /C /Q | Out-Null
                if ($LASTEXITCODE -ne 0) { $failed++; Say ("   تعذّرت إعادة حساب صلاحيات " + $w[0]) }
            }
        }
    }

    foreach ($name in $LimitTask) {
        $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $t) { $failed++; Say ("المهمة {0} غير موجودة." -f $name); continue }
        $lvl = [string]$t.Principal.RunLevel
        if ($lvl -ne 'Highest') { Say ("المهمة {0} مستواها {1} أصلًا." -f $name, $lvl); continue }
        if (-not $Apply) { Say ("المهمة {0}: Highest ← Limited (معاينة)." -f $name); continue }
        Set-TaskLevel $name 'Limited'
        $undoLines.Add("task`t$name`tHighest")
        Say ("المهمة {0}: صارت Limited." -f $name)
    }

    if ($Apply) {
        Set-Content -LiteralPath $undoFile -Value $undoLines -Encoding UTF8
        Say ("انتهى التطبيق: {0} تغييرًا، والإخفاقات {1}. سجل التراجع: {2}" -f $undoLines.Count, $failed, $undoFile)
    } else {
        Say ("معاينة فقط: {0} عنصرًا يملكه المستخدم. للتطبيق أعِد التشغيل بـ -Apply." -f $total)
    }
    }
} catch {
    $failed++
    Say ("فشل: " + $_.Exception.Message)
} finally {
    Set-Content -LiteralPath $resultFile -Value $log -Encoding UTF8
}
exit [int]($failed -gt 0)
