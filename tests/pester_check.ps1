# غلاف لـ run_all: يشغّل اختبارات Pester لدوال الويدجت إن توفّر Pester 5+، وإلا يطبع SKIP ويخرج 77 (تغطية ناقصة لا نجاح).
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8   # مخرجات عربية سليمة داخل run_all
$mod = Get-Module -ListAvailable Pester | Sort-Object Version -Descending | Select-Object -First 1
if (-not $mod -or $mod.Version.Major -lt 5) {
    Write-Output ("SKIP Pester 5+ غير متوفر (الموجود: " + $(if ($mod) { $mod.Version } else { 'لا شيء' }) + "); Install-Module Pester -Scope CurrentUser")
    exit 77
}
Import-Module Pester -MinimumVersion 5.0
$r = Invoke-Pester -Path (Join-Path $PSScriptRoot 'widget_functions.Tests.ps1') -PassThru -Output Normal
if ($r.FailedCount -gt 0 -or $r.TotalCount -eq 0) { Write-Output "FAIL Pester: $($r.FailedCount) فاشل من $($r.TotalCount)"; exit 1 }
Write-Output "PASS Pester: $($r.PassedCount) اختبارًا لدوال widget.ps1 وidentity.ps1"
