# إعادة تشغيل الوكيل والخادم (لتطبيق تعديلات الخلفية)، مع التحقق من أن عملية مستمعة جديدة حلّت محل القديمة.
# الخلفية قد تعمل مسؤولًا (set_backend_privilege.ps1 -Level Highest، قرار المالك 13/11/2026): الجلسة العادية لا تنهي عملياتها بـ Stop-Process،
# فنوقفها بمهامها أولًا؛ وإن نجت العملية القديمة يرفع السكربت نفسه بطلب UAC واحد بلا نافذة.
param([switch]$Elevated)
$ErrorActionPreference = 'Stop'
$r = $PSScriptRoot
$port = 47832
$tasks = @('MonitorWidget-Agent', 'MonitorWidget-Server-Logon')   # الثانية تشغّل server.py مباشرة عند الدخول
$ap = [regex]::Escape($r) + '[\\/](agent|server)\.py'   # [\\/] = شرطة عكسية أو أمامية؛ الصنف بشرطة عكسية مفردة كان يطابق الأمامية فقط
function Get-ServerPid {
    # رقم عملية الخادم من /api/healthz (بلا مصادقة). Get-NetTCPConnection يعيد 0 لمالك مرتفع حين تسأله جلسة عادية.
    try { [int](Invoke-RestMethod "http://127.0.0.1:$port/api/healthz" -TimeoutSec 3).pid }
    catch { if ($_.Exception.Response) { -1 } else { 0 } }   # -1: حيّ لكنه أجاب بخطأ؛ 0: لا خادم
}
function Get-Backend { @(Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.Name -in 'python.exe', 'pythonw.exe' -and $_.CommandLine -match $ap }) }
try {
    $task = Get-ScheduledTask -TaskName 'MonitorWidget-Agent' -ErrorAction SilentlyContinue
    $before = Get-ServerPid
    foreach ($t in $tasks) { Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue }
    Get-Backend | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $alive = [bool]($before -gt 0 -and (Get-ServerPid) -eq $before)
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
    $after = 0
    for ($i = 0; $i -lt 40 -and $after -le 0; $i++) { Start-Sleep -Milliseconds 500; $after = Get-ServerPid }
    if ($after -le 0) { throw 'لم يُجب خادم جديد خلال 20 ثانية؛ راجع monitor.log' }
    if ($after -eq $before) { throw "العملية القديمة $before ما زالت تجيب؛ لم تُستبدل." }
    'agent restarted (pid ' + $after + ')'
}
catch {
    Write-Error $_.Exception.Message -ErrorAction Continue
    exit 1
}
