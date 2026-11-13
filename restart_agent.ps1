# إعادة تشغيل الوكيل والخادم (لتطبيق تعديلات الخلفية)، مع التحقق من أن عملية مستمعة جديدة حلّت محل القديمة.
# الخلفية قد تعمل مسؤولًا (set_backend_privilege.ps1 -Level Highest، قرار المالك 13/11/2026): الجلسة العادية لا تنهي عملياتها بـ Stop-Process،
# فنوقفها بمهامها أولًا؛ وإن نجت العملية القديمة يرفع السكربت نفسه بطلب UAC واحد بلا نافذة.
param([switch]$Elevated)
$ErrorActionPreference = 'Stop'
$r = $PSScriptRoot
$port = 47832
$tasks = @('MonitorWidget-Agent', 'MonitorWidget-Server-Logon')   # الثانية تشغّل server.py مباشرة عند الدخول
$ap = [regex]::Escape($r) + '[\\/](agent|server)\.py'   # [\\/] = شرطة عكسية أو أمامية؛ [\/] كانت تطابق الأمامية فقط
function Get-Listener { (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess }
function Get-Backend { @(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.Name -in 'python.exe', 'pythonw.exe' -and $_.CommandLine -match $ap }) }
try {
    $task = Get-ScheduledTask -TaskName 'MonitorWidget-Agent' -ErrorAction SilentlyContinue
    $before = Get-Listener
    foreach ($t in $tasks) { Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue }
    Get-Backend | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $alive = [bool]($before -and (Get-Listener) -eq $before)
    if ($alive -and -not $isAdmin -and -not $Elevated) {
        $exe = (Get-Process -Id $PID).Path
        try { $p = Start-Process $exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', ('"' + $PSCommandPath + '"'), '-Elevated' }
        catch { throw 'العملية القديمة مرتفعة ورُفض طلب صلاحية المسؤول؛ لم يُعَد تشغيل الخلفية.' }
        if ($p.ExitCode -ne 0) { throw ('فشلت إعادة تشغيل الخلفية المرتفعة (رمز الخروج ' + $p.ExitCode + ').') }
        'agent restarted (elevated)'
        return
    }
    if ($alive) { throw "العملية القديمة $before ما زالت تستمع؛ لم تُستبدل." }
    if ($task) { Start-ScheduledTask -TaskName 'MonitorWidget-Agent' }
    else { Start-Process pythonw.exe -ArgumentList ('"' + (Join-Path $r 'agent.py') + '"') -WindowStyle Hidden }
    $after = $null
    for ($i = 0; $i -lt 30 -and -not $after; $i++) { Start-Sleep -Milliseconds 500; $after = Get-Listener }
    if (-not $after) { throw 'لم يستمع خادم جديد على المنفذ خلال 15 ثانية؛ راجع monitor.log' }
    'agent restarted (pid ' + $after + ')'
}
catch {
    Write-Error $_.Exception.Message -ErrorAction Continue
    exit 1
}
