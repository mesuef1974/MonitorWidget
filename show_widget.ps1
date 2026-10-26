# Shows the monitor widget through the local server (works when the widget is hidden or was closed).
$root = $PSScriptRoot
$t = (Get-Content (Join-Path $root '.token') -Raw).Trim()
try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:47832/api/widget' -Headers @{ 'X-Token' = $t } -ContentType 'application/json' -Body '{"show":true}' -TimeoutSec 30 | Out-Null } catch { }
