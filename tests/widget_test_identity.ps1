# مساعد مشترك لاختبارات الويدجت التي تستخرج الدوال من widget.ps1 عبر AST ثم تستدعيها:
# Get-Palette صارت تعتمد على Get-IdentityPalette المعرَّفة في identity.ps1 بجذر المستودع (تقرأ web\css	okens.css)،
# وداخل الاختبار يشير $PSScriptRoot إلى tests\ لا إلى الجذر، لذا يُحمَّل identity.ps1 هنا من الجذر وتُعاد كتابة
# $PSScriptRoot في نصّ أيّ دالة مستخرجة إلى مسار الجذر الحرفي قبل تنفيذها.
# يُستدعى بـ:  . (Join-Path $PSScriptRoot 'widget_test_identity.ps1')
# ثمّ:        . ([scriptblock]::Create((Resolve-WidgetFunctionText $fn.Extent.Text)))
$script:widgetRepoRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $script:widgetRepoRoot 'identity.ps1')
function Resolve-WidgetFunctionText([string]$text) {
    return $text.Replace('$PSScriptRoot', "'" + $script:widgetRepoRoot.Replace("'", "''") + "'")
}
