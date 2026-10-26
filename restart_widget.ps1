# إعادة تشغيل الويجت فقط (لا الوكيل ولا الخادم). الويجت يعمل مسؤولاً عبر المهمة MonitorWidget-Launch،
# فإنهاؤه من جلسة عادية يفشل؛ في تلك الحالة شغّل هذا السكربت كمسؤول.
$r = $PSScriptRoot
$wp = [regex]::Escape((Join-Path $r 'widget.ps1'))
Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.Name -in 'powershell.exe', 'pwsh.exe' -and $_.CommandLine -match $wp } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch { Write-Warning ('تعذّر إنهاء ' + $_.Id + ' (يعمل مسؤولاً؟ شغّل هذا السكربت كمسؤول)') } }
Start-Sleep 1
if (Get-ScheduledTask -TaskName 'MonitorWidget-Launch' -ErrorAction SilentlyContinue) { Start-ScheduledTask -TaskName 'MonitorWidget-Launch' }
else { Start-Process wscript.exe -WindowStyle Hidden -ArgumentList ('"' + (Join-Path $r 'launch_mini.vbs') + '"') }
'widget restarted'
