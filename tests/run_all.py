"""Unified checks. --isolated runs regressions only; --write explicitly enables the startup toggle test."""
import os, subprocess, sys, datetime
HERE = os.path.dirname(os.path.abspath(__file__))
legacy_steps = [("ثابت: الكود والواجهة", ["check_all.py"]),
         ("قاعدة البيانات", ["db_check.py"]),
         ("سياسة التنظيف", ["policy_check.py"]),
         ("محرك التنظيف", ["engine_check.py"]),
         ("حارس الذاكرة (حراس)", ["guard_check.py"]),
         ("حي: الخادم وواجهاته", ["live_check.py"] + (["--write"] if "--write" in sys.argv else []))]
steps = [(title, [sys.executable, "-X", "utf8", "-B", os.path.join(HERE, file)]) for title, file in (
    ("الخلفية والإعدادات (معزول)", "backend_regression.py"),
    ("المظهر المشترك والمراجعات (معزول)", "appearance_regression.py"),
    ("البيانات والتشغيل (معزول)", "db_ops_regressions.py"),
    ("الحجر والتعافي (معزول)", "quarantine_regression.py"),
    ("SQLite الخاص (معزول)", "sqlite_runtime_check.py"),
    ("تغطية مؤشرات الصحة (معزول)", "health_score_regression.py"),
    ("التزام الذاكرة: الحالة وأعلى العمليات (معزول)", "commit_check.py"),
    ("إغلاق عملية بعينها من جدول الالتزام (معزول)", "procclose_check.py"),
    ("حارس النوافذ: الأصناف والإسكات (معزول)", "winguard_check.py"),
    ("لوحة الذكاء المحلي (معزول)", "localai_check.py"),
    ("اسأل المرصد (معزول)", "assistant_check.py"),
    ("ذاكرة اسأل المرصد (معزول)", "askstore_check.py"),
    ("اختيار النموذج المحلي الموحّد (معزول)", "explain_model_check.py"),
    ("مسارات التطبيق: الشرح وعدّاد الصفحات والإيقاف (معزول)", "routes_app_check.py"),
    ("جلسات المتصفح والتذاكر والرمز (معزول)", "websession_check.py"),
    ("الصفحة والملفات الثابتة وCSP (معزول)", "webstatic_check.py"),
    ("إقلاع الخادم: النسخة الواحدة والوكيل والخيوط الخلفية (معزول)", "webboot_check.py"),
    ("خريطة الالتزام: المشاريع والحجز الخامل والحاسبة والحاويات (معزول)", "commitmap_check.py"),
    ("رصد قفزات الالتزام لكل عملية (معزول)", "commitwatch_check.py"),
    ("رصد الإقلاع غير النظيف (معزول)", "bootwatch_check.py"),
    ("رصد استنفاد المنافذ (معزول)", "portwatch_check.py"))]
steps += [(title, [sys.executable, "-X", "utf8", "-B", os.path.join(HERE, file)]) for title,file in (
    ("فتح النافذة وسياسة CSP (معزول)", "window_security_check.py"),
    ("جمع النسخة المحمية (معزول)", "deployment_agent_check.py"),
    ("حد التنفيذ المرتفع (معزول)", "elevated_execution_check.py"),
    ("ترحيل بيانات الحزمة (معزول)", "protected_installer_check.py"),
    ("حذف Windows.old عبر المهمة المرتفعة (معزول)", "winold_task_check.py"),
    ("تنظيفات wintemp/swdist/DISM عبر المهام المرتفعة الثابتة (معزول)", "elevated_clean_task_check.py"))]
steps += [("عقود مهام التثبيت (معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "protected_installer_xml_check.ps1")])]
# The full original widget is restored. The compact/balanced-only contracts
# remain historical and are replaced by widget_restored_check.
# Legacy greedy-packing tests remain historical; canonical acceptance replaces that retired path.
# واجهة الويب الوحيدة (web/index.html) تُفحص حيًّا عبر tests/check_all.py وفحوص الخادم؛ حُذفت اختبارات الواجهة القديمة.
steps += [("أزرار نافذة الويدجت (معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_close_check.ps1")]),
          ("الويدجت الكامل المستعاد والمؤشرات ومفاتيح الصيانة (WPF معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_restored_check.ps1")]),
          ("المظهر المشترك للويدجت الكامل (WPF معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_appearance_check.ps1")]),
          ("انتشار الهوية على أسطح ومؤشرات الويدجت (WPF معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_identity_check.ps1")]),
          ("تباين أزرار الويدجت الكامل (WPF معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_button_contrast_check.ps1")]),
          ("هندسة الرسم الكامل وعناوين المحاور (WPF معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_chart_geometry_check.ps1")]),
          ("تباين المنيو والقوائم الفرعية (معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_menu_contrast_check.ps1")]),
          ("صقل الويدجت: عتامة الأسطح ورأس بصفين وموضع معلومة الصلاحية (WPF معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_polish_check.ps1")])]
steps += [("دوال الويدجت والهوية عبر Pester (معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "pester_check.ps1")]),
          ("صفحات الواجهة في الوضعين عبر Playwright على خادم الثوابت (معزول)", [sys.executable, "-X", "utf8", "-B", os.path.join(HERE, "e2e_check.py")])]
if "--isolated" not in sys.argv:
    steps += [(title, [sys.executable, "-X", "utf8", "-B", os.path.join(HERE, cmd[0])] + cmd[1:]) for title, cmd in legacy_steps]
out, bad, skipped = [], 0, 0
child_env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
for title, cmd in steps:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, env=child_env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        out.append(f"\n===== {title} (FAIL) =====\n{type(exc).__name__}\n")
        bad += 1
        continue
    out.append(f"\n===== {title} (exit {p.returncode}) =====\n{p.stdout}{p.stderr}")
    # Exit 77 explicitly means unavailable optional coverage, never PASS.
    if p.returncode == 77:
        skipped += max(1, sum(line.startswith("SKIP ") for line in p.stdout.splitlines()))
    elif p.returncode != 0 or any(line.startswith("FAIL ") for line in p.stdout.splitlines()):
        bad += 1
    if p.returncode != 77:
        skipped += sum(line.startswith("SKIP ") for line in p.stdout.splitlines())
header = f"تقرير مرصد الجهاز: {datetime.datetime.now():%Y-%m-%d %H:%M}\nالمراحل الفاشلة: {bad} من {len(steps)}\nالفحوص المتخطاة: {skipped} (التغطية غير مكتملة إن تجاوزت صفرًا)\n"
report = header + "".join(out)
open(os.path.join(HERE, "report.txt"), "w", encoding="utf-8").write(report)
print(report)
print("التقرير محفوظ في tests\\report.txt")
sys.exit(1 if bad else (77 if skipped else 0))
