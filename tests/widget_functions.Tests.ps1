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
    foreach ($name in @('Alpha', 'Restore-WidgetPriority', 'Get-WidgetTickGap', 'Normalize-WidgetOpacity', 'Get-WidgetKeyAction', 'Test-WidgetOpacityLow', 'Lvl', 'Fmt-Bytes', 'Get-SummaryLevel', 'Test-AppearanceRecord', 'Get-WidgetPlacement', 'Get-WidgetLayoutMode', 'Test-WidgetLayoutFlipAllowed', 'Add-WidgetLayoutFlip', 'Get-Palette', 'Get-DriveLevel')) {
        $fn = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }.GetNewClosure(), $true)
        if (-not $fn) { throw "Missing $name" }
        . ([scriptblock]::Create((Resolve-WidgetFunctionText $fn.Extent.Text)))
    }
    $script:S = @{ palette = 'teal'; opacity = .25 }
    function Rect([double]$l, [double]$t, [double]$w, [double]$h) { [Windows.Rect]::new($l, $t, $w, $h) }
}

Describe 'إتمام الطلبات الخلفية' {
    It 'لا يُعدّ ردّ 428 الملتقط فشلًا: HadErrors ترتفع والمجرى فارغ' {
        $ps = [powershell]::Create()
        [void]$ps.AddScript({ try { Get-Item 'Z:\غير-موجود' -ErrorAction Stop } catch { 'ok' } })
        @($ps.Invoke())[0] | Should -Be 'ok'
        $ps.Streams.Error.Count | Should -Be 0
        $ps.Dispose()
        $source | Should -Not -Match 'HadErrors\s*\)'
        $source | Should -Match 'Streams\.Error\.Count -gt 0'
    }
}

