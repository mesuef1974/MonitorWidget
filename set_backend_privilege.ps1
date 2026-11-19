# صلاحية الخلفية كلها بأمر واحد: مهام الخادم (MonitorWidget-Server-Logon) والوكيل (MonitorWidget-Agent) والمجمّع (MonitorWidget-Collector).
#   -Level Highest : تعمل الثلاث بصلاحية مسؤول دائمًا (قرار المالك 13/11/2026، بدّل قرار 27/10/2026).
#   -Level Limited : تعود الثلاث إلى الصلاحية العادية.
# يغيّر مستوى التشغيل وحده: الأمر والوسائط والمشغّلات والإعدادات تبقى كما هي (يُعدَّل XML المهمة نفسه)، وتُحفظ نسخة منه في backups قبل التعديل.
# يرفع نفسه بطلب UAC واحد بلا نافذة، ويعمل في Windows PowerShell 5.1 وفي pwsh. يشغّله المالك؛ لا تشغّله جلسة نموذج.
# تنبيه أمني مقصود ومعلوم: ملفات هذا المجلد يكتبها المستخدم العادي، فأي برنامج بصلاحية عادية يعدّلها يعمل تعديله مسؤولًا عند التشغيل التالي.
param([ValidateSet('Highest', 'Limited')][string]$Level = 'Highest')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
# بعد النشر المحمي (deploy_protected.ps1) البيانات في مجلد لا يكتبه إلا المسؤولون: خلفية بصلاحية عادية لا تكتب قاعدتها.
if (Test-Path -LiteralPath (Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MonitorWidget\app\.deployment\manifest.json')) {
    Write-Error 'النشر المحمي مفعّل: الخلفية مسؤول دائمًا من ProgramData. للعودة إلى الوضع القديم: deploy_protected.ps1 -Rollback' -ErrorAction Continue
    exit 1
}
$port = 47832
$names = @('MonitorWidget-Server-Logon', 'MonitorWidget-Agent', 'MonitorWidget-Collector')
$resultFile = Join-Path $root 'backups\backend_privilege.result.txt'

function Set-TaskRunLevelXml([string]$Xml, [string]$Level) {
    # يعيد XML المهمة بمستوى التشغيل المطلوب دون مسّ غيره. المبدأ بلا RunLevel يعني LeastPrivilege.
    $doc = New-Object System.Xml.XmlDocument
    $doc.PreserveWhitespace = $true
    $doc.LoadXml($Xml)
    $ns = $doc.DocumentElement.NamespaceURI
    $m = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
    $m.AddNamespace('t', $ns)
    $principals = @($doc.SelectNodes('/t:Task/t:Principals/t:Principal', $m))
    if ($principals.Count -ne 1) { throw 'تعريف المهمة بلا مبدأ واحد واضح؛ لم يُعدَّل.' }
    $want = if ($Level -eq 'Highest') { 'HighestAvailable' } else { 'LeastPrivilege' }
    $node = $principals[0].SelectSingleNode('t:RunLevel', $m)
    if (-not $node) { $node = $principals[0].AppendChild($doc.CreateElement('RunLevel', $ns)) }
    $node.InnerText = $want
    $doc.OuterXml
}

function Get-ServerPid {
    # رقم عملية الخادم من /api/healthz (بلا مصادقة). Get-NetTCPConnection يعيد 0 لمالك مرتفع حين تسأله جلسة عادية.
    try { [int](Invoke-RestMethod "http://127.0.0.1:$port/api/healthz" -TimeoutSec 3).pid }
    catch { if ($_.Exception.Response) { -1 } else { 0 } }   # -1: حيّ لكنه أجاب بخطأ؛ 0: لا خادم
}

function Get-BackendProcess([string]$Root, $All) {
    # الوكيل والخادم والمجمّع من هذا المجلد، أو agent.py عاريًا (كان restart_admin.ps1 القديم يطلقه بلا مسار).
    $ap = '(' + [regex]::Escape($Root) + '[\\/](agent|server|collector)\.py)|(^(\S+|"[^"]+")\s+"?agent\.py"?\s*$)'
    @($All | Where-Object { $_.Name -in 'python.exe', 'pythonw.exe' -and $_.CommandLine -match $ap })
}

$id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $resultFile) | Out-Null
    Remove-Item -LiteralPath $resultFile -Force -ErrorAction SilentlyContinue
    $exe = (Get-Process -Id $PID).Path
    try { $p = Start-Process $exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', ('"' + $PSCommandPath + '"'), '-Level', $Level }
    catch { Write-Error 'رُفض طلب صلاحية المسؤول؛ لم يتغيّر شيء.' -ErrorAction Continue; exit 1 }
    if (Test-Path -LiteralPath $resultFile) { Get-Content -LiteralPath $resultFile -Encoding UTF8 }
    exit $p.ExitCode
}

