# READ-ONLY. Lists OneDrive leftovers. Changes nothing.
$r = [ordered]@{}
$r.Processes   = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'OneDrive|FileCoAuth|FileSyncHelper' } | Select-Object -ExpandProperty Name)
$r.RunKeys     = @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Run','HKLM:\Software\Microsoft\Windows\CurrentVersion\Run','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run' | ForEach-Object { $k=$_; (Get-ItemProperty $k -ErrorAction SilentlyContinue).PSObject.Properties | Where-Object { $_.Name -match 'OneDrive' } | ForEach-Object { "$k :: $($_.Name)" } })
$r.Tasks       = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match 'OneDrive' } | ForEach-Object { "$($_.TaskPath)$($_.TaskName)" })
$paths = @("$env:LOCALAPPDATA\Microsoft\OneDrive","$env:LOCALAPPDATA\OneDrive","$env:ProgramFiles\Microsoft OneDrive","${env:ProgramFiles(x86)}\Microsoft OneDrive","$env:ProgramData\Microsoft OneDrive","$env:SystemRoot\SysWOW64\OneDriveSetup.exe","$env:SystemRoot\System32\OneDriveSetup.exe","$env:USERPROFILE\OneDrive")
$r.Folders     = @($paths | Where-Object { Test-Path $_ } | ForEach-Object { $s = if ((Get-Item $_ -Force).PSIsContainer) { [math]::Round(((Get-ChildItem $_ -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum)/1MB,1) } else { 'file' }; "$_  ($s MB)" })
$r.NavPane     = @('HKCU:\Software\Classes\CLSID\{018D5C66-4533-4307-9B53-224DE2ED1FE6}','HKCR:\CLSID\{018D5C66-4533-4307-9B53-224DE2ED1FE6}' | ForEach-Object { try { if (-not (Test-Path 'HKCR:')) { New-PSDrive HKCR Registry HKEY_CLASSES_ROOT | Out-Null }; if (Test-Path $_) { "$_  System.IsPinnedToNameSpaceTree=" + (Get-ItemProperty $_ -ErrorAction SilentlyContinue).'System.IsPinnedToNameSpaceTree' } } catch {} })
$r.ShellExt    = @(Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Shell Extensions\Approved' -ErrorAction SilentlyContinue | Out-Null; (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Shell Extensions\Approved' -ErrorAction SilentlyContinue).PSObject.Properties | Where-Object { $_.Value -match 'OneDrive' } | ForEach-Object { $_.Name + ' = ' + $_.Value })
$r.EnvVars     = @(Get-ChildItem Env: | Where-Object { $_.Name -match '^OneDrive' } | ForEach-Object { "$($_.Name)=$($_.Value)" })
$us = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -ErrorAction SilentlyContinue
$r.KnownFolders= @($us.PSObject.Properties | Where-Object { "$($_.Value)" -match 'OneDrive' } | ForEach-Object { "$($_.Name) -> $($_.Value)" })
$r.Policies    = @('HKLM:\SOFTWARE\Policies\Microsoft\Windows\OneDrive' | Where-Object { Test-Path $_ })
$r.Installed   = @(Get-Package -Name '*OneDrive*' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
$r.AppxPkg     = @(Get-AppxPackage -Name '*OneDrive*' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty PackageFullName)
foreach ($k in $r.Keys) { "== $k"; if (@($r[$k]).Count) { $r[$k] | ForEach-Object { "   $_" } } else { "   (none)" } }
