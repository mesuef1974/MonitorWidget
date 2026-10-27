# elevated_clean.ps1 — تنظيفات تحتاج المسؤول من مهام مجدولة مرتفعة ثابتة فقط (MonitorWidget-Clean-WinTemp / -SwDist / -Dism).
# يُنسخ إلى المجلد المحمي بواسطة install_secure_*.ps1 ويعمل من هناك؛ الخادم (بصلاحية عادية) يطلق المهمة بـ schtasks /run ويقرأ ملف النتيجة.
# القائمة البيضاء: النوع من ثلاثة فقط، المسارات ثابتة هنا، لا وسيطة مسار من الخادم، لا تتبّع لروابط، ولا /ResetBase في DISM.
param([Parameter(Mandatory)][ValidateSet('wintemp', 'swdist', 'dism')][string]$Kind,
      [string]$Result = 'C:\ProgramData\MonitorWidget\out\clean_result.json')
$ErrorActionPreference = 'Stop'
$Roots = @{ wintemp = @('C:\Windows\Temp'); swdist = @('C:\Windows\SoftwareDistribution\Download') }
$MinDays = @{ wintemp = 3; swdist = 0 }
$t0 = Get-Date
$o = [ordered]@{ ts = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); kind = $Kind; state = 'error'; ok = $false; mb = 0; files = 0; skipped = 0; freeBeforeGB = 0; freeAfterGB = 0; error = $null }
function Save { $o | ConvertTo-Json -Compress | Set-Content -LiteralPath $Result -Encoding UTF8 }
function FreeGB { [math]::Round((Get-PSDrive C).Free / 1GB, 2) }
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
    } else {
        $cut = (Get-Date).AddDays(-$MinDays[$Kind])
        foreach ($root in $Roots[$Kind]) {
            if (-not (Test-Path -LiteralPath $root -PathType Container)) { continue }
            $item = Get-Item -LiteralPath $root -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { $o.skipped++; continue }
            # الملفات أولًا (الأقدم من الحد)، ثم المجلدات الفارغة من الأعمق؛ ما هو قيد الاستخدام يُترك ويُعدّ متروكًا
            $files = @(Get-ChildItem -LiteralPath $root -Recurse -Force -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -lt $cut -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) })
            foreach ($f in $files) {
                try { $sz = $f.Length; Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop; $o.files++; $o.mb += $sz / 1MB } catch { $o.skipped++ }
            }
            $dirs = @(Get-ChildItem -LiteralPath $root -Recurse -Force -Directory -ErrorAction SilentlyContinue | Sort-Object { $_.FullName.Length } -Descending)
            foreach ($d in $dirs) {
                if ($d.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
                try { if (-not (Get-ChildItem -LiteralPath $d.FullName -Force -ErrorAction Stop | Select-Object -First 1)) { Remove-Item -LiteralPath $d.FullName -Force -ErrorAction Stop } } catch { }
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
