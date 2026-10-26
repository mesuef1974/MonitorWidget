# Read-only: lists reliability events from the Windows event logs (last N days) as JSON on stdout. Changes nothing.
param([int]$Days = 30)
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$st = (Get-Date).AddDays(-$Days)
$specs = @(
  @{ L = 'System';      I = @(41);                K = 'crash' },
  @{ L = 'System';      I = @(6008);              K = 'unexpected' },
  @{ L = 'System';      I = @(1001);              K = 'bsod';     P = 'Microsoft-Windows-WER-SystemErrorReporting' },
  @{ L = 'System';      I = @(7, 11, 51, 153);    K = 'disk';     P = 'disk' },
  @{ L = 'System';      I = @(55, 98, 137);       K = 'disk';     P = 'Ntfs' },
  @{ L = 'System';      I = @(4101);              K = 'tdr';      P = 'Display' },
  @{ L = 'System';      I = @(1, 17, 18, 19, 20, 47); K = 'whea'; P = 'Microsoft-Windows-WHEA-Logger' },
  @{ L = 'Application'; I = @(1002);              K = 'hang';     P = 'Application Hang' },
  @{ L = 'Application'; I = @(1000);              K = 'appcrash'; P = 'Application Error' }
)
$out = New-Object System.Collections.ArrayList
foreach ($spec in $specs) {
  $h = @{ LogName = $spec.L; Id = $spec.I; StartTime = $st }
  if ($spec.P) { $h['ProviderName'] = $spec.P }
  $events = Get-WinEvent -FilterHashtable $h -MaxEvents 300
  foreach ($e in $events) {
    $app = ''
    if ($spec.K -eq 'hang' -or $spec.K -eq 'appcrash') { try { $app = [string]$e.Properties[0].Value } catch {} }
    $code = ''
    if ($spec.K -eq 'crash') { try { $code = ('0x{0:X}' -f [int64]$e.Properties[0].Value) } catch {} }
    $msg = ''
    try { $msg = ($e.Message -split "`r?`n")[0] } catch {}
    if ($msg.Length -gt 180) { $msg = $msg.Substring(0, 180) }
    $ts = [int64]([DateTimeOffset]$e.TimeCreated).ToUnixTimeSeconds()
    [void]$out.Add([pscustomobject]@{ ts = $ts; kind = $spec.K; id = [int]$e.Id; src = [string]$e.ProviderName; app = $app; code = $code; msg = $msg })
  }
}
ConvertTo-Json -InputObject @($out) -Compress -Depth 3
