"""غلاف لـ run_all: يشغّل فحص Playwright المرئي (tests/e2e) إن كانت تبعياته مثبّتة، وإلا يطبع SKIP ويخرج 77.
    cd tests/e2e && npm ci && npx playwright install chromium   # مرة واحدة
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
E2E = os.path.join(HERE, "e2e")
CLI = os.path.join(E2E, "node_modules", "@playwright", "test", "cli.js")
node = shutil.which("node")
if not node or not os.path.isfile(CLI):
    print("SKIP Playwright غير مثبّت في tests/e2e (npm ci ثم npx playwright install chromium)")
    sys.exit(77)
p = subprocess.run([node, CLI, "test", "-c", os.path.join(E2E, "playwright.config.cjs")], cwd=E2E,
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=170)
sys.stdout.write(p.stdout[-6000:] + p.stderr[-3000:])
if p.returncode != 0:
    print("FAIL Playwright: فحص الصفحات لم ينجح")
    sys.exit(1)
print("PASS Playwright: كل صفحات web/index.html في الوضعين على خادم الثوابت")
