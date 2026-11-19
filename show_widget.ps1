# Shows the monitor widget through the local server (works when the widget is hidden or was closed).
# بعد النشر المحمي لا يقرأ الرمز الكامل إلا المسؤولون: من جلسة عادية تُشغَّل مهمة الويدجت بدلًا من الطلب.
$root = $PSScriptRoot
$live = Join-Path ([Environment]::GetFolderPath('CommonApplicationData')) 'MonitorWidget\app'
if (Test-Path -LiteralPath (Join-Path $live '.deployment\manifest.json')) { $root = $live }
try { $t = (Get-Content -LiteralPath (Join-Path $root '.token') -Raw -ErrorAction Stop).Trim() } catch { $t = $null }
if (-not $t) { Start-ScheduledTask -TaskName 'MonitorWidget-Launch' -ErrorAction SilentlyContinue; return }
try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:47832/api/widget' -Headers @{ 'X-Token' = $t } -ContentType 'application/json' -Body '{"show":true}' -TimeoutSec 30 | Out-Null } catch { }
