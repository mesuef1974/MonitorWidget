# النشر المحمي لمرصد الجهاز: الخلفية المرتفعة لا تشغّل شيئًا يكتبه المستخدم العادي (مجلس 19/11/2026، البنود الحرجة 1-3).
#
# المشكلة: مهام الخادم والوكيل والويدجت تعمل بصلاحية مسؤول (قرار المالك 13/11) لكنها كانت تشغّل pythonw من AppData
# والكود من Documents، ورمز ‎.token‎ يقرؤه كل برنامج عادي، وسياسة التنظيف المرتفع في Documents. فأي برنامج بصلاحية
# عادية يعدّل ملفًا منها يعمل مسؤولًا.
#
# ما يفعله (بطلب UAC واحد بلا نافذة، ويشغّله المالك بعد كل دمج بدل restart_agent.ps1):
#   1. يرفض نشر ما لم يُدمج: HEAD يجب أن يكون ضمن origin/main، والكود يُؤخذ من الالتزام (git archive) لا من الشجرة.
#   2. مفسّر Python معزول في ProgramData\MonitorWidget\python: نسخة من المفسّر المثبّت بلا site-packages، بملف ._pth
#      يثبّت sys.path (لا PYTHONPATH ولا site للمستخدم)، وموقّع من PSF.
#   3. الكود في ProgramData\MonitorWidget\app (المسؤولون والنظام: كامل؛ المستخدمون: قراءة وتنفيذ). يُحذف منه ما حُذف
#      من المستودع وفق بيان النشر السابق فقط، فلا تُمسّ البيانات أبدًا.
#   4. أول مرة: تُنقل البيانات (القاعدة والإعدادات والسجلات والحجر والنسخ) من مجلد المستودع إلى app، ويُدوَّر الرمز:
#      ‎.token‎ جديد لا يقرؤه إلا المسؤولون والنظام، و‎.token.read‎ للبرامج العادية يقبله الخادم في GET القرائية وحدها.
#   5. سياسة التنظيف: نسخة محمية في ProgramData\MonitorWidget\cleanup ومهام Cleanup_v2_* تقرؤها هي.
#   6. المهام: الوكيل والخادم من المفسّر والكود المحميين، والمجمّع يعمل داخل الوكيل (تُعطَّل مهمته)، والويدجت من app.
#   7. يعيد التشغيل ويتحقق: الخادم الجديد من المسار المحمي ويجيب admin=true بالرمز الجديد، والويدجت ظهر من app.
#
#   -Rollback : يعيد تعريفات المهام الأصلية وينقل البيانات إلى مجلد المستودع (الرمز المدوَّر يبقى).
#   -RefreshPython : يعيد نسخ المفسّر ولو لم يتغيّر إصداره.
param([switch]$Rollback, [switch]$RefreshPython, [string]$PythonHome = '')
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$secure = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MonitorWidget'
$app = Join-Path $secure 'app'
$py = Join-Path $secure 'python'
$cleanupDir = Join-Path $secure 'cleanup'
$state = Join-Path $secure 'deploy'
$resultFile = Join-Path $state 'last_result.txt'   # في مجلد محمي لا في مجلد المستخدم (لا يكتب المسؤول عبر وصلة زرعها غيره)
$port = 47832
$userPolicy = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Documents\Scripts\Cleanup\v2\cleanup-policy.json'
$protPolicy = Join-Path $cleanupDir 'cleanup-policy.json'
$backendTasks = @('MonitorWidget-Agent', 'MonitorWidget-Server-Logon', 'MonitorWidget-Collector', 'MonitorWidget-Launch')
$policyTasks = @('Cleanup_v2_Daily', 'Cleanup_v2_Weekly', 'Cleanup_v2_Monthly', 'Cleanup_v2_Quarterly')
# ما لا يُنقل من مجلد المستودع أبدًا: أدوات التطوير، وملفات المالك غير المتتبَّعة، ونسخ التحرير، والرموز القديمة (تُحذف).
$keepInSource = @('.git', '.claude', '.ruff_cache', '__pycache__', 'tests', 'docs', 'data', 'web', '.github',
    '.token', '.token.old', '.token.read', '.protected-launch', 'crash.dmp', 'crash_analysis.txt', 'proc_report.txt',
    'purge.exe', 'purge_mode.txt', 'install_purge.ok')
# ما لا يُنسخ من الالتزام إلى app: ليس جزءًا من التطبيق الحي.
$notShipped = '^(tests|docs|\.github|\.claude)/'

$id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $exe = (Get-Process -Id $PID).Path
    $argv = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', ('"' + $PSCommandPath + '"'))
    if ($Rollback) { $argv += '-Rollback' }
    if ($RefreshPython) { $argv += '-RefreshPython' }
    if ($PythonHome) { $argv += @('-PythonHome', ('"' + $PythonHome + '"')) }
    try { $p = Start-Process $exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList $argv }
    catch { Write-Error 'رُفض طلب صلاحية المسؤول؛ لم يتغيّر شيء.' -ErrorAction Continue; exit 1 }
    if (Test-Path -LiteralPath $resultFile) { Get-Content -LiteralPath $resultFile -Encoding UTF8 }
    exit $p.ExitCode
}

