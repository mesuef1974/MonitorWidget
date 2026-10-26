# Apply brand icon everywhere (shortcuts, Start menu, tray, dashboard). Safe: touches only MonitorWidget shortcuts.
$root = Join-Path $env:USERPROFILE 'Documents\Scripts\MonitorWidget'
$ico  = Join-Path $root 'assets\icon.ico'
if (-not (Test-Path -LiteralPath $ico)) { Write-Host 'icon.ico not found'; return }
$sh = New-Object -ComObject WScript.Shell
$dirs = @("$env:USERPROFILE\OneDrive\Desktop","$env:USERPROFILE\Desktop","$env:PUBLIC\Desktop",
          "$env:APPDATA\Microsoft\Windows\Start Menu\Programs","$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
          "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup") | Where-Object { Test-Path $_ } | Select-Object -Unique
$n = 0
foreach ($f in (Get-ChildItem $dirs -Filter *.lnk -Recurse -ErrorAction SilentlyContinue)) {
  try {
    $l = $sh.CreateShortcut($f.FullName)
    if ($f.BaseName -match 'Monitor Widget|Marsad|MonitorWidget' -or $f.BaseName -match [regex]::Unescape('مرصد') -or "$($l.TargetPath) $($l.Arguments)" -match 'MonitorWidget|widget\.ps1|launch_mini|show_widget') {
      $l.IconLocation = "$ico,0"; $l.Save(); $n++; Write-Host "icon set: $($f.FullName)"
    }
  } catch { Write-Host "skip (no permission): $($f.FullName)" }
}
if ($n -eq 0) {
  $p = Join-Path $dirs[0] 'Monitor Widget.lnk'
  $l = $sh.CreateShortcut($p)
  $l.TargetPath = "$env:WINDIR\System32\wscript.exe"; $l.Arguments = "`"$root\launch_mini.vbs`""
  $l.WorkingDirectory = $root; $l.IconLocation = "$ico,0"; $l.Save(); Write-Host "created: $p"
}
# restart widget so header logo + tray icon load
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'widget\.ps1' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
# restart server (agent server_guard brings it back within ~60 s)
Get-NetTCPConnection -LocalPort 47832 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep 3
Start-Process wscript.exe "`"$root\launch_mini.vbs`""
& ie4uinit.exe -show
Write-Host 'Done. Wait ~60 s for the server, then Ctrl+F5 in the dashboard.'
