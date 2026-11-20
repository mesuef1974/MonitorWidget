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
$t0 = Get-Date
$o = [ordered]@{ ts = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); kind = $Kind; state = 'error'; ok = $false; mb = 0; files = 0; skipped = 0; freeBeforeGB = 0; freeAfterGB = 0; error = $null }
function Save { $o | ConvertTo-Json -Compress | Set-Content -LiteralPath $Result -Encoding UTF8 }
function FreeGB { [math]::Round((Get-PSDrive C).Free / 1GB, 2) }
# swdist بعتبة 0 يوم: حذف مجلد التنزيل أثناء تحديث جارٍ أو قبل إعادة تشغيل معلّقة يقطع التحديث (البند 17 من المجلس 19/11).
# يُرفض التنظيف إن وُجد أحد مفتاحي إعادة التشغيل المعلّقة، أو عملية من عمليات خدمة التحديث والتثبيت قيد العمل.
function UpdateBusy {
    $keys = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired',
              'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
    foreach ($k in $keys) { if (Test-Path -LiteralPath $k) { return 'إعادة تشغيل معلّقة لتحديث ويندوز' } }
    $p = @(Get-Process -Name 'TiWorker', 'TrustedInstaller', 'MoUsoCoreWorker', 'wuauclt' -ErrorAction SilentlyContinue)
    if ($p.Count -gt 0) { return ('تحديث ويندوز يعمل الآن ({0})' -f (($p.Name | Sort-Object -Unique) -join '، ')) }
    return $null
}
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
    } elseif ($Kind -eq 'swdist' -and ($busy = UpdateBusy)) {
        $o.ok = $false; $o.error = $busy + '؛ لم يُحذف شيء'
    } else {
        # الحذف بالمقبض (MwSafeDel، نظير safedel.py): Windows\Temp يكتب فيه أي مستخدم، فمجلد يُحوَّل junction بعد السرد كان
        # يوجّه Remove-Item المرتفع إلى System32. pwsh 7 يترجم Add-Type في الذاكرة فلا ملفات ترجمة في مجلد قابل للكتابة.
        Add-Type -Path (Join-Path $PSScriptRoot 'mwsafedel.cs')
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
