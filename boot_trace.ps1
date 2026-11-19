# Boot trace v2: samples CPU, disk, memory and top processes every ~5 s for about 4 minutes after startup.
# Runs as SYSTEM at boot (before logon). Output: logs\boot_trace.csv (previous run kept as boot_trace_prev.csv).
# v2: names which services live inside each svchost, and shows the command line of pwsh/python/node/WMI hosts.
# -OutDir: مجلد المخرجات. المهمة المسجّلة (register_boot_trace.ps1) تشغّل النسخة المحمية في ProgramData وتمرّر مجلد out المحمي.
param([string]$OutDir)
$ErrorActionPreference = 'SilentlyContinue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$logs = if ($OutDir) { $OutDir } else { Join-Path $dir 'logs' }
if (-not (Test-Path $logs)) { New-Item -ItemType Directory $logs | Out-Null }
$out = Join-Path $logs 'boot_trace.csv'
if (Test-Path $out) { Move-Item $out (Join-Path $logs 'boot_trace_prev.csv') -Force }
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
'time,sec_since_boot,cpu_pct,disk_pct,disk_queue,avail_mb,explorer,top_cpu,top_io' | Out-File $out -Encoding ascii
$svcMap = @{}
function Update-SvcMap {
    $script:svcMap = @{}
    foreach ($s in (Get-CimInstance Win32_Service)) {
        $k = [int]$s.ProcessId
        if ($k -gt 0) { if ($script:svcMap.ContainsKey($k)) { $script:svcMap[$k] += '+' + $s.Name } else { $script:svcMap[$k] = $s.Name } }
    }
}
function Get-Label($e) {
    $n = $e.Name; $id = [int]$e.IDProcess
    if ($n -like 'svchost*') { return ('{0}[{1}]' -f $n, $script:svcMap[$id]) }
    if ($n -match '^(pwsh|pythonw|python|powershell|node|WmiPrvSE|cmd|wscript|cscript)') {
        $c = (Get-CimInstance Win32_Process -Filter "ProcessId=$id").CommandLine
        if ($c) { $c = $c -replace '"',''; if ($c.Length -gt 80) { $c = $c.Substring($c.Length - 80) }; return ('{0}<{1}>' -f $n, $c) }
    }
    return $n
}
for ($i = 0; $i -lt 48; $i++) {
    if ($i % 8 -eq 0) { Update-SvcMap }
    $c = (Get-Counter '\Processor(_Total)\% Processor Time','\PhysicalDisk(_Total)\% Disk Time','\PhysicalDisk(_Total)\Avg. Disk Queue Length','\Memory\Available MBytes' -SampleInterval 1 -MaxSamples 1).CounterSamples
    $p = Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | Where-Object { $_.Name -notin '_Total','Idle' -and [int]$_.IDProcess -ne $PID }
    $cpu = ($p | Sort-Object PercentProcessorTime -Descending | Select-Object -First 4 | ForEach-Object { '{0}={1}%' -f (Get-Label $_), $_.PercentProcessorTime }) -join ' | '
    $io  = ($p | Sort-Object IODataBytesPersec -Descending | Select-Object -First 3 | ForEach-Object { '{0}={1}MB/s' -f (Get-Label $_), [math]::Round($_.IODataBytesPersec / 1MB, 1) }) -join ' | '
    $ex = [bool](Get-Process explorer -ErrorAction SilentlyContinue)
    $row = '{0},{1},{2},{3},{4},{5},{6},"{7}","{8}"' -f (Get-Date -Format 'HH:mm:ss'), [int]((Get-Date) - $boot).TotalSeconds, [math]::Round($c[0].CookedValue), [math]::Round($c[1].CookedValue), [math]::Round($c[2].CookedValue, 1), [int]$c[3].CookedValue, $ex, ($cpu -replace '"',''), ($io -replace '"','')
    Add-Content $out $row -Encoding ascii
    Start-Sleep -Seconds 3
}