$log = New-Object System.Collections.Generic.List[string]
try {
    New-Item -ItemType Directory -Force -Path (Join-Path $root 'backups') | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $plan = foreach ($n in $names) {
        $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
        if (-not $t) { $log.Add("$n : غير مسجّلة؛ تُركت."); continue }
        $xml = Export-ScheduledTask -TaskName $n
        Set-Content -LiteralPath (Join-Path $root ("backups\{0}.{1}.before_{2}.xml" -f $n, $stamp, $Level.ToLower())) -Value $xml -Encoding Unicode
        [pscustomobject]@{ name = $n; xml = (Set-TaskRunLevelXml $xml $Level); was = [string]$t.Principal.RunLevel }
    }
    if (-not @($plan).Count) { throw 'لا مهمة من الثلاث مسجّلة.' }

    # التسجيل أولًا (لا يمسّ العمليات الجارية): الويدجت يشغّل مهمة الوكيل فور سقوط الخادم، فيجب أن يجد التعريف الجديد لا القديم.
    foreach ($p in $plan) {
        Register-ScheduledTask -TaskName $p.name -Xml $p.xml -Force | Out-Null
        $log.Add(("{0} : {1} -> {2}" -f $p.name, $p.was, (Get-ScheduledTask -TaskName $p.name).Principal.RunLevel))
    }
    $before = Get-ServerPid
    foreach ($n in $names) { Stop-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue }
    $all = Get-CimInstance Win32_Process
    $victims = @(Get-BackendProcess $root $all)
    if ($before -gt 0) { $victims += @($all | Where-Object { $_.ProcessId -eq $before -and $_.Name -match '^pythonw?\.exe$' }) }
    $victims | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 1
    # ما وُلد بعد القتل شغّله الويدجت بالتعريف الجديد فيُترك؛ ما بقي من قبل القتل عملية قديمة عالقة
    $old = @($victims | ForEach-Object ProcessId)
    $left = @(Get-BackendProcess $root (Get-CimInstance Win32_Process) | Where-Object { $_.ProcessId -in $old })
    if ($left.Count) { throw ('بقيت عملية خلفية قديمة حيّة: ' + (($left | ForEach-Object ProcessId) -join ', ')) }
    # IgnoreNew: إن سبقنا الويدجت إلى تشغيل المهمة فهذا التشغيل يُرفض بلا ضرر
    if ($plan | Where-Object name -eq 'MonitorWidget-Agent') { Start-ScheduledTask -TaskName 'MonitorWidget-Agent' -ErrorAction SilentlyContinue }
    else { Start-ScheduledTask -TaskName @($plan)[0].name -ErrorAction SilentlyContinue }

    $tok = (Get-Content -LiteralPath (Join-Path $root '.token') | Select-Object -First 1)
    $m = $null
    for ($i = 0; $i -lt 40 -and -not $m; $i++) {
        Start-Sleep 1
        try { $m = Invoke-RestMethod "http://127.0.0.1:$port/api/mini" -Headers @{ 'X-Token' = $tok } -TimeoutSec 3 } catch {}
    }
    if (-not $m) { throw 'لم يستجب الخادم خلال 40 ثانية؛ راجع monitor.log' }
    $after = Get-ServerPid
    if ($before -gt 0 -and $after -eq $before) { throw "العملية القديمة $before ما زالت تستمع؛ لم تُستبدل." }
    if ([bool]$m.admin -ne ($Level -eq 'Highest')) { throw ("الخادم الجديد admin=" + $m.admin + " والمطلوب " + $Level + '؛ راجع مبدأ المهام.') }
    $log.Add("تم: الخادم يعمل (PID $after)، admin=" + $m.admin)
    $code = 0
}
catch {
    $log.Add('فشل: ' + $_.Exception.Message)
    # لا تُترك الخلفية ميتة: إن أُوقفت ولم يعد خادم يستمع فأعد تشغيل الوكيل بتعريفه الحالي
    if ((Get-ServerPid) -eq 0) {
        try { Start-ScheduledTask -TaskName 'MonitorWidget-Agent' -ErrorAction Stop; $log.Add('أُعيد تشغيل الوكيل بتعريفه الحالي.') } catch { $log.Add('تعذّر إعادة تشغيل الوكيل: ' + $_.Exception.Message) }
    }
    $code = 1
}
Set-Content -LiteralPath $resultFile -Value $log -Encoding UTF8
$log
exit $code