# البرنامج بمساره الكامل: أسماء الأوامر في PowerShell لا تفرّق بين الحروف، والدالة تُقدَّم على البرنامج، فدالة باسم git
# تستدعي نفسها بلا نهاية (فشل النشر الأول 19/11: call depth overflow).
$gitExe = (Get-Command git -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$log = New-Object System.Collections.Generic.List[string]
function Say([string]$m) { $log.Add(('{0:HH:mm:ss} {1}' -f (Get-Date), $m)) }
$admins = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
$system = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')

function Invoke-RepoGit([string[]]$GitArgs) {
    # المستودع يملكه المستخدم والعملية مسؤول: safe.directory لهذا المجلد وحده
    # Continue محليًا: في Windows PowerShell 5.1 يرمي Stop على أي سطر في stderr من أمر أصلي
    $ErrorActionPreference = 'Continue'
    # الأسماء غير اللاتينية: core.quotePath=false يمنع git من اقتباسها بثمانيات ("docs\330\245...")، وناتجه UTF-8
    # يُفكّ بـ UTF-8 لا بصفحة الرموز الافتراضية للطرفية (فشل النشر الثاني 19/11 عند docs/<اسم عربي>).
    $enc = [Console]::OutputEncoding
    [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
    try { $out = @(& $gitExe -c core.quotePath=false -c ("safe.directory=" + ($src -replace '\\', '/')) -C $src @GitArgs 2>&1) }
    finally { [Console]::OutputEncoding = $enc }
    if ($LASTEXITCODE -ne 0) { throw ('git ' + ($GitArgs -join ' ') + ': ' + ($out -join ' ')) }
    $out | Where-Object { $_ -is [string] -and $_ -ne '' }   # بلا مخرجات = لا عناصر (لا $null واحد يُعدّ عنصرًا)
}

function Test-RepoGit([string[]]$GitArgs) {
    # أوامر git التي جوابها رمز الخروج وحده (check-ignore، merge-base --is-ancestor)
    $ErrorActionPreference = 'Continue'
    & $gitExe -c core.quotePath=false -c ("safe.directory=" + ($src -replace '\\', '/')) -C $src @GitArgs 2>$null | Out-Null
    $LASTEXITCODE -eq 0
}

function Get-ServerPid {
    try { [int](Invoke-RestMethod "http://127.0.0.1:$port/api/healthz" -TimeoutSec 3).pid }
    catch { if ($_.Exception.Response) { -1 } else { 0 } }
}

function Set-AdminOnly([string]$Path) {
    # ملف لا يقرؤه إلا المسؤولون والنظام، مالكه المسؤولون، بلا وراثة
    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetOwner($admins)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($s in @($admins, $system)) {
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($s, 'FullControl', 'Allow')))
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Write-Secret([string]$Path, [switch]$AdminOnly) {
    # الملف يُنشأ فارغًا ويُقفل قبل كتابة القيمة، فلا تمرّ لحظة يكون فيها مقروءًا بالوراثة
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $value = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    [IO.File]::WriteAllText($Path, '')
    if ($AdminOnly) { Set-AdminOnly $Path }
    [IO.File]::WriteAllText($Path, $value, (New-Object Text.UTF8Encoding($false)))
    $value
}

function Reset-Tree([string]$Path) {
    # الملكية للمسؤولين ثم الصلاحيات موروثة من المجلد المحمي (المستخدم المالك يستطيع تغيير صلاحيات ملفه، فلا يبقى مالكًا)
    & icacls $Path /setowner '*S-1-5-32-544' /T /C /Q | Out-Null
    & icacls $Path /reset /T /C /Q | Out-Null
}

function Set-TaskExecXml([string]$Xml, [string]$Command, [string]$Arguments, [string]$WorkDir) {
    # يبدّل الأمر ووسائطه ومجلد العمل وحدها؛ المشغّلات والمبدأ والإعدادات تبقى كما هي
    $doc = New-Object System.Xml.XmlDocument
    $doc.PreserveWhitespace = $true
    $doc.LoadXml($Xml)
    $ns = $doc.DocumentElement.NamespaceURI
    $m = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
    $m.AddNamespace('t', $ns)
    $execs = @($doc.SelectNodes('/t:Task/t:Actions/t:Exec', $m))
    if ($execs.Count -ne 1) { throw 'تعريف المهمة بلا أمر واحد واضح؛ لم يُعدَّل.' }
    foreach ($pair in @(@('Command', $Command), @('Arguments', $Arguments), @('WorkingDirectory', $WorkDir))) {
        if ([string]::IsNullOrEmpty($pair[1])) { continue }   # $null الممرَّر إلى [string] يصل ''؛ الفارغ = لا تبديل
        $node = $execs[0].SelectSingleNode('t:' + $pair[0], $m)
        if (-not $node) { $node = $execs[0].AppendChild($doc.CreateElement($pair[0], $ns)) }
        $node.InnerText = $pair[1]
    }
    $doc.OuterXml
}

function Stop-Backend {
    foreach ($n in $backendTasks) { Stop-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue }
    $roots = '(' + [regex]::Escape($src) + '|' + [regex]::Escape($app) + ')'
    $pyRx = $roots + '[\\/](agent|server|collector)\.py'
    $psRx = $roots + '[\\/](widget|app_shell)\.ps1'
    $all = Get-CimInstance Win32_Process
    $victims = @($all | Where-Object {
            ($_.Name -in 'python.exe', 'pythonw.exe' -and $_.CommandLine -match $pyRx) -or
            ($_.Name -in 'pwsh.exe', 'powershell.exe' -and $_.CommandLine -match $psRx -and $_.ProcessId -ne $PID) })
    $victims | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    for ($i = 0; $i -lt 30 -and (Get-ServerPid) -ne 0; $i++) { Start-Sleep -Milliseconds 500 }
    if ((Get-ServerPid) -ne 0) { throw 'ما زال خادم يستمع على المنفذ بعد الإيقاف؛ لم يُنشر شيء.' }
    Say ('أُوقفت الخلفية والويدجت (' + $victims.Count + ' عملية).')
}

function Save-TaskXml([string]$Dir) {
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    foreach ($n in ($backendTasks + $policyTasks)) {
        if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
            Set-Content -LiteralPath (Join-Path $Dir ($n + '.xml')) -Value (Export-ScheduledTask -TaskName $n) -Encoding Unicode
        }
    }
}

function Restore-TaskXml([string]$Dir) {
    foreach ($f in Get-ChildItem -LiteralPath $Dir -Filter '*.xml') {
        Register-ScheduledTask -TaskName $f.BaseName -Xml (Get-Content -LiteralPath $f.FullName -Raw) -Force | Out-Null
    }
}

function Set-PythonPath([string]$Dir) {
    # ._pth يجعل sys.path هذه الأسطر وحدها ويعطّل site وPYTHON* (عزل تام عن مجلدات المستخدم). app أولًا: مع ._pth لا يُضاف
    # مجلد السكربت إلى sys.path، فلو سبقته Lib لحُمّلت sched القياسية بدل sched.py في المشروع (فشل النشر الثالث 19/11:
    # /api/mini يعيد 500 بـ module 'sched' has no attribute 'next_run'). يُكتب في كل تشغيل لأن نسخ المفسّر يُتخطّى إن طابق.
    $dll = Get-ChildItem -LiteralPath $Dir -Filter 'python3*.dll' | Where-Object Name -ne 'python3.dll' | Select-Object -First 1
    if (-not $dll) { throw "لا مكتبة python3XX.dll في $Dir." }
    $pth = Join-Path $Dir ($dll.BaseName + '._pth')
    $want = [string[]]@('..\app', 'Lib', 'DLLs')
    $have = if (Test-Path -LiteralPath $pth) { [string[]]@([IO.File]::ReadAllLines($pth)) } else { @() }
    if (($have -join '|') -ne ($want -join '|')) {
        [IO.File]::WriteAllLines($pth, $want, (New-Object Text.UTF8Encoding($false)))
        Say ('ترتيب sys.path في المفسّر المحمي: ' + ($want -join '، '))
    }
    $paths = @(& (Join-Path $Dir 'python.exe') -c 'import sys,json,sqlite3,ctypes;print(json.dumps(sys.path))' | ConvertFrom-Json)
    $bad = @($paths | Where-Object { $_ -and -not ([IO.Path]::GetFullPath($_)).StartsWith($secure, [StringComparison]::OrdinalIgnoreCase) })
    if ($bad.Count) { throw ('المفسّر المحمي يبحث خارج المجلد المحمي: ' + ($bad -join '; ')) }
    if (-not $paths.Count -or ([IO.Path]::GetFullPath($paths[0])).TrimEnd('\') -ne ([IO.Path]::GetFullPath((Join-Path $Dir '..\app'))).TrimEnd('\')) {
        throw ('أول sys.path في المفسّر المحمي ليس app: ' + ($paths -join '; '))
    }
}

function Install-Python {
    $srcHome = $PythonHome
    if (-not $srcHome) {
        $t = Get-ScheduledTask -TaskName 'MonitorWidget-Agent' -ErrorAction SilentlyContinue
        $exe = if ($t) { [string]$t.Actions[0].Execute } else { '' }
        if ($exe -and -not $exe.StartsWith($secure, [StringComparison]::OrdinalIgnoreCase)) { $srcHome = Split-Path -Parent $exe }
    }
    $have = Join-Path $py 'pythonw.exe'
    if (-not $srcHome) {
        if (Test-Path -LiteralPath $have) { Say 'المفسّر المحمي موجود؛ لم يُنسخ.'; return }
        throw 'لا مفسّر محمي ولا مصدر له؛ مرّر -PythonHome.'
    }
    $srcExe = Join-Path $srcHome 'python.exe'
    if (-not (Test-Path -LiteralPath $srcExe)) { throw "لا python.exe في $srcHome" }
    $sig = Get-AuthenticodeSignature -LiteralPath $srcExe
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') { throw 'مفسّر المصدر غير موقّع من PSF توقيعًا صالحًا.' }
    $want = (Get-Item -LiteralPath $srcExe).VersionInfo.FileVersion
    $cur = if (Test-Path -LiteralPath (Join-Path $py 'python.exe')) { (Get-Item -LiteralPath (Join-Path $py 'python.exe')).VersionInfo.FileVersion } else { '' }
    if ($cur -eq $want -and -not $RefreshPython) { Say "المفسّر المحمي $cur مطابق؛ لم يُنسخ."; return }
    $tmp = $py + '.new'
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
    $skip = @('Lib\site-packages', 'Lib\test', 'Lib\idlelib', 'Lib\tkinter', 'Lib\turtledemo', 'Lib\ensurepip', 'Scripts', 'Doc', 'include', 'libs', 'tcl', 'Tools', 'share') |
        ForEach-Object { Join-Path $srcHome $_ }
    & robocopy $srcHome $tmp /E /COPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP /XD @skip | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "فشل نسخ المفسّر (robocopy $LASTEXITCODE)." }
    $dll = Get-ChildItem -LiteralPath $tmp -Filter 'python3*.dll' | Where-Object Name -ne 'python3.dll' | Select-Object -First 1
    if (-not $dll) { throw 'لا مكتبة python3XX.dll في النسخة.' }
    Set-PythonPath $tmp
    if (Test-Path -LiteralPath $py) { Remove-Item -LiteralPath $py -Recurse -Force }
    Move-Item -LiteralPath $tmp -Destination $py
    Reset-Tree $py
    Say "نُسخ المفسّر المحمي $want من $srcHome (sys.path محصور في $secure)."
}

function Install-Code([string]$Commit) {
    $stage = Join-Path $state 'staging'
    $zip = Join-Path $state 'release.zip'
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    Invoke-RepoGit @('archive', '--format=zip', '-o', $zip, $Commit) | Out-Null
    Expand-Archive -LiteralPath $zip -DestinationPath $stage
    Remove-Item -LiteralPath $zip -Force
    $files = @(Invoke-RepoGit @('ls-tree', '-r', '--name-only', $Commit) | Where-Object { $_ -notmatch $notShipped })
    New-Item -ItemType Directory -Force -Path $app | Out-Null
    $manifestPath = Join-Path $app '.deployment\manifest.json'
    $old = @()
    if (Test-Path -LiteralPath $manifestPath) { $old = @((Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json).files) }
    foreach ($rel in $files) {
        $from = Join-Path $stage ($rel -replace '/', '\')
        $to = Join-Path $app ($rel -replace '/', '\')
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $to) | Out-Null
        Copy-Item -LiteralPath $from -Destination $to -Force
    }
    $gone = @($old | Where-Object { $_ -notin $files })
    foreach ($rel in $gone) { Remove-Item -LiteralPath (Join-Path $app ($rel -replace '/', '\')) -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $stage -Recurse -Force
    Remove-Item -LiteralPath (Join-Path $app '.protected-launch') -Force -ErrorAction SilentlyContinue   # في app يشغّل الويدجت مباشرة
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $manifestPath) | Out-Null
    [ordered]@{ destination = $app; commit = $Commit; deployedAt = (Get-Date).ToString('o'); files = $files } |
        ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    foreach ($rel in $files) { & icacls (Join-Path $app ($rel -replace '/', '\')) /setowner '*S-1-5-32-544' /C /Q | Out-Null }
    Say ('نُشر الالتزام ' + $Commit.Substring(0, 7) + ' إلى app: ' + $files.Count + ' ملفًا، وحُذف ' + $gone.Count + '.')
}

function Update-QuarantinePaths([string]$Db, [string]$From, [string]$To) {
    # الحجر يخزّن مسار كل عنصر مطلقًا (quarantine.qpath)، والمخزن يرفض ما خرج عن جذره: بعد النقل يُبدَّل البادئ القديم
    # بالجديد، وإلا تعذّر استرجاع المحجوز قبل النشر وتفريغه.
    if (-not (Test-Path -LiteralPath $Db)) { return }
    $fix = Join-Path $state 'fix_qpath.py'
    $code = @'
import sqlite3, sys
db, old, new = sys.argv[1:4]
sep = "\\"
old, new = old.rstrip(sep) + sep, new.rstrip(sep) + sep
c = sqlite3.connect(db, timeout=30)
has = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quarantine'").fetchone()
n = 0
if has:
    n = c.execute("UPDATE quarantine SET qpath = ? || substr(qpath, ?) WHERE lower(substr(qpath, 1, ?)) = lower(?)",
                  (new, len(old) + 1, len(old), old)).rowcount
    c.commit()
c.close()
print(n)
'@
    [IO.File]::WriteAllText($fix, $code, (New-Object Text.UTF8Encoding($false)))
    $n = & (Join-Path $py 'python.exe') $fix $Db $From $To
    if ($LASTEXITCODE -ne 0) { throw 'تعذّر تحديث مسارات الحجر في القاعدة.' }
    Remove-Item -LiteralPath $fix -Force
    Say ('حُدّثت مسارات الحجر في القاعدة: ' + (($n | Select-Object -Last 1) -as [string]) + ' عنصرًا.')
}

function Move-Data {
    $marker = Join-Path $state 'migrated.json'
    $moved = New-Object System.Collections.Generic.List[string]
    # العلامة تُكتب بعد كل عنصر (complete=false) لا في النهاية وحدها: إن فشل نقلٌ في المنتصف عرف الإرجاع أن بعض البيانات
    # صار في app فلا يعيد المهام القديمة (تنشئ قاعدة فارغة في المستودع)، والتشغيل التالي يستأنف، و-Rollback يعيد ما نُقل.
    if (Test-Path -LiteralPath $marker) {
        $prev = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
        if ($prev.complete -ne $false) { return $false }   # علامة قديمة بلا الحقل = نقل مكتمل
        foreach ($n in @($prev.items)) { $moved.Add([string]$n) }
        Say ('استئناف نقل بيانات لم يكتمل (' + $moved.Count + ' عنصرًا نُقل سابقًا).')
    }
    $writeMarker = {
        param([bool]$Done)
        [IO.File]::WriteAllText($marker, ([ordered]@{ at = (Get-Date).ToString('o'); complete = $Done; items = $moved.ToArray() } | ConvertTo-Json),
            (New-Object Text.UTF8Encoding($false)))
    }
    foreach ($item in Get-ChildItem -LiteralPath $src -Force) {
        $n = $item.Name
        if ($n -in $keepInSource -or $n -like '*.bak*' -or $n -like '*.edit_pending_*') { continue }
        if (@(Invoke-RepoGit @('ls-files', '--', $n)).Count) { continue }            # متتبَّع: كود لا بيانات
        if (-not (Test-RepoGit @('check-ignore', '-q', '--', $n))) { continue }   # غير متتبَّع وغير متجاهَل: ملفات المالك، تُترك
        $to = Join-Path $app $n
        if (Test-Path -LiteralPath $to) {
            if ($item.PSIsContainer) {
                & robocopy $item.FullName $to /E /MOVE /COPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
                if ($LASTEXITCODE -ge 4) { throw "فشل دمج المجلد $n (robocopy $($LASTEXITCODE): 4 تعارض بين ملف ومجلد، 8 فأكثر خطأ نسخ)." }
            }
            else { Move-Item -LiteralPath $item.FullName -Destination $to -Force }
        }
        else { Move-Item -LiteralPath $item.FullName -Destination $to }   # القرص نفسه: إعادة تسمية فورية حتى للحجر الكبير
        if ($n -notin $moved) { $moved.Add($n) }
        & $writeMarker $false
    }
    Reset-Tree $app
    Update-QuarantinePaths (Join-Path $app 'monitor.db') (Join-Path $src 'quarantine') (Join-Path $app 'quarantine')
    & $writeMarker $true
    Say ('نُقلت البيانات إلى app: ' + ($moved -join '، '))
    $true
}

function Set-Tokens([bool]$Rotate) {
    $full = Join-Path $app '.token'
    $read = Join-Path $app '.token.read'
    if ($Rotate -or -not (Test-Path -LiteralPath $full)) { Write-Secret $full -AdminOnly | Out-Null; Say 'دُوِّر الرمز الكامل (لا يقرؤه إلا المسؤولون والنظام).' }
    else { Set-AdminOnly $full }
    if ($Rotate -or -not (Test-Path -LiteralPath $read)) { Write-Secret $read | Out-Null; Say 'أُنشئ رمز القراءة (GET القرائية وحدها).' }
    foreach ($n in '.token', '.token.old') { Remove-Item -LiteralPath (Join-Path $src $n) -Force -ErrorAction SilentlyContinue }
}

function Set-PolicyTasks {
    if (-not (Test-Path -LiteralPath $protPolicy)) {
        if (-not (Test-Path -LiteralPath $userPolicy)) { throw "لا سياسة تنظيف في $userPolicy لزرع نسختها المحمية." }
        Copy-Item -LiteralPath $userPolicy -Destination $protPolicy
        & icacls $protPolicy /setowner '*S-1-5-32-544' /C /Q | Out-Null
        & icacls $protPolicy /reset /C /Q | Out-Null
        Say 'زُرعت النسخة المحمية من سياسة التنظيف.'
    }
    foreach ($n in $policyTasks) {
        $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
        if (-not $t) { continue }
        $a = [string]$t.Actions[0].Arguments
        $fixed = [regex]::Replace($a, '-PolicyPath\s+("[^"]*"|\S+)', ('-PolicyPath "' + $protPolicy + '"'))
        if ($fixed -eq $a) { continue }
        $xml = Set-TaskExecXml (Export-ScheduledTask -TaskName $n) $null $fixed $null
        Register-ScheduledTask -TaskName $n -Xml $xml -Force | Out-Null
        Say "$n تقرأ السياسة المحمية."
    }
}

function Set-BackendTasks {
    $pyw = Join-Path $py 'pythonw.exe'
    $map = @{
        'MonitorWidget-Agent'        = @($pyw, ('"' + (Join-Path $app 'agent.py') + '"'), $app)
        'MonitorWidget-Server-Logon' = @($pyw, ('"' + (Join-Path $app 'server.py') + '"'), $app)
        'MonitorWidget-Launch'       = @((Join-Path ([Environment]::SystemDirectory) 'wscript.exe'), ('"' + (Join-Path $app 'launch_mini.vbs') + '"'), $app)
    }
    foreach ($n in $map.Keys) {
        if (-not (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue)) { throw "المهمة $n غير مسجّلة." }
        $v = $map[$n]
        Register-ScheduledTask -TaskName $n -Xml (Set-TaskExecXml (Export-ScheduledTask -TaskName $n) $v[0] $v[1] $v[2]) -Force | Out-Null
    }
    # النشر المحمي يجمع داخل الوكيل المرتفع (agent.register_protected_collector)؛ مهمة المجمّع المستقلة (Highest على pythonw
    # في مجلد المستخدم) يُلغى تسجيلها لا تعطيلها فقط (البند 4 من المجلس 19/11). تعريفها محفوظ في tasks-original للتراجع.
    if (Get-ScheduledTask -TaskName 'MonitorWidget-Collector' -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName 'MonitorWidget-Collector' -Confirm:$false }
    # نسخة المستودع من launch_mini.vbs تحيل إلى المهمة بدل تشغيل ويدجت من مجلد يكتبه المستخدم
    [IO.File]::WriteAllText((Join-Path $src '.protected-launch'), '')
    Say 'المهام تشغّل المفسّر والكود المحميين؛ المجمّع داخل الوكيل.'
}

function Start-AndVerify {
    Start-ScheduledTask -TaskName 'MonitorWidget-Server-Logon'
    Start-ScheduledTask -TaskName 'MonitorWidget-Agent'
    $tok = [IO.File]::ReadAllText((Join-Path $app '.token')).Trim()
    $m = $null
    for ($i = 0; $i -lt 60 -and -not $m; $i++) {
        Start-Sleep 1
        try { $m = Invoke-RestMethod "http://127.0.0.1:$port/api/mini" -Headers @{ 'X-Token' = $tok } -TimeoutSec 3 } catch {}
    }
    if (-not $m) { throw 'لم يستجب الخادم المحمي خلال 60 ثانية؛ راجع monitor.log في app.' }
    $srvPid = Get-ServerPid
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$srvPid"
    if (-not $proc -or -not ([string]$proc.ExecutablePath).StartsWith($py, [StringComparison]::OrdinalIgnoreCase) -or [string]$proc.CommandLine -notmatch [regex]::Escape($app)) {
        throw ('الخادم المستمع ليس من المسار المحمي: ' + $proc.ExecutablePath + ' ' + $proc.CommandLine)
    }
    if (-not [bool]$m.admin) { throw 'الخادم المحمي يجيب admin=false.' }
    $rtok = [IO.File]::ReadAllText((Join-Path $app '.token.read')).Trim()
    try { Invoke-RestMethod "http://127.0.0.1:$port/api/mini" -Headers @{ 'X-Token' = $rtok } -TimeoutSec 5 | Out-Null } catch { throw 'رمز القراءة رُفض في GET.' }
    $postRejected = $false
    # مسار غير موجود عمدًا: المصادقة تسبق التوجيه، فالرفض 401 لا 404، ولا يُنفَّذ أي فعل حتى لو قُبل الرمز خطأً
    try { Invoke-RestMethod "http://127.0.0.1:$port/api/auth-probe-none" -Method Post -ContentType 'application/json' -Body '{}' -Headers @{ 'X-Token' = $rtok } -TimeoutSec 5 | Out-Null }
    catch { $postRejected = ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401) }
    if (-not $postRejected) { throw 'رمز القراءة قُبل في POST؛ يجب أن يُرفض بـ 401.' }
    Say "الخادم المحمي يعمل (PID $srvPid، admin=true)، ورمز القراءة يُقبل في GET ويُرفض في POST."
    Start-ScheduledTask -TaskName 'MonitorWidget-Launch'
    $w = $null
    for ($i = 0; $i -lt 30 -and -not $w; $i++) {
        Start-Sleep 1
        $w = Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'pwsh.exe', 'powershell.exe' -and $_.CommandLine -match ([regex]::Escape((Join-Path $app 'widget.ps1'))) } | Select-Object -First 1
    }
    if ($w) { Say ('الويدجت يعمل من app (PID ' + $w.ProcessId + ').') } else { Say 'تحذير: لم تظهر عملية الويدجت من app خلال 30 ثانية.' }
}

function Invoke-Rollback {
    $orig = Join-Path $state 'tasks-original'
    if (-not (Test-Path -LiteralPath $orig)) { throw 'لا نسخة أصلية من تعريفات المهام؛ لا تراجع.' }
    Stop-Backend
    Restore-TaskXml $orig
    # المجمّع يعود بصلاحية محدودة: مفسّره في مجلد المستخدم، فـ Highest عليه تنفيذ مسؤول لما يزرعه المستخدم (البند 4)
    $col = Get-ScheduledTask -TaskName 'MonitorWidget-Collector' -ErrorAction SilentlyContinue
    if ($col) {
        $col.Principal.RunLevel = 'Limited'
        Set-ScheduledTask -InputObject $col | Out-Null
        Enable-ScheduledTask -TaskName 'MonitorWidget-Collector' | Out-Null
    }
    $marker = Join-Path $state 'migrated.json'
    if (Test-Path -LiteralPath $marker) {
        foreach ($n in @((Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json).items)) {
            $from = Join-Path $app $n
            if ((Test-Path -LiteralPath $from) -and -not (Test-Path -LiteralPath (Join-Path $src $n))) { Move-Item -LiteralPath $from -Destination (Join-Path $src $n) }
        }
        Update-QuarantinePaths (Join-Path $src 'monitor.db') (Join-Path $app 'quarantine') (Join-Path $src 'quarantine')
        if (Test-Path -LiteralPath (Join-Path $app '.token')) {   # نقل لم يكتمل قد يسبق إنشاء الرمز المحمي
            Copy-Item -LiteralPath (Join-Path $app '.token') -Destination (Join-Path $src '.token') -Force
            & icacls (Join-Path $src '.token') /reset /C /Q | Out-Null
        }
        Remove-Item -LiteralPath $marker -Force
    }
    Remove-Item -LiteralPath (Join-Path $src '.protected-launch') -Force -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName 'MonitorWidget-Agent'
    Start-ScheduledTask -TaskName 'MonitorWidget-Launch'
    Say 'تراجع: المهام بتعريفاتها الأصلية والبيانات في مجلد المستودع.'
}

function Protect-SecureTree {
    # ثغرة كُشفت 19/11 أثناء البند 1: ما أنشأه مسؤول بحساب المالك يملكه حساب المالك نفسه (المالك الافتراضي «منشئ الكائن»)،
    # والمالك يملك ضمنًا تغيير الصلاحيات (WRITE_DAC) حتى من عملية غير مرتفعة؛ فأي برنامج عادي كان يستطيع أن يمنح نفسه الكتابة على
    # bin\elevated_clean.ps1 وCleanupEngine.ps1 وجذر المجلد نفسه ثم يُنفَّذ ما كتبه مرتفعًا. العلاج: عنصر OWNER RIGHTS (S-1-3-4)
    # موروث بقراءة وتنفيذ فقط يلغي الحق الضمني للمالك، على الجذر وعلى كل مجلد فرعي صلاحياته لا ترث؛ ثم الملكية للمسؤولين في الشجرة كلها.
    $dirs = @($secure) + @(Get-ChildItem -LiteralPath $secure -Directory -Force | Where-Object {
            -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -and (Get-Acl -LiteralPath $_.FullName).AreAccessRulesProtected
        } | ForEach-Object { $_.FullName })
    foreach ($d in $dirs) {
        & icacls $d /grant '*S-1-3-4:(OI)(CI)(RX)' /C /Q | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "تعذّر وضع OWNER RIGHTS على $d" }
    }
    & icacls $secure /setowner '*S-1-5-32-544' /T /C /Q | Out-Null
    Say ('حُصّنت ملكية المجلد المحمي: OWNER RIGHTS على ' + $dirs.Count + ' مجلدًا، والملكية للمسؤولين في الشجرة كلها.')
}

function Test-MicrosoftSigned([string]$Path) {
    # توقيع صالح من Microsoft Corporation، وجذر سلسلته في مخزن الجهاز لا مخزن المستخدم (المستخدم يستطيع زرع جذر في مخزنه هو)
    $sig = Get-AuthenticodeSignature -LiteralPath $Path
    if ($sig.Status -ne 'Valid' -or -not $sig.SignerCertificate) { return $false }
    if ($sig.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') { return $false }
    $chain = New-Object Security.Cryptography.X509Certificates.X509Chain
    $chain.ChainPolicy.RevocationMode = 'NoCheck'
    # توقيع مختوم زمنيًّا: Valid من WinVerifyTrust يعني أن الشهادة كانت سارية وقت الختم، فانتهاؤها بعده لا يُسقطه
    # (مكتبات Teams موقّعة بشهادة انتهت 20/8/2025). بلا ختم زمني تبقى الشهادة المنتهية مرفوضة.
    if ($sig.TimeStamperCertificate) { $chain.ChainPolicy.VerificationFlags = [Security.Cryptography.X509Certificates.X509VerificationFlags]::IgnoreNotTimeValid }
    if (-not $chain.Build($sig.SignerCertificate)) { return $false }
    $root = $chain.ChainElements[$chain.ChainElements.Count - 1].Certificate
    if ($root.Subject -notmatch 'Microsoft') { return $false }
    return (Test-Path -LiteralPath ('Cert:\LocalMachine\Root\' + $root.Thumbprint)) -or (Test-Path -LiteralPath ('Cert:\LocalMachine\AuthRoot\' + $root.Thumbprint))
}

function Install-ShellDir {
    # البند 1 من المجلس 19/11: نافذة التطبيق تعمل مرتفعة، فمكتباتها وملفا تعريف متصفحها وحالتها في مجلد للمسؤولين والنظام وحدهم
    # (لا قراءة للمستخدمين: ملف التعريف يحمل كعكة الجلسة). المكتبات تُنسخ من نسخة الجهاز (إضافة Teams أو النسخة المحلية القديمة)
    # ثم يُفحص توقيع النسخ المنسوخة نفسها قبل اعتمادها. غيابها لا يُفشل النشر: الغلاف يسقط إلى Edge بملف تعريف محمي.
    $shellDir = Join-Path $secure 'shell'
    if ((Test-Path -LiteralPath $shellDir) -and ((Get-Item -LiteralPath $shellDir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$shellDir وصلة لا مجلد؛ أزلها يدويًا ثم أعد النشر."
    }
    New-Item -ItemType Directory -Force -Path $shellDir | Out-Null
    if (Get-ChildItem -LiteralPath $shellDir -Force -ErrorAction SilentlyContinue) {
        & icacls $shellDir /setowner '*S-1-5-32-544' /T /C /Q | Out-Null
        & icacls (Join-Path $shellDir '*') /reset /T /C /Q | Out-Null   # المحتوى يرث من الجذر المحمي أدناه
    }
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetOwner($admins)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($s in @($admins, $system)) {
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($s, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
    }
    Set-Acl -LiteralPath $shellDir -AclObject $acl
    New-Item -ItemType Directory -Force -Path (Join-Path $shellDir 'profiles') | Out-Null

    $need = @('Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.Wpf.dll', 'WebView2Loader.dll')
    $sources = @()
    $teams = Join-Path $env:LOCALAPPDATA 'Microsoft\TeamsMeetingAdd-in'
    foreach ($d in (Get-ChildItem -LiteralPath $teams -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
        $x64 = Join-Path $d.FullName 'x64'
        $sources += , @{ (Join-Path $x64 $need[0]) = $need[0]; (Join-Path $x64 $need[1]) = $need[1]; (Join-Path $x64 'runtimes\win-x64\native\WebView2Loader.dll') = $need[2] }
    }
    $old = Join-Path $env:LOCALAPPDATA 'MonitorWidget\webview2'
    $sources += , @{ (Join-Path $old $need[0]) = $need[0]; (Join-Path $old $need[1]) = $need[1]; (Join-Path $old $need[2]) = $need[2] }
    $vendor = Join-Path $shellDir 'webview2'
    $stage = Join-Path $shellDir 'webview2.new'
    foreach ($set in $sources) {
        if (@($set.Keys | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) }).Count) { continue }
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path $stage | Out-Null
        foreach ($k in $set.Keys) { Copy-Item -LiteralPath $k -Destination (Join-Path $stage $set[$k]) -Force }
        if (@($need | Where-Object { -not (Test-MicrosoftSigned (Join-Path $stage $_)) }).Count) { Say ('مكتبات WebView2 في ' + (Split-Path (@($set.Keys)[0]) -Parent) + ' ليست موقّعة من Microsoft؛ تُتجاوز.'); continue }
        Remove-Item -LiteralPath $vendor -Recurse -Force -ErrorAction SilentlyContinue
        Move-Item -LiteralPath $stage -Destination $vendor
        Say ('زُرعت مكتبات WebView2 الموقّعة في المجلد المحمي ' + $vendor + '.')
        return
    }
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath (Join-Path $vendor $need[1])) { Say 'مكتبات WebView2 المحمية باقية كما هي (لا مصدر أحدث موقّع).' }
    else { Say 'تنبيه: لا مكتبات WebView2 موقّعة على الجهاز؛ نافذة التطبيق ستفتح في Edge بملف تعريف محمي.' }
}

$code = 1
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$runBackup = Join-Path $state ('tasks-' + $stamp)
$stopped = $false; $migrated = $false; $registered = $false
try {
    New-Item -ItemType Directory -Force -Path $state | Out-Null
    if ($Rollback) { Invoke-Rollback; $code = 0 }
    else {
        if (-not (Test-Path -LiteralPath (Join-Path $src '.git'))) { throw "$src ليس مستودع المرصد." }
        $commit = [string](Invoke-RepoGit @('rev-parse', 'HEAD'))
        if (-not (Test-RepoGit @('merge-base', '--is-ancestor', $commit, 'origin/main'))) { throw 'HEAD ليس ضمن origin/main: لا يُنشر إلا ما دُمج.' }
        Install-Python
        Set-PythonPath $py
        Save-TaskXml $runBackup
        if (-not (Test-Path -LiteralPath (Join-Path $state 'tasks-original'))) { Copy-Item -LiteralPath $runBackup -Destination (Join-Path $state 'tasks-original') -Recurse }
        Stop-Backend; $stopped = $true
        Install-Code $commit
        Install-ShellDir
        $migrated = Move-Data
        Set-Tokens $migrated
        Set-PolicyTasks
        Set-BackendTasks; $registered = $true
        Protect-SecureTree
        Start-AndVerify
        $code = 0
    }
}
catch {
    Say ('فشل: ' + $_.Exception.Message)
    if ($stopped -and -not $migrated -and -not (Test-Path -LiteralPath (Join-Path $state 'migrated.json'))) {
        try { Restore-TaskXml $runBackup; Start-ScheduledTask -TaskName 'MonitorWidget-Agent'; Start-ScheduledTask -TaskName 'MonitorWidget-Launch'; Say 'أُعيدت المهام كما كانت وشُغّلت.' }
        catch { Say ('تعذّر إرجاع المهام: ' + $_.Exception.Message) }
    }
    elseif ($stopped -and (Get-ServerPid) -eq 0) {
        # البيانات صارت في app: لا تُشغَّل المهام بتعريفها القديم (تنشئ قاعدة فارغة في مجلد المستودع)
        if (-not $registered) { try { Set-BackendTasks; $registered = $true } catch { Say ('تعذّر تسجيل المهام المحمية: ' + $_.Exception.Message) } }
        try { Start-ScheduledTask -TaskName 'MonitorWidget-Agent'; Start-ScheduledTask -TaskName 'MonitorWidget-Launch'; Say ('شُغّل الوكيل بتعريفه الحالي (المهام مسجّلة: ' + $registered + '). للتراجع: -Rollback') }
        catch { Say ('تعذّر تشغيل الوكيل: ' + $_.Exception.Message) }
    }
}
Set-Content -LiteralPath $resultFile -Value $log -Encoding UTF8
$log
exit $code
