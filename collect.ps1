param([string]$Root)
# Read-only collector: prints one JSON document describing machine state, scheduled tasks, cleanup rules and recent logs.
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$u    = $env:USERPROFILE
$days = @('الأحد','الاثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت')

function Iso($d) { if ($d -and ([datetime]$d).Year -ge 2000) { ([datetime]$d).ToString('s') } else { $null } }
function RepText($r) {
    if ($r -and $r.Interval) {
        $ts = [Xml.XmlConvert]::ToTimeSpan($r.Interval)
        if ($ts.TotalMinutes -lt 120) { return "كل $([int]$ts.TotalMinutes) د" }
        return "كل $([math]::Round($ts.TotalHours,1)) س"
    }
}
function TrigText($t) {
    $c  = $t.CimClass.CimClassName
    $at = ''
    if ($t.StartBoundary) { $at = ([datetime]$t.StartBoundary).ToString('HH:mm') }
    switch ($c) {
        'MSFT_TaskDailyTrigger'   { return "يومياً $at" }
        'MSFT_TaskWeeklyTrigger'  {
            $d = @(); for ($i = 0; $i -lt 7; $i++) { if ([int]$t.DaysOfWeek -band (1 -shl $i)) { $d += $days[$i] } }
            return "أسبوعياً ($($d -join '، ')) $at"
        }
        'MSFT_TaskMonthlyTrigger' {
            $m = ([Convert]::ToString([int]$t.MonthsOfYear, 2).ToCharArray() | Where-Object { $_ -eq '1' }).Count
            if ($m -ge 12) { return "شهرياً $at" } else { return "$m مرات سنوياً $at" }
        }
        'MSFT_TaskLogonTrigger'   { return 'عند الدخول' }
        'MSFT_TaskBootTrigger'    { return 'عند الإقلاع' }
        'MSFT_TaskTimeTrigger'    { $r = RepText $t.Repetition; if ($r) { return "$r (من $at)" } else { return "مرة $at" } }
        default {
            if ($null -ne $t.MonthsOfYear) {
                $m = ([Convert]::ToString([int]$t.MonthsOfYear, 2).ToCharArray() | Where-Object { $_ -eq '1' }).Count
                if ($m -ge 12) { return "شهرياً $at" } else { return "$m مرات سنوياً $at" }
            }
            if ($t.StartBoundary) { return 'تقويمي (يبدأ ' + ([datetime]$t.StartBoundary).ToString('yyyy-MM-dd HH:mm') + ')' }
            return $c
        }
    }
}

# ---- scheduled tasks (whitelist from tasks.json)
$wl = Get-Content "$Root\tasks.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$allTasks = @{}
foreach ($x in (Get-ScheduledTask)) { $allTasks["$($x.TaskPath)$($x.TaskName)"] = $x }
$tasks = foreach ($w in $wl) {
    $t = $allTasks["$($w.path)$($w.name)"]
    if (-not $t) {
        [pscustomobject]@{ path = $w.path; name = $w.name; label = $w.label; group = $w.group; canRun = $w.canRun; needsAdmin = [bool]$w.needsAdmin; missing = $true }
        continue
    }
    $i   = Get-ScheduledTaskInfo -InputObject $t
    $off = ($t.State -eq 'Disabled')
    [pscustomobject]@{
        path = $w.path; name = $w.name; label = $w.label; group = $w.group; canRun = $w.canRun; needsAdmin = [bool]$w.needsAdmin; missing = $false
        state = [string]$t.State; enabled = (-not $off)
        schedule = (@($t.Triggers | ForEach-Object { TrigText $_ }) -join ' · ')
        last = (Iso $i.LastRunTime); next = $(if ($off) { $null } else { Iso $i.NextRunTime }); result = [int64]$i.LastTaskResult
    }
}

