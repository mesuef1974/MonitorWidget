# proc_report.ps1  (read-only: lists processes and memory, changes nothing)
$ErrorActionPreference = 'SilentlyContinue'
$out = Join-Path $PSScriptRoot 'proc_report.txt'
$L = New-Object System.Collections.Generic.List[string]
$L.Add('== memory (MB)')
foreach ($c in (Get-Counter '\Memory\Standby Cache Normal Priority Bytes','\Memory\Standby Cache Reserve Bytes','\Memory\Standby Cache Core Bytes','\Memory\Available Bytes').CounterSamples) {
  $L.Add(('{0}: {1:N0}' -f $c.Path.Split('\')[-1], ($c.CookedValue / 1MB)))
}
$procs = Get-CimInstance Win32_Process
$byId = @{}
foreach ($p in $procs) { $byId[[int]$p.ProcessId] = $p }
$L.Add('')
$L.Add('== top 15 process names by private memory (MB)')
$all = foreach ($p in $procs) { $g = Get-Process -Id $p.ProcessId; if ($g) { [pscustomobject]@{N=$p.Name; Priv=$g.PrivateMemorySize64; WS=$g.WorkingSet64} } }
$all | Group-Object N | ForEach-Object {
  [pscustomobject]@{N=$_.Name; Count=$_.Count; PrivMB=[math]::Round((($_.Group | Measure-Object Priv -Sum).Sum)/1MB); WSMB=[math]::Round((($_.Group | Measure-Object WS -Sum).Sum)/1MB)}
} | Sort-Object PrivMB -Descending | Select-Object -First 15 | ForEach-Object { $L.Add(('{0}  x{1}  private={2}  ws={3}' -f $_.N, $_.Count, $_.PrivMB, $_.WSMB)) }
foreach ($n in 'node.exe','pwsh.exe','cmd.exe','msedge.exe','claude.exe') {
  $set = $procs | Where-Object { $_.Name -ieq $n }
  if (-not $set) { continue }
  $L.Add('')
  $L.Add("== $n : parent groups (orphan = parent gone)")
  $rows = foreach ($p in $set) {
    $g = Get-Process -Id $p.ProcessId
    $par = $byId[[int]$p.ParentProcessId]
    $pn = if ($par) { $par.Name } else { '(gone)' }
    $tag = ''
    if ($n -eq 'node.exe' -and $p.CommandLine) {
      $m = [regex]::Match($p.CommandLine, '[^\s"]+\.(js|mjs|cjs|ts|py)\b')
      if ($m.Success) { $tag = Split-Path $m.Value -Leaf }
    }
    [pscustomobject]@{Parent=$pn; Tag=$tag; Priv=$g.PrivateMemorySize64; WS=$g.WorkingSet64}
  }
  $rows | Group-Object Parent, Tag | Sort-Object { ($_.Group | Measure-Object Priv -Sum).Sum } -Descending | ForEach-Object {
    $pv = [math]::Round((($_.Group | Measure-Object Priv -Sum).Sum)/1MB)
    $ws = [math]::Round((($_.Group | Measure-Object WS -Sum).Sum)/1MB)
    $L.Add(('  parent={0}  script={1}  count={2}  private={3}  ws={4}' -f $_.Group[0].Parent, ($(if ($_.Group[0].Tag) { $_.Group[0].Tag } else { '-' })), $_.Count, $pv, $ws))
  }
}
$L | Out-File -FilePath $out -Encoding utf8
$L | ForEach-Object { Write-Host $_ }
Write-Host ''
Write-Host "saved: $out"
