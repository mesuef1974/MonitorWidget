# gpu_dwm_restart.ps1 — إعادة تشغيل مدير نوافذ سطح المكتب (dwm) من المهمة المرتفعة الثابتة MonitorWidget-Gpu-Dwm فقط.
# يُنسخ إلى المجلد المحمي بواسطة install_secure_*.ps1؛ الخادم (بصلاحية عادية) يطلق المهمة بـ schtasks /run ويقرأ ملف النتيجة.
# الغرض: تحرير ذاكرة الرسوميات التي يحجزها dwm (تسرّب تعريف إنتل المعروف). Windows يعيد تشغيل dwm فورًا:
# تومض الشاشة لحظة ولا يُغلق أي برنامج. لا وسيطة من الخادم، ولا يُعاد التشغيل مرتين خلال دقيقتين.
param([string]$Result = 'C:\ProgramData\MonitorWidget\out\clean_dwm_result.json')
$ErrorActionPreference = 'Stop'
$MinGapS = 120
$t0 = Get-Date
$o = [ordered]@{ ts = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); kind = 'dwm'; state = 'error'; ok = $false; mb = 0; beforeMB = $null; afterMB = $null; oldPid = $null; newPid = $null; lastDone = $null; error = $null }
function Save { $o | ConvertTo-Json -Compress | Set-Content -LiteralPath $Result -Encoding UTF8 }
function SharedMB([int]$procId) {
    $s = (Get-Counter ("\GPU Process Memory(pid_{0}_*)\Shared Usage" -f $procId) -ErrorAction SilentlyContinue).CounterSamples
    if (-not $s) { return $null }
    [math]::Round(($s | Measure-Object CookedValue -Sum).Sum / 1MB, 0)
}
function SessionDwm([int]$session) { Get-Process -Name dwm -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq $session } | Select-Object -First 1 }
try {
    $id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'يلزم تشغيل مرتفع (المهمة المجدولة)' }
    $prev = $null
    if (Test-Path -LiteralPath $Result) { try { $prev = Get-Content -LiteralPath $Result -Raw | ConvertFrom-Json } catch { $prev = $null } }
    # آخر إعادة ناجحة تُحمل من نتيجة إلى التالية، فالرفض لا يمحو أثرها
    if ($prev -and $prev.lastDone) { $o.lastDone = [int]$prev.lastDone }
    if ($o.lastDone -and ($o.ts - $o.lastDone) -lt $MinGapS) { throw 'أُعيد تشغيل مدير سطح المكتب قبل أقل من دقيقتين؛ انتظر قليلًا' }
    $o.state = 'running'; Save
    # dwm جلسة المستخدم نفسها (المهمة تعمل تفاعليًا باسمه)، لا جلسات أخرى
    $session = (Get-Process -Id $PID).SessionId
    $old = SessionDwm $session
    if (-not $old) { throw 'لم يُعثر على dwm في جلسة المستخدم' }
    $o.oldPid = $old.Id; $o.beforeMB = SharedMB $old.Id
    Stop-Process -Id $old.Id -Force
    $deadline = (Get-Date).AddSeconds(20); $new = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $new = SessionDwm $session
        if ($new -and $new.Id -ne $old.Id) { break }
        $new = $null
    }
    if (-not $new) { throw 'لم يعد dwm خلال 20 ث؛ يعيده Windows تلقائيًا عادةً' }
    $o.newPid = $new.Id
    Start-Sleep -Seconds 3
    $o.afterMB = SharedMB $new.Id
    if ($null -ne $o.beforeMB -and $null -ne $o.afterMB) { $o.mb = [math]::Max(0, $o.beforeMB - $o.afterMB) }
    $o.ok = $true; $o.state = 'done'; $o.lastDone = $o.ts
} catch {
    $msg = '' + $_.Exception.Message
    $o.state = 'error'; $o.ok = $false; $o.error = $msg.Substring(0, [Math]::Min(120, $msg.Length))
}
$o.secs = [int]((Get-Date) - $t0).TotalSeconds
Save
