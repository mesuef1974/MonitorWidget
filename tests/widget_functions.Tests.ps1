# اختبارات Pester (5+) للدوال النقية في widget.ps1 وidentity.ps1 وapp_shell.ps1.
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
        $p.accentText | Should -Be '#066B4F'
    }
    It 'الوضع الداكن وبقية اللوحات: نصّ accent يساوي accent' {
        $script:S.palette = 'teal'; (Get-Palette $false).accentText | Should -Be (Get-Palette $false).accent
        $script:S.palette = 'blue'; (Get-Palette $true).accentText | Should -Be '#2654BD'
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
        $t['--accent-text'] | Should -Be '#066b4f'
        $t['--bg'] | Should -Not -BeNullOrEmpty
        (Read-IdentityTokens $script:widgetRepoRoot $true 'blue')['--accent'] | Should -Be '#2f62d6'
        (Read-IdentityTokens $script:widgetRepoRoot $false 'teal')['--accent'] | Should -Be '#3ee6b0'
    }
    It 'يعيد null لجذر بلا رموز' { Read-IdentityTokens $env:TEMP $true 'teal' | Should -BeNullOrEmpty }
}

Describe 'Get-IdentityFonts (خطوط الهوية من tokens.css إلى WPF عبر identity.ps1)' {
    BeforeAll { Add-Type -AssemblyName PresentationCore }
    It 'رموز الخطوط الأربعة في tokens.css تبدأ بعائلات الهوية المعتمدة' {
        $t = Read-IdentityTokens $script:widgetRepoRoot $false 'teal'
        $t['--f-head'] | Should -Match '^"Zain"'
        $t['--f-list'] | Should -Match '^"El Messiri"'
        $t['--f-ui'] | Should -Match '^"Amiri"'
        $t['--f-num'] | Should -Match '^"Outfit"'
    }
    It 'يحمّل الأدوار الأربعة من assets\fonts بعائلاتها وأوزانها (Zain 700، El Messiri 500، Amiri 400/700، Outfit 400/600)' {
        $f = Get-IdentityFonts $script:widgetRepoRoot
        $f.loaded.head | Should -Be 'Zain'
        $f.loaded.list | Should -Be 'El Messiri'
        $f.loaded.ui | Should -Be 'Amiri'
        $f.loaded.num | Should -Be 'Outfit'
        foreach ($case in @(@('head', 'Bold', 'Zain-Bold.ttf'), @('list', 'Medium', 'ElMessiri-Medium.ttf'), @('ui', 'Normal', 'Amiri-Regular.ttf'), @('ui', 'SemiBold', 'Amiri-Bold.ttf'), @('num', 'Normal', 'Outfit-Regular.ttf'), @('num', 'SemiBold', 'Outfit-SemiBold.ttf'))) {
            $tf = New-Object Windows.Media.Typeface($f[$case[0]], [Windows.FontStyles]::Normal, [Windows.FontWeights]::($case[1]), [Windows.FontStretches]::Normal)
            $gt = $null
            $tf.TryGetGlyphTypeface([ref]$gt) | Should -BeTrue
            [IO.Path]::GetFileName($gt.FontUri.LocalPath) | Should -Be $case[2]
        }
    }
    It 'بلا ملفات الخطوط أو بلا رموز يبقى كل دور على Segoe UI' {
        $root = Join-Path $TestDrive 'bare'; New-Item -ItemType Directory -Path (Join-Path $root 'web\css') -Force | Out-Null
        Copy-Item (Join-Path $script:widgetRepoRoot 'web\css\tokens.css') (Join-Path $root 'web\css\tokens.css')
        $f = Get-IdentityFonts $root
        $f.loaded.Count | Should -Be 0
        foreach ($k in @('head', 'list', 'ui', 'num')) { $f[$k].Source | Should -Be 'Segoe UI' }
        (Get-IdentityFonts (Join-Path $TestDrive 'nothing')).ui.Source | Should -Be 'Segoe UI'
    }
}

