# مسارات ملفات نافذة التطبيق (مكتبات WebView2، ملفا تعريف المتصفح، ملف الحالة وسجل الخطأ). يقرؤه app_shell.ps1 والويدجت وrestart_admin.ps1.
# البند 1 من المجلس الاستشاري 19/11: العملية المرتفعة لا تحمّل مكتبة ولا ملف تعريف من مجلد يكتبه المستخدم العادي (LOCALAPPDATA وإضافة Teams)،
# وإلا استبدل أي برنامج بصلاحية المستخدم مكتبةً فنُفِّذت بصلاحية مسؤول. المرتفع يستعمل ProgramData\MonitorWidget\shell وحده، وهو مجلد
# يزرعه deploy_protected.ps1 للمسؤولين والنظام فقط، ويُرفض إن لم يكن كذلك. العملية العادية (التطوير) تبقى على LOCALAPPDATA كما كانت.

function Test-MwElevated {
    $id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    return $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-MwAdminOnlyAcl([string]$Owner, [object[]]$Rules) {
    # Rules: عناصر فيها Sid وAllow وRights (عدد). المالك والمسموح لهم بالكتابة: المسؤولون (S-1-5-32-544) والنظام (S-1-5-18) فقط.
    $trusted = @('S-1-5-32-544', 'S-1-5-18')
    if ($trusted -notcontains $Owner) { return $false }
    # WriteData|AppendData|WriteExtendedAttributes|DeleteSubdirectoriesAndFiles|WriteAttributes|Delete|ChangePermissions|TakeOwnership
    # والحقوق العامة GENERIC_WRITE وGENERIC_ALL كما تظهر أحيانًا في الوراثة
    $writeMask = 0x2 -bor 0x4 -bor 0x10 -bor 0x40 -bor 0x100 -bor 0x10000 -bor 0x40000 -bor 0x80000 -bor 0x40000000 -bor 0x10000000
    foreach ($r in $Rules) {
        if (-not $r.Allow -or ($trusted -contains $r.Sid)) { continue }
        if (([long]$r.Rights -band $writeMask) -ne 0) { return $false }
    }
    return $true
}

function Test-MwProtectedDir([string]$Path) {
    # مجلد موجود، مالكه المسؤولون أو النظام، ولا يكتب فيه غيرهما. أي خطأ في القراءة = غير محمي.
    try {
        if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
        $item = Get-Item -LiteralPath $Path -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { return $false }   # وصلة أو رابط: لا يُتبع
        $acl = Get-Acl -LiteralPath $Path
        $sid = [Security.Principal.SecurityIdentifier]
        $owner = (New-Object Security.Principal.NTAccount $acl.Owner).Translate($sid).Value
        $rules = foreach ($a in $acl.GetAccessRules($true, $true, $sid)) {
            [pscustomobject]@{ Sid = $a.IdentityReference.Value; Allow = ($a.AccessControlType -eq 'Allow'); Rights = [long][int]$a.FileSystemRights }
        }
        return (Test-MwAdminOnlyAcl $owner @($rules))
    } catch { return $false }
}

function Get-MwShellPaths([bool]$Elevated = (Test-MwElevated), [string]$MachineDir = '', [string]$UserDir = '') {
    # Usable=$false: مرتفع والمجلد المحمي غائب أو غير سليم؛ فلا مكتبات ولا ملف تعريف، والمستدعي يفتح الرابط في المتصفح الافتراضي.
    if (-not $MachineDir) { $MachineDir = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MonitorWidget\shell' }   # لا $env:ProgramData: متغيّر يورَث من المستخدم
    if (-not $UserDir) { $UserDir = Join-Path $env:LOCALAPPDATA 'MonitorWidget' }
    if ($Elevated) {
        $ok = Test-MwProtectedDir $MachineDir
        return [pscustomobject]@{
            Protected = $true; Usable = $ok; AllowTeamsCopy = $false; Base = $MachineDir
            Vendor = (Join-Path $MachineDir 'webview2'); ShellProfile = (Join-Path $MachineDir 'profiles\shellwin'); EdgeProfile = (Join-Path $MachineDir 'profiles\appwin')
            State = (Join-Path $MachineDir 'shell_state.json'); ErrorLog = (Join-Path $MachineDir 'shell_error.log')
        }
    }
    return [pscustomobject]@{
        Protected = $false; Usable = $true; AllowTeamsCopy = $true; Base = $UserDir
        Vendor = (Join-Path $UserDir 'webview2'); ShellProfile = (Join-Path $UserDir 'shellwin'); EdgeProfile = (Join-Path $UserDir 'appwin')
        State = (Join-Path $UserDir 'shell_state.json'); ErrorLog = (Join-Path $UserDir 'shell_error.log')
    }
}

function Clear-MwWebView2Overrides {
    # متغيّرات بيئة يقرؤها محمّل WebView2 فتغيّر الملف التنفيذي للمتصفح أو مجلد بياناته أو وسائطه؛ العملية المرتفعة ترثها من بيئة المستخدم
    foreach ($n in 'WEBVIEW2_BROWSER_EXECUTABLE_FOLDER', 'WEBVIEW2_USER_DATA_FOLDER', 'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS',
        'WEBVIEW2_RELEASE_CHANNEL_PREFERENCE', 'WEBVIEW2_PIPE_FOR_SCRIPT_DEBUGGER', 'WEBVIEW2_WAIT_FOR_SCRIPT_DEBUGGER') {
        [Environment]::SetEnvironmentVariable($n, $null, 'Process')
    }
}
