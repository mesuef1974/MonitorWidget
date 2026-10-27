# اختبارات Pester (5+) للدوال النقية في widget.ps1 وidentity.ps1.
# الدوال تُستخرج من widget.ps1 عبر AST ولا يُشغَّل السكربت نفسه (لا نافذة ولا إعدادات ولا شبكة).
#   Invoke-Pester -Path tests/widget_functions.Tests.ps1 -CI

BeforeAll {
    Add-Type -AssemblyName WindowsBase   # [Windows.Rect] لدالة Get-WidgetPlacement
    . (Join-Path $PSScriptRoot 'widget_test_identity.ps1')   # identity.ps1 من الجذر + Resolve-WidgetFunctionText
    $source = Get-Content -LiteralPath (Join-Path $script:widgetRepoRoot 'widget.ps1') -Raw
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($errors.Message -join '; ') }
    foreach ($name in @('Alpha', 'Normalize-WidgetOpacity', 'Lvl', 'Fmt-Bytes', 'Get-SummaryLevel', 'Test-AppearanceRecord', 'Get-WidgetPlacement', 'Get-Palette')) {
        $fn = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }.GetNewClosure(), $true)
        if (-not $fn) { throw "Missing $name" }
        . ([scriptblock]::Create((Resolve-WidgetFunctionText $fn.Extent.Text)))
    }
    $script:S = @{ palette = 'teal'; opacity = .25 }
    function Rect([double]$l, [double]$t, [double]$w, [double]$h) { [Windows.Rect]::new($l, $t, $w, $h) }
}

Describe 'Alpha' {
    It 'يسبق اللون بقناة الشفافية' { Alpha '#112233' '80' | Should -Be '#80112233' }
    It 'يقبل اللون بلا علامة #' { Alpha '112233' 'FF' | Should -Be '#FF112233' }
}

Describe 'Normalize-WidgetOpacity' {
    It 'الافتراضي 0.96 للقيم الفارغة والمنطقية والنصّية' {
        Normalize-WidgetOpacity $null | Should -Be 0.96
        Normalize-WidgetOpacity $true | Should -Be 0.96
        Normalize-WidgetOpacity 'abc' | Should -Be 0.96
        Normalize-WidgetOpacity 'NaN' | Should -Be 0.96
    }
    It 'يحصر القيمة بين 0.1 و1.0' {
        Normalize-WidgetOpacity '2' | Should -Be 1.0
        Normalize-WidgetOpacity 0 | Should -Be 0.1
        Normalize-WidgetOpacity '0.5' | Should -Be 0.5
    }
}

Describe 'Lvl' {
    It 'يصنّف بحسب عتبتي التحذير والخطر (الحدّ ضمن المستوى الأعلى)' {
        Lvl 50 70 90 | Should -Be 'ok'
        Lvl 70 70 90 | Should -Be 'warn'
        Lvl 89.9 70 90 | Should -Be 'warn'
        Lvl 90 70 90 | Should -Be 'bad'
    }
}

Describe 'Fmt-Bytes' {
    It 'يختار الوحدة المناسبة' {
        Fmt-Bytes 512 | Should -Be '512 B'
        Fmt-Bytes 2048 | Should -Be '2 KB'
        Fmt-Bytes (1.5 * 1MB) | Should -Match '^1[.,]5 MB$'
        Fmt-Bytes (2 * 1GB) | Should -Match '^2[.,]00 GB$'
    }
}

Describe 'Get-SummaryLevel' {
    It 'الأسوأ يغلب' {
        Get-SummaryLevel @('ok', 'warn', 'ok') | Should -Be 'warn'
        Get-SummaryLevel @('ok', 'bad', 'warn') | Should -Be 'bad'
        Get-SummaryLevel @('ok') | Should -Be 'ok'
        Get-SummaryLevel @() | Should -Be 'ok'
    }
}

