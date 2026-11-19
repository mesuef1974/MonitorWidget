# إعادة تشغيل الويدجت فقط (لا الوكيل ولا الخادم)، مع التحقق من أن عملية جديدة ظهرت فعلًا.
# الويدجت يعمل مسؤولًا عبر المهمة MonitorWidget-Launch: الجلسة العادية لا ترى سطر أمره ولا تستطيع إنهاءه،
# فكانت النسخة السابقة تطبع «widget restarted» دون أن تفعل شيئًا. الآن يرفع السكربت نفسه (طلب UAC واحد، بلا نافذة).
$ErrorActionPreference = 'Stop'
$r = $PSScriptRoot
$live = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MonitorWidget\app'
if (Test-Path -LiteralPath (Join-Path $live '.deployment\manifest.json')) { $r = $live }   # النشر المحمي: الويدجت يعمل من هناك
$wp = [regex]::Escape((Join-Path $r 'widget.ps1'))
function Get-WidgetProcess {
    Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.Name -in 'powershell.exe', 'pwsh.exe' -and $_.CommandLine -match $wp }
}
try {
    $task = Get-ScheduledTask -TaskName 'MonitorWidget-Launch' -ErrorAction SilentlyContinue
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($task -and $task.Principal.RunLevel -eq 'Highest' -and -not $isAdmin) {
        $exe = (Get-Process -Id $PID).Path
        try { $p = Start-Process $exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList '-NoProfile', '-WindowStyle', 'Hidden', '-File', ('"' + $PSCommandPath + '"') }
        catch { throw 'رُفض طلب صلاحية المسؤول؛ لم يُعَد تشغيل الويدجت.' }
        if ($p.ExitCode -ne 0) { throw ('فشلت إعادة تشغيل الويدجت المرتفع (رمز الخروج ' + $p.ExitCode + ').') }
        'widget restarted (elevated)'
        return
    }
    $old = @(Get-WidgetProcess)
    $oldIds = @($old | ForEach-Object { $_.ProcessId })
    $stuck = @()
    foreach ($o in $old) { try { Stop-Process -Id $o.ProcessId -Force -ErrorAction Stop } catch { $stuck += $o.ProcessId } }
    if ($stuck.Count) { throw ('تعذّر إنهاء الويدجت ' + ($stuck -join ', ') + ' (يعمل مسؤولًا؟ شغّل هذا السكربت من طرفية مسؤول).') }
    Start-Sleep 1
    if ($task) { Start-ScheduledTask -TaskName 'MonitorWidget-Launch' }
    else { Start-Process wscript.exe -WindowStyle Hidden -ArgumentList ('"' + (Join-Path $r 'launch_mini.vbs') + '"') }
    $new = $null
    for ($i = 0; $i -lt 30 -and -not $new; $i++) {
        Start-Sleep -Milliseconds 500
        $new = Get-WidgetProcess | Where-Object { $_.ProcessId -notin $oldIds } | Select-Object -First 1
    }
    if (-not $new) { throw 'لم تظهر عملية ويدجت جديدة خلال 15 ثانية؛ لم يُعَد التشغيل.' }
    'widget restarted (pid ' + $new.ProcessId + ')'
}
catch {
    Write-Error $_.Exception.Message -ErrorAction Continue
    exit 1
}
