# يعيد تشغيل الوكيل والخادم بصلاحية المسؤول ثم يفتح التطبيق. شغّله من نافذة مسؤول.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$port = 47832
# بعد النشر المحمي لا يُشغَّل الوكيل بمفسّر المستخدم أبدًا (يعمل مسؤولًا فيرث ما يكتبه أي برنامج عادي): المهمة وحدها
if (Test-Path -LiteralPath (Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MonitorWidget\app\.deployment\manifest.json')) {
    & (Join-Path $PSScriptRoot 'restart_agent.ps1')
    exit $LASTEXITCODE
}

$listen = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
$before = if ($listen) { $listen.OwningProcess } else { 0 }
$all = Get-CimInstance Win32_Process
# المسار الكامل لهذا المجلد فقط (كما في restart_agent.ps1): لا تُقتل عملية أخرى لمجرد أن سطر أمرها فيه MonitorWidget وserver.py
$ap = [regex]::Escape($root) + '[\\/](agent|server)\.py'
$isPy = { param($p) $p.Name -in 'python.exe', 'pythonw.exe' }
$parent = if ($listen) { ($all | Where-Object ProcessId -eq $listen.OwningProcess).ParentProcessId } else { 0 }
$victims = @($all | Where-Object {
    (& $isPy $_) -and (
        ($_.CommandLine -match $ap) -or
        ($listen -and $_.ProcessId -eq $listen.OwningProcess) -or
        ($parent -and $_.ProcessId -eq $parent))
})
$py = ($victims | Where-Object { $_.Name -eq 'pythonw.exe' } | Select-Object -First 1).ExecutablePath
if (-not $py) { $py = (Get-Command pythonw.exe).Source }
$victims | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

Start-Process -FilePath $py -ArgumentList ('"' + (Join-Path $root 'agent.py') + '"') -WorkingDirectory $root -WindowStyle Hidden   # مسار كامل كي تطابقه سكربتات الإيقاف

$token = (Get-Content -LiteralPath (Join-Path $root '.token') -Raw).Trim()
$h = @{ 'X-Token' = $token }
$up = $false
for ($i = 0; $i -lt 40 -and -not $up; $i++) {
    Start-Sleep -Milliseconds 750
    try { $up = (Invoke-RestMethod "http://127.0.0.1:$port/api/healthz" -Headers $h -TimeoutSec 3).ok } catch { }
}
if (-not $up) { throw 'الخادم لم يستجب بعد إعادة التشغيل؛ راجع monitor.log' }
# النجاح الحقيقي = عملية مستمعة جديدة؛ وإلا فالقديمة (المرتفعة غالبًا) نجت من Stop-Process وأجابت هي على healthz
$after = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
if ($before -and $after -eq $before) { throw "العملية القديمة $before ما زالت تستمع على المنفذ؛ لم تُستبدل (شغّل السكربت من نافذة مسؤول إن كانت مرتفعة)." }

# النسخة المؤقتة من الصفحة لم تعد لازمة بعد أن صار الخادم يقرأ web\index.html
$tmp = Join-Path $root 'index.html'
if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force }

$ticket = (Invoke-RestMethod "http://127.0.0.1:$port/api/ticket" -Method Post -Headers $h -ContentType 'application/json' -Body '{}').ticket
$shell = Join-Path $root 'app_shell.ps1'
# الغلاف يقرّر المكتبات وملف التعريف المحميين ويسقط بنفسه (البند 1 من المجلس 19/11)؛ لا فحص لمكتبات في مجلد المستخدم هنا
if (Test-Path -LiteralPath $shell) {
    # نافذة بلا إطار بحواف مستديرة مثل الويدجت
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', "`"$shell`"", '-Url', "`"http://127.0.0.1:$port/?k=$ticket`"", '-Replace'   # الجلسة القديمة ماتت مع الخادم: تُستبدل النافذة القائمة
} else {
    . (Join-Path $root 'shellpaths.ps1')
    $sp = Get-MwShellPaths
    $edge = Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Microsoft\Edge\Application\msedge.exe'
    if ($sp.Usable -and (Test-Path -LiteralPath $edge)) {
        New-Item -ItemType Directory -Force -Path $sp.EdgeProfile | Out-Null
        Start-Process $edge -ArgumentList "--app=http://127.0.0.1:$port/?k=$ticket", "--user-data-dir=$($sp.EdgeProfile)"
    } else { Start-Process "http://127.0.0.1:$port/?k=$ticket" }
}
'تم: الخادم يعمل والتطبيق مفتوح.'
