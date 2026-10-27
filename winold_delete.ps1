# winold_delete.ps1 — حذف C:\Windows.old من مهمة مجدولة مرتفعة ثابتة فقط (MonitorWidget-WinOld / MonitorWidget-WinOld-Early).
# يُنسخ إلى المجلد المحمي بواسطة install_secure_*.ps1 ويعمل من هناك؛ الخادم (بصلاحية عادية) يطلقه بـ schtasks /run ويقرأ ملف النتيجة.
# القائمة البيضاء: مسار واحد ثابت، لا وسيطة مسار، لا تتبّع لروابط، وبوابة 14 يومًا تُفرض هنا أيضًا (Early يتجاوزها، وهي مهمة منفصلة).
param([switch]$Early, [string]$Result = 'C:\ProgramData\MonitorWidget\out\winold_result.json')
$ErrorActionPreference = 'Stop'
$Target = 'C:\Windows.old'
$MinDays = 14
$t0 = Get-Date
$o = [ordered]@{ ts = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); early = [bool]$Early; state = 'error'; ok = $false; left = $false; mb = 0; freeBeforeGB = 0; freeAfterGB = 0; error = $null }
function Save { $o | ConvertTo-Json -Compress | Set-Content -LiteralPath $Result -Encoding UTF8 }
try {
    $id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'يلزم تشغيل مرتفع (المهمة المجدولة)' }
    if (-not (Test-Path -LiteralPath $Target -PathType Container)) { $o.state = 'refused'; $o.error = 'غير موجود'; Save; exit 0 }
    $item = Get-Item -LiteralPath $Target -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { $o.state = 'refused'; $o.error = 'المسار غير آمن (رابط)'; Save; exit 0 }
    $age = ((Get-Date) - $item.CreationTime).TotalDays
    if ($age -lt $MinDays -and -not $Early) { $o.state = 'refused'; $o.error = ('لم تمضِ {0} يومًا بعد' -f $MinDays); Save; exit 0 }
    $o.state = 'running'; Save
    $o.freeBeforeGB = [math]::Round((Get-PSDrive C).Free / 1GB, 2)
    & takeown.exe /F $Target /R /D Y 2>&1 | Out-Null
    & icacls.exe $Target /grant '*S-1-5-32-544:(OI)(CI)F' /T /C /Q /L 2>&1 | Out-Null
    & cmd.exe /c rmdir /s /q $Target 2>&1 | Out-Null
    $o.left = Test-Path -LiteralPath $Target -PathType Container
    $o.freeAfterGB = [math]::Round((Get-PSDrive C).Free / 1GB, 2)
    $o.mb = [math]::Round([math]::Max(0, ($o.freeAfterGB - $o.freeBeforeGB) * 1024), 1)
    $o.ok = -not $o.left
    $o.state = if ($o.left) { 'partial' } else { 'done' }
    if ($o.left) { $o.error = 'بقي جزء' }
} catch {
    $msg = '' + $_.Exception.Message
    $o.state = 'error'; $o.error = $msg.Substring(0, [Math]::Min(120, $msg.Length))
}
$o.secs = [int]((Get-Date) - $t0).TotalSeconds
Save
