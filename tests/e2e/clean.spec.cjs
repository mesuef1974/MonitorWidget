// التنظيف الفعلي مهمة خلفية على خادم الثوابت: POST يبدأ بفحص ما قبل الحذف، والصفحة تستطلع GET /api/space-clean فتعرض
// «تحقّق قبل الحذف» ثم «عولج N ملف · تحرّر X» ثم المجلدات الفارغة المزالة ثم النتيجة؛ رفض نموّ المرشّحين يظهر في الصف، والتنظيف الجماعي يعرض النوع الجاري وتقدّمه.
const { test, expect } = require('@playwright/test');

const run = (phase, kind, dirs, files, mb) => ({ state: 'running', phase, kind, dirs, files, mb, elapsed: 1 });
const replier = replies => { let n = 0; return route => route.fulfill({ json: replies[Math.min(n++, replies.length - 1)] }); };

async function previewThenClean(page, cleanReplies) {
  await page.route('**/api/space-preview*', replier([{ state: 'done', dirs: 2600, files: 1840, mb: 512.3, elapsed: 2, res: { ok: true, dry: true, mb: 512.3, files: 1840, skipped: 0 } }]));
  await page.route('**/api/space-clean*', replier(cleanReplies));
  await page.goto('/#/spaceclean');
  const cell = page.locator('#main td[data-fa="sp-temp"]');
  await cell.locator('button[data-sp="preview"]').click({ timeout: 30000 });
  await cell.locator('button[data-sp="clean"]').click({ timeout: 5000 });
  await page.getByRole('dialog').getByRole('button', { name: 'نظّف' }).click();   // الحذف بعد المعاينة لا يبدأ إلا بتأكيد المالك
  return cell;
}

test('التنظيف يعرض التحقق ثم تقدّم الحذف ثم ما تحرّر', async ({ page }) => {
  const cell = await previewThenClean(page, [
    run('measure', 'temp', 1500, 900, 300),
    run('clean', 'temp', 2600, 1200, 340),
    { ...run('clean', 'temp', 2600, 1838, 510), emptyDirs: 4200 },
    { state: 'done', phase: 'clean', kind: 'temp', dirs: 2600, files: 1838, mb: 510.1, elapsed: 4, res: { kind: 'temp', ok: true, dry: false, mb: 510, files: 1838, skipped: 2, emptyDirs: 9373 } },
  ]);
  await expect(cell).toContainText('تحقّق قبل الحذف: فُحص 1,500 مجلد · 900 ملف مرشّح');
  await expect(cell).toContainText('فُحص 2,600 مجلد · عولج 1,200 ملف · تحرّر 340 MB');
  await expect(cell).toContainText('عولج 1,838 ملف · تحرّر 510 MB · أُزيل 4,200 مجلد فارغ');   // ثم المجلدات الفارغة القديمة
  await expect(cell.locator('.tag.ok')).toContainText('تحرّر 510 MB', { timeout: 5000 });
  await expect(cell).toContainText('+2 متروك');
  await expect(cell).toContainText('أُزيل 9,373 مجلد فارغ');
});

test('نموّ المرشّحين منذ المعاينة يُرفض في الصف بلا حذف', async ({ page }) => {
  const cell = await previewThenClean(page, [{ state: 'error', phase: 'measure', kind: 'temp', dirs: 2600, files: 5000, mb: 900, elapsed: 3, error: 'candidates_changed', now_mb: 900 }]);
  await expect(cell.locator('.tag.bad')).toHaveText('تغيّرت الملفات المرشّحة للحذف منذ المعاينة؛ أعد المعاينة', { timeout: 5000 });
});

test('التنظيف الجماعي يعرض النوع الجاري وتقدّمه ثم ما تحرّر', async ({ page }) => {
  await page.route('**/api/space-clean*', replier([
    run('clean', 'edgecache', 40, 300, 12),
    { state: 'done', phase: 'clean', kind: 'swdist', dirs: 0, files: 0, mb: 0, elapsed: 5, res: { ok: true, mb: 270.4, parts: [{ kind: 'temp', emptyDirs: 1200 }, { kind: 'edgecache', emptyDirs: 0 }] } },
  ]));
  await page.goto('/#/spaceclean');
  await page.locator('#main button[data-sp="all"]').click({ timeout: 30000 });
  await page.getByRole('button', { name: 'نظّف الكل' }).click();
  await expect(page.locator('#main [data-spall-prog]')).toContainText('ذاكرة Edge · فُحص 40 مجلد · عولج 300 ملف · تحرّر 12 MB');
  await expect(page.getByText('حُرّر 270 MB · أُزيل 1,200 مجلد فارغ')).toBeVisible({ timeout: 5000 });
});

test('الحذف بعد المعاينة يعرض حوار تأكيد، و«إلغاء» لا يرسل طلب التنظيف', async ({ page }) => {
  const posts = [];
  page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/api/space')) posts.push(JSON.parse(r.postData() || '{}').action); });
  await page.route('**/api/space-preview*', replier([{ state: 'done', dirs: 2600, files: 1840, mb: 512.3, elapsed: 2, res: { ok: true, dry: true, mb: 512.3, files: 1840, skipped: 0 } }]));
  await page.goto('/#/spaceclean');
  const cell = page.locator('#main td[data-fa="sp-temp"]');
  await cell.locator('button[data-sp="preview"]').click({ timeout: 30000 });
  await cell.locator('button[data-sp="clean"]').click({ timeout: 5000 });
  const dlg = page.getByRole('dialog');
  await expect(dlg).toContainText('تنظيف «الملفات المؤقتة» الآن — المتوقع تحرير 512 MB');
  await dlg.getByRole('button', { name: 'إلغاء' }).click();
  await expect(dlg).toHaveCount(0);
  expect(posts).toEqual(['preview']);
});
