# Slow hardware / security probe. Read-only. Prints one JSON object. Every section is independent: a failure only drops that section.
param([switch]$SkipUpdates, [switch]$UpdatesOnly)
$ErrorActionPreference = 'SilentlyContinue'
$o = [ordered]@{ ts = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }

# --- updates-only mode: used by the server to refresh the pending-update count on its own schedule
if ($UpdatesOnly) {
    try {
        $job = Start-Job { $s = New-Object -ComObject Microsoft.Update.Session; $q = $s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0"); @{ n = $q.Updates.Count; titles = @($q.Updates | Select-Object -First 5 | ForEach-Object { $_.Title }) } }
        if (Wait-Job $job -Timeout 75) { $o.updates = Receive-Job $job } else { Stop-Job $job }
        Remove-Job $job -Force
    } catch {}
    $o | ConvertTo-Json -Depth 6 -Compress
    exit
}

# --- disks
$o.disks = @(Get-PhysicalDisk | ForEach-Object {
    $r = $_ | Get-StorageReliabilityCounter
    [ordered]@{ name = $_.FriendlyName; media = "$($_.MediaType)"; sizeGB = [math]::Round($_.Size / 1GB); health = "$($_.HealthStatus)"; op = "$($_.OperationalStatus)"
        tempC = $r.Temperature; wearPct = $r.Wear; readErr = $r.ReadErrorsTotal; writeErr = $r.WriteErrorsTotal; hours = $r.PowerOnHours }
})

# --- battery
try {
    $d = (Get-CimInstance -Namespace root\wmi -ClassName BatteryStaticData).DesignedCapacity | Select-Object -First 1
    $f = (Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity).FullChargedCapacity | Select-Object -First 1
    $c = (Get-CimInstance -Namespace root\wmi -ClassName BatteryCycleCount).CycleCount | Select-Object -First 1
    $b = Get-CimInstance Win32_Battery | Select-Object -First 1
    if (-not $d -or -not $f) {
  $tmp = Join-Path $env:TEMP 'bat_probe.html'
  powercfg /batteryreport /output $tmp | Out-Null
  if (Test-Path $tmp) {
    $h = Get-Content $tmp -Raw
    $md = [regex]::Match($h, 'DESIGN CAPACITY</span></td>\s*<td[^>]*>\s*([\d,]+)')
    $mf = [regex]::Match($h, 'FULL CHARGE CAPACITY</span></td>\s*<td[^>]*>\s*([\d,]+)')
    $mc = [regex]::Match($h, 'CYCLE COUNT</span></td>\s*<td[^>]*>\s*([\d,]+)')
    if ($md.Success) { $d = [int]($md.Groups[1].Value -replace ',', '') }
    if ($mf.Success) { $f = [int]($mf.Groups[1].Value -replace ',', '') }
    if (-not $c -and $mc.Success) { $c = [int]($mc.Groups[1].Value -replace ',', '') }
    Remove-Item $tmp -ErrorAction SilentlyContinue
  }
}
if ($d -and $f) { $o.battery = [ordered]@{ designed = $d; full = $f; healthPct = [math]::Round(100 * $f / $d, 1); cycles = $c; charge = $b.EstimatedChargeRemaining; status = $b.BatteryStatus } }
} catch {}

# --- boot time (ms) from Diagnostics-Performance event 100
try {
    $ev = Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-Diagnostics-Performance/Operational'; Id = 100 } -MaxEvents 8
    $o.boots = @($ev | ForEach-Object { $x = [xml]$_.ToXml(); $bt = ($x.Event.EventData.Data | Where-Object Name -eq 'BootTime').'#text'; [ordered]@{ ts = [int][DateTimeOffset]::new($_.TimeCreated).ToUnixTimeSeconds(); bootSec = [math]::Round([double]$bt / 1000, 1) } })
} catch {}

# --- startup items + uptime + reboot flag
try { $o.startupCount = @(Get-CimInstance Win32_StartupCommand).Count } catch {}
try { $os = Get-CimInstance Win32_OperatingSystem; $o.uptimeH = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours, 1); $o.os = "$($os.Caption) $($os.Version)" } catch {}
$o.pendingReboot = [bool]((Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') -or (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'))

# --- defender
try { $m = Get-MpComputerStatus; $o.defender = [ordered]@{ realtime = [bool]$m.RealTimeProtectionEnabled; sigAgeDays = $m.AntivirusSignatureAge; quickScanAgeDays = $m.QuickScanAge; service = [bool]$m.AMServiceEnabled } } catch {}

# --- power
try { $o.powerPlan = (powercfg /getactivescheme) -replace '^.*\((.*)\).*$', '$1' } catch {}

# --- pending Windows updates (slow; COM search, hard cap)
if (-not $SkipUpdates) {
    try {
        $job = Start-Job { $s = New-Object -ComObject Microsoft.Update.Session; $q = $s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0"); @{ n = $q.Updates.Count; titles = @($q.Updates | Select-Object -First 5 | ForEach-Object { $_.Title }) } }
        if (Wait-Job $job -Timeout 75) { $o.updates = Receive-Job $job } else { Stop-Job $job }
        Remove-Job $job -Force
    } catch {}
}
$o | ConvertTo-Json -Depth 6 -Compress
