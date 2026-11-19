# -*- coding: utf-8 -*-
"""ثوابت المسارات والأدوات المشتركة بين server.py ووحدات المسارات (البند 6 من الخارطة).
مصدر واحد لهذه القيم حتى تستوردها وحدات المسارات دون أن تستورد server.py (دورة استيراد)، وحتى لا تتكرر في كل وحدة."""
import os

import secure

ROOT = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("USERPROFILE") or os.path.expanduser("~")
PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"
CNW = 0x08000000  # CREATE_NO_WINDOW
ENGINE = HOME + r"\Documents\Scripts\Cleanup\v2\CleanupEngine.ps1"
PROT_ENGINE_DIR = os.path.join(secure.SECURE_DIR, "cleanup")
# النشر المحمي (deploy_protected.ps1): الكود يعمل من ProgramData\MonitorWidget\app. سياسة التنظيف تحدّد ما يحذفه محرّك
# مرتفع، فلا تُقرأ عندئذ من مجلد يكتبه المستخدم العادي: نسختها المحمية في مجلد المحرّك (يزرعها المثبّت مرة من النسخة القديمة).
PROTECTED = secure.protected_app_ready()
USER_POLICY = HOME + r"\Documents\Scripts\Cleanup\v2\cleanup-policy.json"
PROT_POLICY = os.path.join(PROT_ENGINE_DIR, "cleanup-policy.json")
POLICY = PROT_POLICY if PROTECTED else USER_POLICY
RUNS = HOME + r"\Documents\Scripts\Cleanup\logs\runs.jsonl"
MCCFG = HOME + r"\Documents\Scripts\MachineCare\machinecare.config.json"
TIERS = ("daily", "weekly", "monthly", "quarterly")
TIER_TASK = {t: "Cleanup_v2_" + t.capitalize() for t in TIERS}
PORT = 47832  # منفذ الخادم المحلي (127.0.0.1 فقط)
