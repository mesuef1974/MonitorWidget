# الهوية البصرية المشتركة: يقرأ web\css\tokens.css (المصدر الوحيد للألوان) فتأخذ النوافذ الأصلية (الويدجت والغلاف)
# الألوان نفسها التي تستخدمها صفحة التطبيق، بدل نسخة منفصلة داخل السكربتات.
# يُستدعى بـ:  . (Join-Path $PSScriptRoot 'identity.ps1')

function Read-IdentityTokens([string]$root, [bool]$light, [string]$palette) {
    $file = Join-Path $root 'web\css\tokens.css'
    if (-not (Test-Path -LiteralPath $file)) { return $null }
    $theme = if ($light) { 'light' } else { 'dark' }
    $text = [IO.File]::ReadAllText($file, [Text.Encoding]::UTF8)
    $text = [regex]::Replace($text, '/\*.*?\*/', '', 'Singleline')
    $out = @{}
    # الترتيب نفسه الذي يطبّقه المتصفح: الجذر ثم الثيم ثم الباليت داخل الثيم
    foreach ($want in @('', "theme=$theme", "palette=$palette,theme=$theme")) {
        foreach ($m in [regex]::Matches($text, '(:root(?:\[[^\]]+\])*)\s*\{([^}]*)\}')) {
            $attrs = @{}
            foreach ($a in [regex]::Matches($m.Groups[1].Value, '\[data-(\w+)="([^"]+)"\]')) { $attrs[$a.Groups[1].Value] = $a.Groups[2].Value }
            $key = (($attrs.Keys | Sort-Object | ForEach-Object { "$_=$($attrs[$_])" }) -join ',')
            if ($key -ne $want) { continue }
            foreach ($d in $m.Groups[2].Value -split ';') {
                if ($d -match '^\s*(--[\w-]+)\s*:\s*(.+?)\s*$') { $out[$Matches[1]] = $Matches[2] }
            }
        }
    }
    if ($out.Count -eq 0) { return $null }
    $out
}

# '#rgb' أو '#rrggbb' أو 'rgba(r,g,b,a)' إلى '#RRGGBB' مع مزج الشفافية فوق خلفية معطاة
function Convert-IdentityColor([string]$value, [string]$backdrop = '#000000') {
    $value = $value.Trim()
    function Parse-Hex([string]$h) {
        $h = $h.TrimStart('#'); if ($h.Length -eq 3) { $h = ($h.ToCharArray() | ForEach-Object { "$_$_" }) -join '' }
        @([Convert]::ToInt32($h.Substring(0, 2), 16), [Convert]::ToInt32($h.Substring(2, 2), 16), [Convert]::ToInt32($h.Substring(4, 2), 16))
    }
    if ($value -match '^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$') { return '#' + ((Parse-Hex $value | ForEach-Object { '{0:X2}' -f $_ }) -join '') }
    if ($value -match '^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)$') {
        $a = if ($Matches[4]) { [double]::Parse($Matches[4], [Globalization.CultureInfo]::InvariantCulture) } else { 1.0 }
        $fg = @([int]$Matches[1], [int]$Matches[2], [int]$Matches[3]); $bg = Parse-Hex $backdrop
        $mix = 0..2 | ForEach-Object { [int][Math]::Round($fg[$_] * $a + $bg[$_] * (1 - $a)) }
        return '#' + (($mix | ForEach-Object { '{0:X2}' -f $_ }) -join '')
    }
    $null
}

# لوحة الويدجت/الغلاف مشتقة من الرموز؛ ما لم يُعرَّف في الرموز يبقى كما في $fallback
function Get-IdentityPalette([string]$root, [bool]$light, [string]$palette, [hashtable]$fallback) {
    $t = Read-IdentityTokens $root $light $palette
    $p = @{}; foreach ($k in $fallback.Keys) { $p[$k] = $fallback[$k] }
    if (-not $t) { return $p }
    $bg = Convert-IdentityColor $t['--bg']
    if (-not $bg) { return $p }
    $map = @{ ink = '--ink'; muted = '--ink2'; accent = '--accent'; violet = '--violet'; ok = '--ok'; warn = '--warn'; bad = '--bad'; card = '--panel2'; track = '--raise'; hover = '--raise' }
    foreach ($k in $map.Keys) { $c = if ($t[$map[$k]]) { Convert-IdentityColor $t[$map[$k]] $bg } else { $null }; if ($c) { $p[$k] = $c } }
    $p['base'] = $bg.TrimStart('#')
    $edge = if ($t['--line2']) { Convert-IdentityColor $t['--line2'] $bg } else { $null }; if ($edge) { $p['edge'] = $edge }
    $line = if ($t['--line2']) { Convert-IdentityColor $t['--line2'] ($p['card']) } else { $null }; if ($line) { $p['line'] = $line }
    $ai = if ($t['--accent-ink']) { Convert-IdentityColor $t['--accent-ink'] $bg } else { $null }; if ($ai) { $p['pilltxt'] = $ai; $p['knob'] = $ai }
    $p
}
