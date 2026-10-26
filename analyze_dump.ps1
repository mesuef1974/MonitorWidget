# Read-only: analyses crash.dmp with the console debugger shipped inside WinDbg. Writes crash_analysis.txt next to this script.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$dump = Join-Path $here 'crash.dmp'
$out  = Join-Path $here 'crash_analysis.txt'
$cdb = $null
$pkg = Get-AppxPackage -Name 'Microsoft.WinDbg*' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($pkg) { $cdb = Get-ChildItem $pkg.InstallLocation -Recurse -Filter cdb.exe -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match 'amd64|x64' } | Select-Object -First 1 -ExpandProperty FullName }
if (-not $cdb) { $c = Get-Command cdb.exe -ErrorAction SilentlyContinue; if ($c) { $cdb = $c.Source } }
if (-not $cdb) { Write-Host 'cdb.exe not found'; exit 1 }
Write-Host "cdb: $cdb"
New-Item -ItemType Directory -Force -Path 'C:\Symbols' | Out-Null
$sym = 'srv*C:\Symbols*https://msdl.microsoft.com/download/symbols'
& $cdb -z $dump -y $sym -c '!analyze -v; lm t n; q' *> $out
Write-Host "done: $out"
Select-String -Path $out -Pattern 'BUGCHECK_CODE|BUGCHECK_P|IMAGE_NAME|MODULE_NAME|FAILURE_BUCKET_ID|PROCESS_NAME|STACK_TEXT' -Context 0,0 | Select-Object -First 20 | ForEach-Object { $_.Line }
