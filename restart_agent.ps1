# إعادة تشغيل الوكيل والخادم (لتطبيق تعديلات الخلفية). الخلفية تعمل مسؤولاً عبر المهمة MonitorWidget-Agent،
# فلا يمكن إنهاؤها بـ Stop-Process من جلسة عادية؛ نستخدم المهمة نفسها.
$r = $PSScriptRoot
$ap = [regex]::Escape($r) + '[\\/](agent|server)\.py'   # [\\/] = شرطة عكسية أو أمامية؛ [\/] كانت تطابق الأمامية فقط
$task = Get-ScheduledTask -TaskName 'MonitorWidget-Agent' -ErrorAction SilentlyContinue
$before = (Get-NetTCPConnection -LocalPort 47832 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
if ($task) { Stop-ScheduledTask -TaskName 'MonitorWidget-Agent' -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.Name -in 'python.exe', 'pythonw.exe' -and $_.CommandLine -match $ap } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch { Write-Warning ("تعذّر إنهاء " + $_.Id + " (يعمل مسؤولاً؟ شغّل هذا السكربت كمسؤول)") } }
Start-Sleep 2
if ($task) { Start-ScheduledTask -TaskName 'MonitorWidget-Agent' }
else { Start-Process pythonw.exe -ArgumentList ('"' + (Join-Path $r 'agent.py') + '"') -WindowStyle Hidden }
Start-Sleep 3
$after = (Get-NetTCPConnection -LocalPort 47832 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess
if ($before -and $after -eq $before) { throw "العملية القديمة $before ما زالت تستمع؛ لم تُستبدل (مرتفعة؟ شغّل uninstall_admin_backend.ps1 كمسؤول مرة واحدة)." }
'agent restarted'
