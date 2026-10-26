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
    ("تغطية مؤشرات الصحة (معزول)", "health_score_regression.py"))]
steps += [(title, [sys.executable, "-X", "utf8", "-B", os.path.join(HERE, file)]) for title,file in (
    ("فتح النافذة وسياسة CSP (معزول)", "window_security_check.py"),
    ("جمع النسخة المحمية (معزول)", "deployment_agent_check.py"),
    ("حد التنفيذ المرتفع (معزول)", "elevated_execution_check.py"),
    ("ترحيل بيانات الحزمة (معزول)", "protected_installer_check.py"))]
steps += [("عقود مهام التثبيت (معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "protected_installer_xml_check.ps1")])]
# The full original widget is restored. The compact/balanced-only contracts
# remain historical and are replaced by widget_restored_check.
# Legacy greedy-packing tests remain historical; canonical acceptance replaces that retired path.
steps += [("سلوك الواجهة (معزول)", ["node", os.path.join(HERE, "frontend_contract_check.js")]),
          ("تركيب الصفحات المعاد بناؤه والوصول للإجراءات (DOM معزول)", ["node", os.path.join(HERE, "frontend_canonical_acceptance_check.cjs")]),
          ("النظرة العامة الجديدة وبياناتها الفعلية البنية (DOM معزول)", ["node", os.path.join(HERE, "frontend_canonical_home_check.cjs")]),
          ("بقاء دوال التحميل ومعالجات الإجراءات الأصلية (مصدر)", ["node", os.path.join(HERE, "frontend_surface_check.cjs")]),
          ("تحميل JavaScript الكامل مع خادم مصطنع منقطع (DOM معزول)", ["node", os.path.join(HERE, "frontend_boot_check.cjs")]),
          ("المظهر المشترك للواجهة (معزول)", ["node", os.path.join(HERE, "frontend_appearance_check.js")]),
          ("انتشار الهوية والتباين في لوحات التطبيق (معزول)", ["node", os.path.join(HERE, "frontend_identity_palette_check.cjs")]),
          ("هندسة الصفحات والرسوم (معزول)", ["node", os.path.join(HERE, "frontend_layout_check.js")]),
          ("عجلة الفأرة والتمرير الطبيعي وتقليب الصفحات المقصود (معزول)", ["node", os.path.join(HERE, "frontend_wheel_check.js")]),
          ("جميع الصفحات والقراءة (معزول)", ["node", os.path.join(HERE, "frontend_pages_check.js")]),
          ("تباين حالات عناصر التحكم (معزول)", ["node", os.path.join(HERE, "frontend_control_contrast_check.js")]),
          ("التحكم والتنبيهات وحفظ المسودات (DOM معزول)", ["node", os.path.join(HERE, "frontend_ctrl_alerts_check.js")]),
          ("التاريخ وقاعدة البيانات والحقول الكاملة (DOM معزول)", ["node", os.path.join(HERE, "frontend_db_history_check.cjs")]),
          ("التنقل وإمكانية الوصول (DOM معزول)", ["node", os.path.join(HERE, "frontend_rail_groups_check.cjs")]),
          ("الذاكرة والطاقة: اكتمال المعلومات والإجراءات (DOM معزول)", ["node", os.path.join(HERE, "frontend_mem_power_check.cjs")]),
          ("العمليات وWSL والتوصيات: اكتمال ووصول (DOM معزول)", ["node", os.path.join(HERE, "frontend_procs_wsl_advice_check.cjs")]),
          ("المعالج والرسوميات: القوائم والرسوم المحدودة (DOM معزول)", ["node", os.path.join(HERE, "frontend_cpu_gpu_check.cjs")]),
          ("ملكية قائمة السجلات والطلبات المتأخرة (DOM معزول)", ["node", os.path.join(HERE, "frontend_pager_owner_check.cjs")]),
          ("ثبات الترقيم والمسودات بعد التحديث (DOM معزول)", ["node", os.path.join(HERE, "frontend_fit_refresh_check.cjs")]),
          ("ميزانية ارتفاع الرسوم المرنة (معزول)", ["node", os.path.join(HERE, "frontend_chart_budget_check.cjs")]),
          ("المساحة وحركة القرص والإجراءات المحمية (DOM معزول)", ["node", os.path.join(HERE, "frontend_space_io_check.cjs")]),
          ("محتوى أقسام المساحة الخمسة والتنقل والصفوف الفعلية (DOM معزول)", ["node", os.path.join(HERE, "frontend_space_content_check.cjs")]),
          ("بدء التشغيل والجدولة والقواعد (DOM معزول)", ["node", os.path.join(HERE, "frontend_startup_tasks_rules_check.cjs")]),
          ("النظرة العامة والصحة والتغطية (DOM معزول)", ["node", os.path.join(HERE, "frontend_home_health_check.cjs")]),
          ("إعدادات المظهر المشتركة وحفظها (DOM معزول)", ["node", os.path.join(HERE, "frontend_appearance_regions_check.cjs")]),
          ("رسائل التأكيد الكاملة وقبولها وإلغاؤها (DOM معزول)", ["node", os.path.join(HERE, "frontend_confirmation_check.cjs")]),
          ("أزرار نافذة الويدجت (معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_close_check.ps1")]),
          ("الويدجت الكامل المستعاد والمؤشرات ومفاتيح الصيانة (WPF معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_restored_check.ps1")]),
          ("المظهر المشترك للويدجت الكامل (WPF معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_appearance_check.ps1")]),
          ("انتشار الهوية على أسطح ومؤشرات الويدجت (WPF معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_identity_check.ps1")]),
          ("تباين أزرار الويدجت الكامل (WPF معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_button_contrast_check.ps1")]),
          ("هندسة الرسم الكامل وعناوين المحاور (WPF معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_chart_geometry_check.ps1")]),
          ("تباين المنيو والقوائم الفرعية (معزول)", ["pwsh", "-NoProfile", "-File", os.path.join(HERE, "widget_menu_contrast_check.ps1")])]
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