Describe 'حلقات الأقراص الإضافية' {
    It 'مستوى القرص بنسبة المستخدم: تحذير عند 85 وخطر عند 93' {
        Get-DriveLevel 29.7 | Should -Be 'ok'
        Get-DriveLevel 84.9 | Should -Be 'ok'
        Get-DriveLevel 85 | Should -Be 'warn'
        Get-DriveLevel 93 | Should -Be 'bad'
    }
    It 'التحديث يستدعي Update-DriveRings بأقراص m.drives قبل الـGPU' {
        $source | Should -Match 'Update-DriveRings \$m\.drives\s+Update-Gpu'
    }
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

Describe 'Get-WidgetKeyAction (لوحة مفاتيح الويدجت)' {
    It 'Esc يخفي الويدجت والقائمة مغلقة، ويغلق القائمة إن كانت مفتوحة' {
        Get-WidgetKeyAction 'Escape' $false | Should -Be 'hide'
        Get-WidgetKeyAction 'Escape' $true | Should -Be 'closeMenu'
    }
    It 'Apps وF10 يفتحان القائمة، وسائر المفاتيح لا تُعالَج' {
        Get-WidgetKeyAction 'Apps' $false | Should -Be 'menu'
        Get-WidgetKeyAction 'F10' $false | Should -Be 'menu'
        Get-WidgetKeyAction 'Tab' $false | Should -Be ''
        Get-WidgetKeyAction 'Enter' $true | Should -Be ''
    }
}

Describe 'Test-WidgetOpacityLow (حدّ وضوح النص 35%)' {
    It 'دون 35% منخفضة، ومن 35% فما فوق لا' {
        Test-WidgetOpacityLow 0.1 | Should -BeTrue
        Test-WidgetOpacityLow '0.34' | Should -BeTrue
        Test-WidgetOpacityLow 0.35 | Should -BeFalse
        Test-WidgetOpacityLow 0.96 | Should -BeFalse
    }
    It 'القيم غير الصالحة تُطبَّع إلى 0.96 فلا تُعدّ منخفضة' { Test-WidgetOpacityLow 'abc' | Should -BeFalse }
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
        $t['--f-list'] | Should -Match '^"Zain"'
        $t['--f-ui'] | Should -Match '^"Zain"'
        $t['--f-num'] | Should -Match '^"Outfit"'
    }
    It 'يحمّل الأدوار الأربعة من assets\fonts بعائلاتها وأوزانها (Zain 400/700 للعناوين والقوائم والنص، Outfit 400/600 للأرقام)' {
        $f = Get-IdentityFonts $script:widgetRepoRoot
        $f.loaded.head | Should -Be 'Zain'
        $f.loaded.list | Should -Be 'Zain'
        $f.loaded.ui | Should -Be 'Zain'
        $f.loaded.num | Should -Be 'Outfit'
        foreach ($case in @(@('head', 'Bold', 'Zain-Bold.ttf'), @('head', 'Normal', 'Zain-Regular.ttf'), @('list', 'Normal', 'Zain-Regular.ttf'), @('ui', 'Normal', 'Zain-Regular.ttf'), @('ui', 'SemiBold', 'Zain-Bold.ttf'), @('num', 'Normal', 'Outfit-Regular.ttf'), @('num', 'SemiBold', 'Outfit-SemiBold.ttf'))) {
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

Describe 'app_shell.ps1: نسخة واحدة من نافذة التطبيق' {
    BeforeAll {
        $shellSource = Get-Content -LiteralPath (Join-Path $script:widgetRepoRoot 'app_shell.ps1') -Raw
        $shellTokens = $null; $shellErrors = $null
        $shellAst = [Management.Automation.Language.Parser]::ParseInput($shellSource, [ref]$shellTokens, [ref]$shellErrors)
        if ($shellErrors.Count) { throw ($shellErrors.Message -join '; ') }
        foreach ($name in @('Test-AppWindowCandidate', 'Get-SingleInstanceAction')) {
            $fn = $shellAst.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }.GetNewClosure(), $true)
            if (-not $fn) { throw "Missing $name" }
            . ([scriptblock]::Create($fn.Extent.Text))
        }
    }
    It 'نافذة التطبيق: العنوان حرفيًا ومضيف معروف وليست نافذة العملية نفسها' {
        Test-AppWindowCandidate 'مرصد الجهاز' 'مرصد الجهاز' 'powershell' 10 99 | Should -BeTrue
        Test-AppWindowCandidate 'مرصد الجهاز' 'مرصد الجهاز' 'MSEdge' 10 99 | Should -BeTrue
        Test-AppWindowCandidate 'مرصد الجهاز' 'مرصد الجهاز' 'pwsh' 10 99 | Should -BeTrue
        Test-AppWindowCandidate 'مرصد الجهاز' 'مرصد الجهاز' 'pwsh' 99 99 | Should -BeFalse
        Test-AppWindowCandidate 'مرصد الجهاز' 'مرصد الجهاز — الويدجت' 'pwsh' 10 99 | Should -BeFalse
        Test-AppWindowCandidate 'مرصد الجهاز' 'مرصد الجهاز' 'notepad' 10 99 | Should -BeFalse
        Test-AppWindowCandidate '' '' 'pwsh' 10 99 | Should -BeFalse
    }
    It 'القرار: تُفتح نافذة واحدة فقط، والنسخة الثانية تُبرز القائمة' {
        Get-SingleInstanceAction $true $false $false | Should -Be 'open'
        Get-SingleInstanceAction $false $true $false | Should -Be 'activate'
        Get-SingleInstanceAction $false $false $false | Should -Be 'wait'
        Get-SingleInstanceAction $true $true $false | Should -Be 'activate'   # نافذة Edge أو غلاف أقدم لا يحملان القفل
    }
    It 'إعادة تشغيل الخادم (-Replace) تستبدل النافذة القائمة لأن جلستها ماتت' {
        Get-SingleInstanceAction $false $true $true | Should -Be 'replace'
        Get-SingleInstanceAction $true $true $true | Should -Be 'replace'
        Get-SingleInstanceAction $true $false $true | Should -Be 'open'
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
    It 'التكبير يحدّه WM_GETMINMAXINFO بمنطقة العمل فلا يغطي شريط المهام، والخطّاف مسجّل على النافذة' {
        $cs = $shellAst.Find({ param($n) $n -is [Management.Automation.Language.StringConstantExpressionAst] -and $n.Value -match 'class MaxFit' }, $true).Value
        if (-not ('MaxFit' -as [type])) { Add-Type -TypeDefinition $cs }
        $buf = [Runtime.InteropServices.Marshal]::AllocHGlobal(40)
        try {
            for ($i = 0; $i -lt 40; $i += 4) { [Runtime.InteropServices.Marshal]::WriteInt32($buf, $i, 0) }
            $handled = $false
            [void][MaxFit]::Hook([IntPtr]::Zero, 0x0005, [IntPtr]::Zero, $buf, [ref]$handled)   # رسالة أخرى لا تمسّ البنية
            [Runtime.InteropServices.Marshal]::ReadInt32($buf, 8) | Should -Be 0
            [void][MaxFit]::Hook([IntPtr]::Zero, 0x0024, [IntPtr]::Zero, $buf, [ref]$handled)   # الشاشة الأقرب: الرئيسية
            $w = [Runtime.InteropServices.Marshal]::ReadInt32($buf, 8); $h = [Runtime.InteropServices.Marshal]::ReadInt32($buf, 12)
            $w | Should -BeGreaterThan 0
            $h | Should -BeGreaterThan 0
            [Runtime.InteropServices.Marshal]::ReadInt32($buf, 16) | Should -BeGreaterOrEqual 0
            [Runtime.InteropServices.Marshal]::ReadInt32($buf, 20) | Should -BeGreaterOrEqual 0
            $handled | Should -BeFalse   # WPF يطبّق MinWidth/MinHeight بعده
        } finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($buf) }
        $shellSource | Should -Match 'Add_SourceInitialized\(\{ \[Windows\.Interop\.HwndSource\]::FromHwnd\(.*\)\.AddHook\(\$script:maxHook\)'
    }
}

Describe 'إتاحة الويدجت: AutomationProperties وLiveSetting (WPF معزول، بلا نافذة)' {
    BeforeAll {
        Add-Type -AssemblyName PresentationFramework
        foreach ($name in @('Brush', 'Surface-Brush', 'New-Text', 'New-Card', 'Gap', 'New-Ring', 'Ring-Arc', 'Set-Ring', 'Level-Brush', 'New-Chip', 'Set-Chip', 'New-ActionButton', 'Normalize-WidgetOpacity', 'Test-WidgetOpacityLow', 'Get-ChartSummary')) {
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
        # ما يقرؤه قارئ الشاشة فعلًا: أسماء نظائر الأتمتة تحت العنصر بعد تخطيطه
        function Get-PeerNames($peer) { foreach ($c in @($peer.GetChildren())) { if ($c) { $c.GetName(); Get-PeerNames $c } } }
        function Get-ReadNames($element) {
            $uc = New-Object Windows.Controls.UserControl; $uc.Content = $element
            $uc.Measure([Windows.Size]::new(400, 400)); $uc.Arrange([Windows.Rect]::new(0, 0, 400, 400)); $uc.UpdateLayout()
            @(Get-PeerNames ([Windows.Automation.Peers.UIElementAutomationPeer]::CreatePeerForElement($uc)))
        }
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
    It 'كل مقياس ورقاقة عنصر واحد لقارئ الشاشة، والوحدة والتسمية مرسومتان بلا تكرار' {
        $r = New-Ring 'الذاكرة الفعلية'; Set-Ring $r 47 'ok' '47' '%' 'متاح 16 GB من 32 GB'
        $names = @(Get-ReadNames $r.Root)
        $names | Should -HaveCount 1
        $names[0] | Should -Be 'الذاكرة الفعلية: 47 %'
        $r.Unit.ActualWidth | Should -BeGreaterThan 0
        $quiet = @($r.Root.Children | Where-Object { $_ -is [QuietText] })
        $quiet | Should -HaveCount 1
        $quiet[0].Text | Should -Be 'الذاكرة الفعلية'
        $quiet[0].ActualWidth | Should -BeGreaterThan 0
        $chip = New-Chip 'شلات'; Set-Chip $chip '5'
        $names = @(Get-ReadNames $chip.Root)
        $names | Should -HaveCount 1
        $names[0] | Should -Be 'شلات: 5'
    }
    It 'البديل النصي للرسم: مدى كل سلسلة وآخر قيمة بتقريب الحلقات، أو «جارٍ جمع البيانات»' {
        Get-ChartSummary @(60.4, 66.2, 64) @(55, $null, 61.4) | Should -Be 'آخر 30 دقيقة: التزام الذاكرة بين 60% و66%، الآن 64%؛ الذاكرة الفعلية بين 55% و61%، الآن 61%'
        Get-ChartSummary @(70, 72) @(50) | Should -Be 'آخر 30 دقيقة: التزام الذاكرة بين 70% و72%، الآن 72%'
        Get-ChartSummary @(70) $null | Should -Be 'آخر 30 دقيقة: جارٍ جمع البيانات'
        Get-ChartSummary $null $null | Should -Be 'آخر 30 دقيقة: جارٍ جمع البيانات'
    }
    It 'الإشعار وتحذير المظهر مناطق حية مهذّبة، وتنبيه المعالج وسؤال التأكيد حازمان' {
        $AP::GetLiveSetting($UI.toast) | Should -Be 'Polite'
        $AP::GetLiveSetting($UI.appearanceWarning) | Should -Be 'Polite'
        $AP::GetLiveSetting($UI.cpuAlert) | Should -Be 'Assertive'
        $AP::GetLiveSetting($UI.confirmText) | Should -Be 'Assertive'
    }
    It 'التقليص العميق يسأل في طبقة التأكيد داخل الويدجت لا في نافذة نظام' {
        $source | Should -Not -Match '\[Windows\.MessageBox\]'
        . ([scriptblock]::Create((& $section 'function Set-ConfirmButtons' 'function Start-Clean')))
        . ([scriptblock]::Create((& $section 'function Start-Trim' '# الزر يفتح قائمة')))
        $script:trimJob = $null; $UI.confirm.Visibility = 'Collapsed'
        Start-Trim 'deep'
        $UI.confirm.Visibility | Should -Be 'Visible'
        $UI.confirmText.Text | Should -Be 'الوضع العميق قد يجمّد الجهاز لدقائق. متابعة؟'
        $AP::GetName($UI.cfReal) | Should -Be 'متابعة'
        $UI.cfDry.Visibility | Should -Be 'Collapsed'
        Set-ConfirmButtons 'تنفيذ فعلي' $true
        $AP::GetName($UI.cfReal) | Should -Be 'تنفيذ فعلي'
        $UI.cfDry.Visibility | Should -Be 'Visible'
    }
    It 'قالب أزرار الإجراء فيه حالة تمرير (IsMouseOver) إلى جانب حالة التركيز' {
        foreach ($toggle in @($false, $true)) {
            $b = New-ActionButton 'اختبار' $toggle
            $props = @($b.Template.Triggers | ForEach-Object { $_.Property.Name })
            $props | Should -Contain 'IsMouseOver'
            $props | Should -Contain 'IsKeyboardFocused'
            $hover = $b.Template.Triggers | Where-Object { $_.Property.Name -eq 'IsMouseOver' }
            @($hover.Setters | Where-Object { $_.TargetName -eq 'Frame' -and $_.Property.Name -eq 'BorderBrush' }).Count | Should -Be 1
        }
    }
    It 'تنبيه حدّ العتامة يظهر دون 35% ويختفي فوقه ويُعلَن كمنطقة حية' {
        $AP::GetLiveSetting($opHint) | Should -Be 'Polite'
        $opHint.Text | Should -Match '35%'
        $opSl.Value = 0.96; $opHint.Visibility | Should -Be 'Collapsed'
        $opSl.Value = 0.2; $opHint.Visibility | Should -Be 'Visible'
        $opSl.Value = 0.35; $opHint.Visibility | Should -Be 'Collapsed'
    }
    It 'منزلقة العتامة لها اسم ونص مساعد، وأزرار الرأس مسمّاة' {
        $AP::GetName($opSl) | Should -Be 'عتامة الويدجت'
        $AP::GetHelpText($opSl) | Should -Not -BeNullOrEmpty
        $AP::GetName($UI.menuBtn) | Should -Be 'خيارات الويدجت'
        $AP::GetName($UI.closeBtn) | Should -Be 'إخفاء الويدجت'
    }
}

Describe 'أولوية عملية الويدجت' {
    It 'ترفع صنف المجدول (أقل من العادي أو خامل) إلى العادي وتعيد الصنف السابق' {
        foreach ($c in 'BelowNormal', 'Idle') {
            $p = [pscustomobject]@{ PriorityClass = $c }
            Restore-WidgetPriority $p | Should -Be $c
            $p.PriorityClass | Should -Be 'Normal'
        }
    }
    It 'لا تمسّ صنفًا عاديًّا أو أعلى' {
        foreach ($c in 'Normal', 'AboveNormal', 'High') {
            $p = [pscustomobject]@{ PriorityClass = $c }
            Restore-WidgetPriority $p | Should -BeNullOrEmpty
            $p.PriorityClass | Should -Be $c
        }
    }
    It 'تُستدعى عند بدء الويدجت على العملية الحالية' {
        $source | Should -Match 'Restore-WidgetPriority \(\[Diagnostics\.Process\]::GetCurrentProcess\(\)\)'
    }
}

Describe 'كاشف تجمّد الواجهة: السكون ليس تجمّدًا' {
    It 'سكون الجهاز بين نبضتين: لا تجمّد، ويُسجَّل زمن السكون' {
        $g = Get-WidgetTickGap 5074.1 1.0
        $g.stall | Should -Be 0
        $g.slept | Should -BeGreaterThan 5073
    }
    It 'تجمّد حقيقي والجهاز يقظ: يُحسب بزمن اليقظة' {
        $g = Get-WidgetTickGap 30.2 30.2
        $g.stall | Should -Be 30.2
        $g.slept | Should -Be 0
    }
    It 'تجمّد ثم سكون في الفجوة نفسها: يُسجَّلان معًا كلٌّ بمقداره' {
        $g = Get-WidgetTickGap 1000 40
        $g.stall | Should -Be 40
        $g.slept | Should -Be 960
    }
    It 'نبضة عادية: لا شيء، وحدّ 5 ثوانٍ غير شامل' {
        $g = Get-WidgetTickGap 1.0 1.0
        $g.stall | Should -Be 0
        $g.slept | Should -Be 0
        (Get-WidgetTickGap 5 5).stall | Should -Be 0
    }
    It 'بلا زمن يقظة: يبقى القياس بساعة الحائط' {
        $g = Get-WidgetTickGap 12 $null
        $g.stall | Should -Be 12
        $g.slept | Should -Be 0
    }
    It 'النبضة تقيس بزمن اليقظة من QueryUnbiasedInterruptTime' {
        $source | Should -Match 'QueryUnbiasedInterruptTime\(out ulong t\)'
        $source | Should -Match 'Get-WidgetTickGap \(\$tickAt - \$script:lastTickAt\)\.TotalSeconds'
        $source | Should -Match '\$script:lastAwakeAt = \$awakeAt'
    }
}

Describe 'set_backend_privilege.ps1: يغيّر مستوى التشغيل وحده' {
    BeforeAll {
        $script:bpPath = Join-Path $script:widgetRepoRoot 'set_backend_privilege.ps1'
        $script:bpText = Get-Content -LiteralPath $script:bpPath -Raw
        $t = $null; $e = $null
        $script:bpAst = [Management.Automation.Language.Parser]::ParseFile($script:bpPath, [ref]$t, [ref]$e)
        $script:bpErrors = $e
        $fn = $script:bpAst.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Set-TaskRunLevelXml' }, $true)
        . ([scriptblock]::Create($fn.Extent.Text))
        function TaskXml([string]$principal) {
            '<?xml version="1.0" encoding="UTF-16"?><Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Triggers><LogonTrigger><Delay>PT30S</Delay></LogonTrigger></Triggers>' +
            '<Principals><Principal id="Author"><UserId>S-1-5-21-1</UserId><LogonType>InteractiveToken</LogonType>' + $principal + '</Principal></Principals>' +
            '<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><Hidden>true</Hidden></Settings><Actions Context="Author"><Exec><Command>pythonw.exe</Command><Arguments>"server.py"</Arguments></Exec></Actions></Task>'
        }
        function Level([string]$xml) { ([xml]$xml).Task.Principals.Principal.RunLevel }
    }
    It 'يبدأ بـ BOM ويُحلَّل بلا أخطاء، وكذلك restart_agent.ps1' {
        $script:bpErrors.Count | Should -Be 0
        foreach ($f in $script:bpPath, (Join-Path $script:widgetRepoRoot 'restart_agent.ps1')) {
            (([IO.File]::ReadAllBytes($f)[0..2] | ForEach-Object { $_.ToString('x2') }) -join '') | Should -Be 'efbbbf'
            $t = $null; $e = $null
            [void][Management.Automation.Language.Parser]::ParseFile($f, [ref]$t, [ref]$e)
            $e.Count | Should -Be 0
        }
    }
    It 'يضيف RunLevel حين يغيب، ويبدّله حين يوجد، في الاتجاهين' {
        Level (Set-TaskRunLevelXml (TaskXml '') 'Highest') | Should -Be 'HighestAvailable'
        Level (Set-TaskRunLevelXml (TaskXml '<RunLevel>HighestAvailable</RunLevel>') 'Limited') | Should -Be 'LeastPrivilege'
        Level (Set-TaskRunLevelXml (TaskXml '<RunLevel>LeastPrivilege</RunLevel>') 'Highest') | Should -Be 'HighestAvailable'
    }
    It 'لا يمسّ الأمر ولا الوسائط ولا المشغّلات ولا الإعدادات' {
        $in = TaskXml ''
        $out = Set-TaskRunLevelXml $in 'Highest'
        $out.Replace('<RunLevel>HighestAvailable</RunLevel>', '') | Should -Be $in.Replace('<?xml version="1.0" encoding="UTF-16"?>', ([xml]$in).FirstChild.OuterXml)
        ([xml]$out).Task.Triggers.LogonTrigger.Delay | Should -Be 'PT30S'
        ([xml]$out).Task.Actions.Exec.Arguments | Should -Be '"server.py"'
    }
    It 'يرفض تعريفًا بلا مبدأ واحد' {
        { Set-TaskRunLevelXml '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Principals /></Task>' 'Highest' } | Should -Throw
    }
    It 'يشمل المهام الثلاث، ينسخ قبل التسجيل، يرفع نفسه بلا نافذة، ولا يضع الرمز في رابط' {
        foreach ($n in 'MonitorWidget-Server-Logon', 'MonitorWidget-Agent', 'MonitorWidget-Collector') { $script:bpText | Should -Match ([regex]::Escape("'$n'")) }
        $script:bpText.IndexOf('before_{2}.xml') | Should -BeLessThan $script:bpText.IndexOf('Register-ScheduledTask')
        # التسجيل قبل الإيقاف: وإلا شغّل الويدجت مهمة الوكيل بالتعريف القديم في الفجوة (حدث حيًّا 13/11)
        $script:bpText.IndexOf('Register-ScheduledTask') | Should -BeLessThan $script:bpText.IndexOf('Stop-ScheduledTask')
        $script:bpText | Should -Match '-Verb RunAs -WindowStyle Hidden -Wait -PassThru'
        $script:bpText | Should -Match "Headers @\{ 'X-Token' = \`$tok \}"
        $script:bpText | Should -Not -Match '\?(k|token)='
        $script:bpText | Should -Match '\[bool\]\$m\.admin -ne \(\$Level -eq ''Highest''\)'
    }
    It 'restart_agent.ps1 يوقف المهمتين، يرفع نفسه إن نجت العملية القديمة، ولا يدّعي نجاحًا بلا مستمع جديد' {
        $ra = Get-Content -LiteralPath (Join-Path $script:widgetRepoRoot 'restart_agent.ps1') -Raw
        $ra | Should -Match "'MonitorWidget-Agent', 'MonitorWidget-Server-Logon'"
        $ra | Should -Match '\$alive -and -not \$isAdmin -and -not \$Elevated'
        # رقم العملية من healthz لا من Get-NetTCPConnection: الأخير يعيد 0 لخادم مرتفع من جلسة عادية (إنذار كاذب حيًّا 13/11)
        foreach ($txt in $ra, $script:bpText) { $txt | Should -Match '/api/healthz'; $txt | Should -Not -Match 'Get-NetTCPConnection -LocalPort' }
        $ra | Should -Match '\$after -eq \$before'
        $ra | Should -Match '-Verb RunAs -WindowStyle Hidden -Wait -PassThru'
        $ra | Should -Not -Match "(?m)^'agent restarted'\s*$"
        $ra | Should -Match '(?m)^\s*exit 1\s*$'
    }
}

Describe 'restart_widget.ps1: لا يدّعي إعادة تشغيل لم تحدث' {
    BeforeAll {
        $script:rwPath = Join-Path $script:widgetRepoRoot 'restart_widget.ps1'
        $script:rwText = Get-Content -LiteralPath $script:rwPath -Raw
    }
    It 'يبدأ بـ BOM ليُحلَّل نصّه العربي في Windows PowerShell 5 أيضًا' {
        $head = [IO.File]::ReadAllBytes($script:rwPath)[0..2]
        ($head | ForEach-Object { $_.ToString('x2') }) -join '' | Should -Be 'efbbbf'
    }
    It 'يُحلَّل بلا أخطاء صياغة' {
        $t = $null; $e = $null
        [void][Management.Automation.Language.Parser]::ParseFile($script:rwPath, [ref]$t, [ref]$e)
        $e.Count | Should -Be 0
    }
    It 'يرفع نفسه حين تكون مهمة الويدجت مرتفعة والجلسة عادية، بلا نافذة' {
        $script:rwText | Should -Match "RunLevel -eq 'Highest' -and -not \`$isAdmin"
        $script:rwText | Should -Match '-Verb RunAs -WindowStyle Hidden -Wait -PassThru'
    }
    It 'لا يطبع النجاح إلا بعد ظهور عملية جديدة أو نجاح النسخة المرتفعة، ويخرج بـ1 عند الفشل' {
        $script:rwText | Should -Not -Match "(?m)^'widget restarted'\s*$"
        $script:rwText | Should -Match "'widget restarted \(pid '"
        $script:rwText | Should -Match 'ProcessId -notin \$oldIds'
        $script:rwText | Should -Match '(?m)^\s*exit 1\s*$'
    }
}

Describe 'تخطيط الويدجت عند الإقلاع: تباطؤ وقاطع تأرجح' {
    It 'القرار الأصلي دون تغيّر: عمودان تحت الأقصى من 1150 وطول المحتوى + 12، وإلا عمود واحد' {
        Get-WidgetLayoutMode 1232 1100 $false | Should -BeFalse   # 1232 فوق 1150 والمحتوى 1112
        Get-WidgetLayoutMode 1140 900 $false | Should -BeTrue     # تحت 1150
        Get-WidgetLayoutMode 1232 1230 $false | Should -BeTrue    # المحتوى + 12 = 1242 فوق منطقة العمل
        Get-WidgetLayoutMode 1232 1220 $false | Should -BeFalse   # المحتوى + 12 = 1232 يساوي منطقة العمل
    }
    It 'العودة من عمودين إلى عمود تحتاج هامشًا، فمنطقة العمل التي تتأرجح حول الحدّ لا تبدّل الوضع' {
        # المحتوى 1230 فالحدّ 1242: عند 1232 يبقى العمودان إن كان الوضع الحالي عمودين، ولا ينتقل إليهما إن كان عمودًا واحدًا إلا تحت 1242
        Get-WidgetLayoutMode 1232 1230 $true | Should -BeTrue
        Get-WidgetLayoutMode 1270 1230 $true | Should -BeTrue     # ما زال دون 1242 + 40
        Get-WidgetLayoutMode 1283 1230 $true | Should -BeFalse    # فوق الحدّ والهامش: يعود إلى عمود
        # تتابع منطقة عمل متأرجحة (1280 ثم 1232 وهكذا) لا يقلب الوضع أكثر من مرة
        $mode = $false; $flips = 0
        foreach ($h in 1280, 1232, 1280, 1232, 1280, 1232, 1280, 1232) {
            $next = Get-WidgetLayoutMode $h 1230 $mode
            if ($next -ne $mode) { $flips++ }
            $mode = $next
        }
        $flips | Should -Be 1
    }
    It 'قاطع التأرجح: يسمح بأقل من 4 تبديلات في 10 ثوانٍ ثم يمنع، ويستعيد بعد انقضاء النافذة' {
        $now = Get-Date
        Test-WidgetLayoutFlipAllowed @() $now | Should -BeTrue
        Test-WidgetLayoutFlipAllowed @($now.AddSeconds(-1), $now.AddSeconds(-2), $now.AddSeconds(-3)) $now | Should -BeTrue
        Test-WidgetLayoutFlipAllowed @($now.AddSeconds(-1), $now.AddSeconds(-2), $now.AddSeconds(-3), $now.AddSeconds(-4)) $now | Should -BeFalse
        Test-WidgetLayoutFlipAllowed @($now.AddSeconds(-30), $now.AddSeconds(-31), $now.AddSeconds(-32), $now.AddSeconds(-33)) $now | Should -BeTrue
    }
    It 'انتظار الشِّل قبل إظهار النافذة، وسجلّ البدء والخروج وتوقف الواجهة موجودة في المصدر' {
        $src = Get-Content -LiteralPath (Join-Path $script:widgetRepoRoot 'widget.ps1') -Raw
        $src.IndexOf('Wait-WidgetShellReady 45 2') | Should -BeLessThan $src.IndexOf('$app.Run($win)')
        $src.IndexOf('Wait-WidgetShellReady 45 2') | Should -BeGreaterThan 0
        $src | Should -Match "Log \`$\(if \(\`$script:userExit\) \{ 'exit: user' \} else \{ 'exit' \}\)"
        ([regex]::Matches($src, '\$script:userExit = \$true; \$win\.Close\(\)')).Count | Should -Be 3   # القائمة وأيقونة الصينية ووضع الاختبار MW_SMOKE
        $src | Should -Match 'ui stall'
        $src | Should -Match "Log \('layout: "
    }
}

Describe 'سجلّ تبديلات التخطيط: أول تبديل لا يُسقط الويدجت' {
    It 'القائمة الفارغة ($null) عند أول تبديل: لا استثناء، والناتج تبديل واحد' {
        $now = Get-Date
        { Add-WidgetLayoutFlip $null $now } | Should -Not -Throw
        $r = Add-WidgetLayoutFlip $null $now
        @($r).Count | Should -Be 1
        $r[0] | Should -Be $now
        { Test-WidgetLayoutFlipAllowed $null $now } | Should -Not -Throw
        Test-WidgetLayoutFlipAllowed $null $now | Should -BeTrue
    }
    It 'يُسقط التبديلات الأقدم من النافذة ويبقي الحديثة مع الحالي' {
        $now = Get-Date
        $r = Add-WidgetLayoutFlip @($now.AddSeconds(-30), $now.AddSeconds(-3)) $now
        @($r).Count | Should -Be 2
        $r[-1] | Should -Be $now
    }
    It 'تتابع التبديلات كما في Set-RestoredLayout: 4 مسموحة ثم يمنع القاطع الخامس' {
        $now = Get-Date; $flips = $null; $allowed = 0
        foreach ($i in 0..5) {
            $t = $now.AddMilliseconds(200 * $i)
            if (Test-WidgetLayoutFlipAllowed $flips $t) { $flips = Add-WidgetLayoutFlip $flips $t; $allowed++ }
        }
        $allowed | Should -Be 4
    }
    It 'المصدر لا يطرح تاريخًا من عنصر غير مفحوص في مسار التبديل' {
        $src = Get-Content -LiteralPath (Join-Path $script:widgetRepoRoot 'widget.ps1') -Raw
        $src | Should -Match 'layoutFlips=Add-WidgetLayoutFlip \$script:layoutFlips \$now'
        $src | Should -Not -Match 'layoutFlips\|Where-Object \{\(\$now-\$_\)'
    }
}

Describe 'مسار تبديل التخطيط كما هو في Set-RestoredLayout (مقتطع من المصدر ومُنفَّذ)' {
    It 'يعمل من حالة فارغة، يسجّل كل تبديل، ويجمّد الوضع بعد 4 تبديلات متتابعة' {
        $src = Get-Content -LiteralPath (Join-Path $script:widgetRepoRoot 'widget.ps1') -Raw
        $m = [regex]::Match($src, '(?s)\$wasWide=\[bool\]\$script:layoutWide;.*?\$script:layoutWide=\$wide;')
        $m.Success | Should -BeTrue
        $step = [scriptblock]::Create($m.Value)
        $script:layoutWide = $null; $script:layoutFlips = $null; $script:layoutFrozenLogged = $null; $script:flipLog = @()
        function Log($text) { $script:flipLog += [string]$text }
        $win = $null; $natural = 1230.0; $modes = @()
        foreach ($height in 1000, 1400, 1000, 1400, 1000, 1400) { { . $step } | Should -Not -Throw; $modes += [bool]$script:layoutWide }
        $modes | Should -Be @($true, $false, $true, $false, $false, $false)
        @($script:flipLog | Where-Object { $_ -like 'layout: wide 726*' }).Count | Should -Be 2
        @($script:flipLog | Where-Object { $_ -like 'layout: portrait 385*' }).Count | Should -Be 2
        @($script:flipLog | Where-Object { $_ -like '*flip loop stopped*' }).Count | Should -Be 1
    }
}

Describe 'حارس المعالج في الويدجت: زر «اخفض أولويتها»' {
    BeforeAll { $script:cpuSrc = Get-Content -LiteralPath (Join-Path $script:widgetRepoRoot 'widget.ps1') -Raw }
    It 'الزر مسمّى لقارئ الشاشة، مخفيّ افتراضيًا، ويُعاد إلى المكدّس مع التنبيه في التخطيط المستعاد' {
        $script:cpuSrc | Should -Match "\`$UI\.cpuLower = New-ActionButton 'اخفض أولوية العملية التي تستهلك المعالج'"
        $script:cpuSrc | Should -Match "\`$UI\.cpuLower\.Visibility = 'Collapsed'; \`$stack\.Children\.Add\(\`$UI\.cpuLower\)"
        $script:cpuSrc | Should -Match '@\(\$hdr,\$UI\.battAlert,\$UI\.cpuAlert,\$UI\.cpuLower,\$UI\.appearanceWarning\)'
    }
    It 'الضغط يرسل اسم العملية المنبَّه إليها إلى /api/cpu-lower ويعرض ردّ الخادم' {
        $script:cpuSrc | Should -Match "Start-Bg '/api/cpu-lower' @\{ name = \`$target \}"
        $script:cpuSrc | Should -Match 'Show-Toast \(\[string\]\$r\.text\)'
    }
    It 'منطق الإظهار كما في Update-Widget: يظهر لغير المحمي وغير المخفّض، ويختفي مع زوال التنبيه' {
        $m = [regex]::Match($script:cpuSrc, '(?s)\$ca = \$d\.cpuAlert.*?\$script:cpuAlertName = \$null \}')
        $m.Success | Should -BeTrue
        $step = [scriptblock]::Create($m.Value)
        function Ar-Minutes($n) { "$n دقائق" }
        $UI = @{ cpuAlert = [pscustomobject]@{ Text = ''; Visibility = '' }; cpuLower = [pscustomobject]@{ Visibility = '' } }
        $worst = 'ok'
        $d = [pscustomobject]@{ cpuAlert = [pscustomobject]@{ name = 'python.exe'; avg = 12.7; minutes = 10; canLower = $true; lowered = $false } }
        . $step
        $UI.cpuLower.Visibility | Should -Be 'Visible'; $script:cpuAlertName | Should -Be 'python.exe'; $worst | Should -Be 'warn'
        $d.cpuAlert.lowered = $true; . $step
        $UI.cpuLower.Visibility | Should -Be 'Collapsed'; $UI.cpuAlert.Text | Should -Match 'أولويتها مخفّضة'
        $d.cpuAlert.lowered = $false; $d.cpuAlert.canLower = $false; . $step
        $UI.cpuLower.Visibility | Should -Be 'Collapsed'
        $d = [pscustomobject]@{ cpuAlert = $null }; . $step
        $UI.cpuAlert.Visibility | Should -Be 'Collapsed'; $UI.cpuLower.Visibility | Should -Be 'Collapsed'; $script:cpuAlertName | Should -BeNullOrEmpty
    }
}

Describe 'تشغيل الويدجت الحقيقي (MW_SMOKE): widget.ps1 كله يعمل، يبدّل التخطيط، ويُغلق بلا fatal' {
    # اختبارات الدوال المنفردة فاتها سطر أسقط الويدجت عند أول تبديل تخطيط (PR 128)؛ هذا يشغّل السكربت نفسه في عملية مستقلة
    # بقفل آخر ومجلد حالة مؤقت ونافذة شفافة، فلا يمسّ ويدجت المالك ولا إعداداته ولا الشبكة.
    It 'يبدأ، يسجّل تبديلات التخطيط، يحدّث الواجهة من ثابت الاختبار، ثم يخرج «exit: user» برمز 0' {
        $root = Join-Path ([IO.Path]::GetTempPath()) ('mw-smoke-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $root | Out-Null
        $widget = Join-Path $script:widgetRepoRoot 'widget.ps1'
        $old = $env:MW_SMOKE
        try {
            $env:MW_SMOKE = '1'
            $p = Start-Process -FilePath (Get-Process -Id $PID).Path -WindowStyle Hidden -PassThru -ArgumentList @('-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $widget), '-Root', ('"{0}"' -f $root))
            $done = $p.WaitForExit(120000)
            if (-not $done) { try { $p.Kill() } catch {} }
            $log = @(Get-Content -LiteralPath (Join-Path $root 'widget.log') -Encoding UTF8 -ErrorAction SilentlyContinue)
            $text = $log -join "`n"
            $done | Should -BeTrue -Because "الويدجت لم يخرج خلال دقيقتين. السجل:`n$text"
            @($log | Where-Object { $_ -match ' fatal: ' }) | Should -BeNullOrEmpty -Because $text
            @($log | Where-Object { $_ -match ' start: pid \d+' }).Count | Should -Be 1 -Because $text
            @($log | Where-Object { $_ -match ' layout: (wide 726|portrait 385) ' }).Count | Should -BeGreaterThan 1 -Because $text
            @($log | Where-Object { $_ -match ' smoke: ok$' }).Count | Should -Be 1 -Because $text
            $log[-1] | Should -Match ' exit: user$'
            $p.ExitCode | Should -Be 0
        } finally {
            $env:MW_SMOKE = $old
            Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    It 'خارج وضع الاختبار لا يتغيّر شيء: القفل الأصلي، والشفافية من الإعداد، والاستطلاع يعمل' {
        $src = Get-Content -LiteralPath (Join-Path $script:widgetRepoRoot 'widget.ps1') -Raw
        $src | Should -Match "else \{ 'Local\\Azkia-MiniWidget' \}"
        $src | Should -Match 'if \(\$script:smoke\) \{ 0 \} else \{ \$v \}'
        ([regex]::Matches($src, 'if \(-not \$script:smoke\) \{ \$script:(ps|appearancePs) = ')).Count | Should -Be 2
    }
}

Describe 'إنذار البطارية في الويدجت (battAlert)' {
    BeforeAll { $script:battSrc = Get-Content -LiteralPath (Join-Path $script:widgetRepoRoot 'widget.ps1') -Raw }
    It 'النص مخفيّ افتراضيًا، يُعلَن لقارئ الشاشة، ويُعاد إلى المكدّس في التخطيط المستعاد' {
        $script:battSrc | Should -Match "\`$UI\.battAlert\.Visibility = 'Collapsed'; \[Windows\.Automation\.AutomationProperties\]::SetLiveSetting\(\`$UI\.battAlert, 'Assertive'\); \`$stack\.Children\.Add\(\`$UI\.battAlert\)"
    }
    It 'منطق الإظهار كما في Update-Widget: تنبيه ثم خطر بإشعار واحد، ويختفي بزوال الإنذار' {
        $m = [regex]::Match($script:battSrc, '(?s)\$ba = \$d\.battAlert.*?\$script:battToasted = \$false \}')
        $m.Success | Should -BeTrue
        $step = [scriptblock]::Create($m.Value)
        $C = @{ warn = 'W'; bad = 'B' }
        function Brush($c) { $c }
        $script:toasts = @(); function Show-Toast([string]$msg) { $script:toasts += $msg }
        $UI = @{ battAlert = [pscustomobject]@{ Text = ''; Visibility = ''; Foreground = '' } }
        $script:battToasted = $false; $worst = 'ok'
        $d = [pscustomobject]@{ battAlert = [pscustomobject]@{ level = 'warn'; text = 'البطارية 14%؛ وصّل الشاحن.' } }
        . $step
        $UI.battAlert.Visibility | Should -Be 'Visible'; $UI.battAlert.Text | Should -Be '⚠ البطارية 14%؛ وصّل الشاحن.'; $UI.battAlert.Foreground | Should -Be 'W'
        $worst | Should -Be 'warn'; $script:toasts.Count | Should -Be 0
        $d.battAlert.level = 'bad'; . $step; . $step
        $UI.battAlert.Foreground | Should -Be 'B'; $worst | Should -Be 'bad'; $script:toasts.Count | Should -Be 1
        $d = [pscustomobject]@{ battAlert = $null }; . $step
        $UI.battAlert.Visibility | Should -Be 'Collapsed'; $script:battToasted | Should -BeFalse
    }
}