Describe 'app_shell.ps1: السقوط إلى Edge وتسجيل المحرك لصفحة الصحة' {
    BeforeAll {
        # الدوال النقية تُستخرج من app_shell.ps1 عبر AST؛ السكربت نفسه لا يُشغَّل (param إلزامي ونافذة WPF)
        $shellSource = Get-Content -LiteralPath (Join-Path $script:widgetRepoRoot 'app_shell.ps1') -Raw
        $shellTokens = $null; $shellErrors = $null
        $shellAst = [Management.Automation.Language.Parser]::ParseInput($shellSource, [ref]$shellTokens, [ref]$shellErrors)
        if ($shellErrors.Count) { throw ($shellErrors.Message -join '; ') }
        foreach ($name in @('ConvertTo-ShellStateJson', 'Write-ShellState', 'Get-EdgeWindowHeight', 'Get-EdgeFallbackArgs')) {
            $fn = $shellAst.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }.GetNewClosure(), $true)
            if (-not $fn) { throw "Missing $name" }
            . ([scriptblock]::Create($fn.Extent.Text))
        }
    }
    It 'يكتب مخطط الخادم نفسه: engine / reason / ts' {
        ConvertTo-ShellStateJson 'edge' 'init_failed' 5 | Should -Be '{"engine":"edge","reason":"init_failed","ts":5}'
    }
    It 'يطرح شريط عنوان Edge (40) ولا ينزل عن 480' {
        Get-EdgeWindowHeight 1000 | Should -Be 960
        Get-EdgeWindowHeight 100 | Should -Be 480
    }
    It 'وسائط Edge --app: التذكرة نفسها وملف تعريف منفصل والمقاس والموضع المطلوبان' {
        $cmd = Get-EdgeFallbackArgs 'http://127.0.0.1:47832/?k=abc' 10 20 1200 1000 'C:\p\appwin'
        $cmd | Should -Be @('--app=http://127.0.0.1:47832/?k=abc', '--user-data-dir=C:\p\appwin', '--window-size=1200,960', '--window-position=10,20')
    }
    It 'يكتب ملف الحالة ذريًّا بلا BOM ويقرؤه الخادم JSON' {
        $p = Join-Path $TestDrive 'MonitorWidget\shell_state.json'
        Write-ShellState 'edge' 'no_webview2' $p | Should -BeTrue
        ([IO.File]::ReadAllBytes($p))[0] | Should -Be 0x7B   # يبدأ بـ { لا بعلامة BOM
        $j = Get-Content -LiteralPath $p -Raw | ConvertFrom-Json
        $j.engine | Should -Be 'edge'
        $j.reason | Should -Be 'no_webview2'
        $j.ts | Should -BeGreaterThan 1700000000
        Write-ShellState 'webview2' '' $p | Should -BeTrue
        (Get-Content -LiteralPath $p -Raw | ConvertFrom-Json).engine | Should -Be 'webview2'
        @(Get-ChildItem (Split-Path $p -Parent) -Filter '*.tmp').Count | Should -Be 0   # لا بقايا ملفات مؤقتة
    }
    It 'يعيد false بدل الفشل حين يتعذّر الكتابة' {
        Write-ShellState 'edge' 'no_shell' 'Z:\nope\<>\shell_state.json' | Should -BeFalse
    }
}

Describe 'إتاحة الويدجت: AutomationProperties وLiveSetting (WPF معزول، بلا نافذة)' {
    BeforeAll {
        Add-Type -AssemblyName PresentationFramework
        foreach ($name in @('Brush', 'Surface-Brush', 'New-Text', 'New-Card', 'Gap', 'New-Ring', 'Ring-Arc', 'Set-Ring', 'Level-Brush', 'New-Chip', 'Set-Chip')) {
            $fn = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }.GetNewClosure(), $true)
            if (-not $fn) { throw "Missing $name" }
            . ([scriptblock]::Create((Resolve-WidgetFunctionText $fn.Extent.Text)))
        }
        $script:S = @{ palette = 'teal'; opacity = .96 }; $script:C = Get-Palette $false; $script:brushCache = @{}; $script:backgroundSurfaces = New-Object Collections.ArrayList
        function Set-CardOpacity($value) {}
        function Save-Settings {}
        $UI = @{ tiers = @{} }; $stack = New-Object Windows.Controls.StackPanel
        # أقسام المصدر الفعلية (بلا معالجات خارجية): الرأس (الإشعار والتنبيهات)، شريط التأكيد، منزلقة العتامة
        $section = { param($from, $to) $a = $source.IndexOf($from); if ($a -lt 0) { throw "marker $from" }; $b = $source.IndexOf($to, $a); if ($b -lt 0) { throw "marker $to" }; $source.Substring($a, $b - $a) }
        . ([scriptblock]::Create((& $section '# ---- header' '# ---- rings')))
        . ([scriptblock]::Create((& $section '# ---- inline confirm strip' '# ---- memory trim button')))
        . ([scriptblock]::Create((& $section '# opacity slider 10%..100%' '$opItem = New-Object')))
        $AP = [Windows.Automation.AutomationProperties]
    }
    It 'الحلقة تحمل اسمها وتقرأ القيمة والوحدة والتلميح بعد Set-Ring' {
        $r = New-Ring 'الذاكرة الفعلية'
        $AP::GetName($r.Val) | Should -Be 'الذاكرة الفعلية'
        Set-Ring $r 47 'ok' '47' '%' 'متاح 16 GB من 32 GB'
        $AP::GetName($r.Val) | Should -Be 'الذاكرة الفعلية: 47 %'
        $AP::GetHelpText($r.Val) | Should -Be 'متاح 16 GB من 32 GB'
        $r.Root.ToolTip | Should -Be 'متاح 16 GB من 32 GB'
    }
    It 'الرقاقة تقرأ «التسمية: القيمة»' {
        $chip = New-Chip 'شلات'
        $AP::GetName($chip.Val) | Should -Be 'شلات'
        Set-Chip $chip '5'
        $chip.Val.Text | Should -Be '5'
        $AP::GetName($chip.Val) | Should -Be 'شلات: 5'
    }
    It 'الإشعار وتحذير المظهر مناطق حية مهذّبة، وتنبيه المعالج وسؤال التأكيد حازمان' {
        $AP::GetLiveSetting($UI.toast) | Should -Be 'Polite'
        $AP::GetLiveSetting($UI.appearanceWarning) | Should -Be 'Polite'
        $AP::GetLiveSetting($UI.cpuAlert) | Should -Be 'Assertive'
        $AP::GetLiveSetting($UI.confirmText) | Should -Be 'Assertive'
    }
    It 'منزلقة العتامة لها اسم ونص مساعد، وأزرار الرأس مسمّاة' {
        $AP::GetName($opSl) | Should -Be 'عتامة الويدجت'
        $AP::GetHelpText($opSl) | Should -Not -BeNullOrEmpty
        $AP::GetName($UI.menuBtn) | Should -Be 'خيارات الويدجت'
        $AP::GetName($UI.closeBtn) | Should -Be 'إخفاء الويدجت'
    }
}
