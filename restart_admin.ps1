# يعيد تشغيل الوكيل والخادم بصلاحية المسؤول ثم يفتح التطبيق. شغّله من نافذة مسؤول.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$port = 47832

$listen = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
$before = if ($listen) { $listen.OwningProcess } else { 0 }
$all = Get-CimInstance Win32_Process
$victims = @($all | Where-Object {
    ($_.CommandLine -match 'MonitorWidget' -and $_.CommandLine -match '(agent|server)\.py') -or
    ($listen -and $_.ProcessId -eq $listen.OwningProcess) -or
    ($listen -and $_.ProcessId -eq ($all | Where-Object ProcessId -eq $listen.OwningProcess).ParentProcessId -and $_.Name -match 'python')
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
$haveVend = (Test-Path (Join-Path $env:LOCALAPPDATA 'MonitorWidget\webview2\Microsoft.Web.WebView2.Wpf.dll')) -or
    (Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Microsoft\TeamsMeetingAdd-in') -Recurse -Filter 'Microsoft.Web.WebView2.Wpf.dll' -ErrorAction SilentlyContinue | Select-Object -First 1)
if ((Test-Path $shell) -and $haveVend) {
    # نافذة بلا إطار بحواف مستديرة مثل الويدجت
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-File', "`"$shell`"", '-Url', "`"http://127.0.0.1:$port/?k=$ticket`"", '-Replace'   # الجلسة القديمة ماتت مع الخادم: تُستبدل النافذة القائمة
} else {
    $edge = Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'
    $prof = Join-Path $env:LOCALAPPDATA 'MonitorWidget\appwin'
    New-Item -ItemType Directory -Force -Path $prof | Out-Null
    Start-Process $edge -ArgumentList "--app=http://127.0.0.1:$port/?k=$ticket", "--user-data-dir=$prof"
}
'تم: الخادم يعمل والتطبيق مفتوح.'