# ---- machine metrics
$mem  = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
$os   = Get-CimInstance Win32_OperatingSystem
$proc = Get-Process
$cl   = @($proc | Where-Object { $_.ProcessName -eq 'claude' })
$wsl  = $proc | Where-Object { $_.ProcessName -eq 'vmmemWSL' } | Select-Object -First 1
$cdr  = Get-PSDrive C
$metrics = [pscustomobject]@{
    commitPct = [math]::Round(100 * $mem.CommittedBytes / $mem.CommitLimit, 1)
    commitGB  = [math]::Round($mem.CommittedBytes / 1GB, 1)
    limitGB   = [math]::Round($mem.CommitLimit / 1GB, 1)
    availGB   = [math]::Round($mem.AvailableMBytes / 1024, 2)
    totalGB   = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    diskFreeGB = [math]::Round($cdr.Free / 1GB, 1)
    diskTotalGB = [math]::Round(($cdr.Used + $cdr.Free) / 1GB, 1)
    claudeProcs = $cl.Count
    claudeGB  = [math]::Round((($cl | Measure-Object PrivateMemorySize64 -Sum).Sum) / 1GB, 1)
    wslGB     = $(if ($wsl) { [math]::Round($wsl.WorkingSet64 / 1GB, 1) } else { 0 })
    shells    = @($proc | Where-Object { $_.ProcessName -in 'pwsh','powershell' }).Count
    conhost   = @($proc | Where-Object { $_.ProcessName -eq 'conhost' }).Count
    uptimeH   = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours, 1)
}

# ---- cleanup policy + last real result per rule
$polPath = "$u\Documents\Scripts\Cleanup\v2\cleanup-policy.json"
$rules = @()
$pol = Get-Content $polPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($pol) {
    $rules = @($pol.rules | ForEach-Object {
        [pscustomobject]@{ id = $_.id; tier = $_.tier; enabled = [bool]$_.enabled; action = $_.action; minFreeGB = $_.minFreeGB; description = $_.description }
    })
}
$ruleLast = @{}
$evPath = "$u\Documents\Scripts\Cleanup\logs\cleanup_events.jsonl"
if (Test-Path $evPath) {
    foreach ($ln in (Get-Content $evPath -Tail 4000 -Encoding UTF8)) {
        try { $e = $ln | ConvertFrom-Json } catch { continue }
        if ($e.dryRun -eq $false) { $ruleLast[$e.rule] = @{ ts = $e.ts; status = $e.status; bytes = $e.bytes; detail = [string]$e.detail } }
    }
}
$runs = @()
$runPath = "$u\Documents\Scripts\Cleanup\logs\runs.jsonl"
if (Test-Path $runPath) {
    $runs = @(Get-Content $runPath -Tail 14 -Encoding UTF8 | ForEach-Object { try { $_ | ConvertFrom-Json } catch {} } | Sort-Object Started -Descending | Select-Object -First 10)
}

# ---- logs
$mcLog = "$u\Documents\Scripts\MachineCare\logs\machinecare.log"
$mcLines = @()
if (Test-Path $mcLog) {
    $mcLines = @(Get-Content $mcLog -Tail 400 -Encoding UTF8 | Where-Object { $_ -match 'WARN|BURST|KILLED|WOULD-KILL|ALERT' } | Select-Object -Last 14)
}
$mgLog = "$u\.claude\logs\memory_guard.log"
$mgLines = @(); if (Test-Path $mgLog) { $mgLines = @(Get-Content $mgLog -Tail 6 -Encoding UTF8) }

# ---- memory guard mode (task arguments + last log line)
$gt = $allTasks["\SchoolOS-MemoryGuard"]
$guardApply = $false
if ($gt -and $gt.Actions.Count) { $guardApply = [bool]($gt.Actions[0].Arguments -match '-Apply') }
$guardLast = $null
if ($mgLines.Count -and ($mgLines[-1] -match '\[(APPLY|DRYRUN)\]')) { $guardLast = $Matches[1] }

# ---- machinecare config
$cfg = $null
$cfgPath = "$u\Documents\Scripts\MachineCare\machinecare.config.json"
if (Test-Path $cfgPath) { $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json }

[pscustomobject]@{
    now = (Get-Date).ToString('s'); metrics = $metrics; tasks = @($tasks); rules = $rules; ruleLast = $ruleLast
    runs = $runs; mcLog = $mcLines; mgLog = $mgLines
    guard = [pscustomobject]@{ applyInArgs = $guardApply; lastMode = $guardLast }
    reaper = $(if ($cfg) { $cfg.reaper } else { $null }); health = $(if ($cfg) { $cfg.health } else { $null })
} | ConvertTo-Json -Depth 7 -Compress