Describe 'Test-AppearanceRecord' {
    It 'يقبل سجلًّا صحيحًا' {
        Test-AppearanceRecord ([pscustomobject]@{ schemaVersion = 1; revision = 3; mode = 'dark'; palette = 'teal' }) | Should -BeTrue
        Test-AppearanceRecord ([pscustomobject]@{ schemaVersion = [long]1; revision = [long]0; mode = 'auto'; palette = 'graphite' }) | Should -BeTrue
    }
    It 'يرفض الأنواع والقيم الخاطئة' {
        Test-AppearanceRecord $null | Should -BeFalse
        Test-AppearanceRecord ([pscustomobject]@{ schemaVersion = '1'; revision = 3; mode = 'dark'; palette = 'teal' }) | Should -BeFalse
        Test-AppearanceRecord ([pscustomobject]@{ schemaVersion = 2; revision = 3; mode = 'dark'; palette = 'teal' }) | Should -BeFalse
        Test-AppearanceRecord ([pscustomobject]@{ schemaVersion = 1; revision = -1; mode = 'dark'; palette = 'teal' }) | Should -BeFalse
        Test-AppearanceRecord ([pscustomobject]@{ schemaVersion = 1; revision = 0; mode = 'sepia'; palette = 'teal' }) | Should -BeFalse
        Test-AppearanceRecord ([pscustomobject]@{ schemaVersion = 1; revision = 0; mode = 'dark'; palette = 'red' }) | Should -BeFalse
    }
}

Describe 'Get-WidgetPlacement' {
    It 'يبقي النافذة داخل منطقة العمل' {
        $work = Rect 0 0 1000 800
        $p = Get-WidgetPlacement $work 300 200 900 700
        $p.Left | Should -Be 700; $p.Top | Should -Be 600
        $p = Get-WidgetPlacement $work 300 200 -50 -20
        $p.Left | Should -Be 0; $p.Top | Should -Be 0
        $p = Get-WidgetPlacement $work 300 200 100 100
        $p.Left | Should -Be 100; $p.Top | Should -Be 100
    }
    It 'يحترم إزاحة شاشة ثانوية' {
        $p = Get-WidgetPlacement (Rect 1920 0 1000 800) 300 200 0 0
        $p.Left | Should -Be 1920; $p.Top | Should -Be 0
    }
}

Describe 'Get-Palette (من web/css/tokens.css عبر identity.ps1)' {
    It 'كل لوحة في الوضعين تحمل المفاتيح الأساسية' {
        foreach ($pal in @('teal', 'blue', 'violet', 'graphite')) {
            $script:S.palette = $pal
            foreach ($light in @($true, $false)) {
                $p = Get-Palette $light
                foreach ($k in @('ink', 'muted', 'accent', 'accentText', 'card', 'line', 'base', 'onWarn', 'knob')) { $p[$k] | Should -Not -BeNullOrEmpty -Because "$pal light=$light يحتاج $k" }
                $p.accent | Should -Match '^#[0-9A-F]{6}$'
            }
        }
    }
    It 'teal الفاتح يغمّق accent للنصّ فقط' {
        $script:S.palette = 'teal'
        $p = Get-Palette $true
        $p.accent | Should -Be '#08A177'
        $p.accentText | Should -Be '#067A5A'
    }
    It 'الوضع الداكن وبقية اللوحات: نصّ accent يساوي accent' {
        $script:S.palette = 'teal'; (Get-Palette $false).accentText | Should -Be (Get-Palette $false).accent
        $script:S.palette = 'blue'; (Get-Palette $true).accentText | Should -Be '#2F62D6'
    }
    It 'onWarn وknob أبيضان في الفاتح' {
        $script:S.palette = 'teal'
        $p = Get-Palette $true
        $p.onWarn | Should -Be '#FFFFFF'
    }
}

Describe 'Convert-IdentityColor' {
    It 'يوسّع #rgb ويطبّع الأحرف' { Convert-IdentityColor '#abc' | Should -Be '#AABBCC' }
    It 'يمزج rgba فوق الخلفية' {
        Convert-IdentityColor 'rgba(255,255,255,.5)' '#000000' | Should -Be '#808080'
        Convert-IdentityColor 'rgb(1,2,3)' | Should -Be '#010203'
    }
    It 'يعيد null لما لا يفهمه' { Convert-IdentityColor 'var(--accent)' | Should -BeNullOrEmpty }
}

Describe 'Read-IdentityTokens' {
    It 'يطبّق الجذر ثم الثيم ثم اللوحة' {
        $t = Read-IdentityTokens $script:widgetRepoRoot $true 'teal'
        $t['--accent'] | Should -Be '#08a177'
        $t['--accent-text'] | Should -Be '#067a5a'
        $t['--bg'] | Should -Not -BeNullOrEmpty
        (Read-IdentityTokens $script:widgetRepoRoot $true 'blue')['--accent'] | Should -Be '#2f62d6'
        (Read-IdentityTokens $script:widgetRepoRoot $false 'teal')['--accent'] | Should -Be '#3ee6b0'
    }
    It 'يعيد null لجذر بلا رموز' { Read-IdentityTokens $env:TEMP $true 'teal' | Should -BeNullOrEmpty }
}
