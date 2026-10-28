// معاينة المساحة الخلفية وإدارة التركيز على خادم الثوابت: POST يبدأ المعاينة جارية، والصفحة تستطلع GET /api/space-preview
// فتعرض التقدّم ثم النتيجة؛ التركيز يبقى في صف النوع (المرساة أثناء الانتظار ثم «نظّف»)، و«نظّف» يختفي حين لا شيء للتحرير،
// وتأجيل توصية ينقل التركيز إلى تأجيل البطاقة التالية ثم إلى «إظهار المؤجّلة».
const { test, expect } = require('@playwright/test');

const active = page => page.evaluate(() => { const a = document.activeElement; return { tag: a?.tagName || '', data: { ...(a?.dataset || {}) } }; });
const done = res => ({ state: 'done', files: res.files || 0, mb: res.mb || 0, elapsed: 2, res: { ok: true, dry: true, gb: 0, skipped: 0, lockedMB: 0, heldMB: 0, ...res } });

async function openTemp(page, replies) {
  let n = 0;
  await page.route('**/api/space-preview*', route => route.fulfill({ json: replies[Math.min(n++, replies.length - 1)] }));
  await page.goto('/#/spaceclean');
  const btn = page.locator('#main button[data-sp="preview"][data-spk="temp"]');
  await btn.waitFor({ timeout: 30000 });
  await btn.focus();
  await page.keyboard.press('Enter');
  return page.locator('#main td[data-fa="sp-temp"]');
}

test('المعاينة تعرض التقدّم ثم النتيجة، والتركيز يبقى في الصف ثم ينتقل إلى «نظّف»', async ({ page }) => {
  const cell = await openTemp(page, [{ state: 'running', dirs: 900, files: 600, mb: 40, elapsed: 1 }, done({ mb: 512.3, files: 1840 })]);
  await expect(cell).toContainText('فُحص 900 مجلد · 600 ملف مرشّح');
  expect(await active(page)).toEqual({ tag: 'TD', data: { fa: 'sp-temp' } });   // الزر المضغوط اختفى: المرساة ترث التركيز

  await expect(cell.locator('button[data-sp="clean"]')).toBeVisible({ timeout: 5000 });
  await expect(cell).toContainText('يتحرّر 512 MB');
  expect((await active(page)).data).toEqual({ sp: 'clean', spk: 'temp' });

  await page.keyboard.press('Tab');
  expect((await active(page)).data).toEqual({ sp: 'cancel', spk: 'temp' });
  await page.keyboard.press('Enter');
  await expect(cell.locator('button[data-sp="preview"]')).toBeVisible();
  expect((await active(page)).data).toEqual({ sp: 'preview', spk: 'temp' });
});

test('معاينة بلا شيء للتحرير لا تعرض «نظّف»، والتركيز على «إغلاق»', async ({ page }) => {
  const cell = await openTemp(page, [done({ mb: 0, files: 0, skipped: 5 })]);
  await expect(cell).toContainText('لا شيء للتحرير', { timeout: 5000 });
  await expect(cell).toContainText('5 ملف مقفل');
  expect(await cell.locator('button[data-sp="clean"]').count()).toBe(0);
  expect((await active(page)).data).toEqual({ sp: 'cancel', spk: 'temp' });
});

test('خطأ المعاينة يظهر في الصف بدل البقاء معلّقًا', async ({ page }) => {
  const cell = await openTemp(page, [{ state: 'error', files: 0, mb: 0, elapsed: 1, error: 'تعذّر القياس' }]);
  await expect(cell.locator('.tag.bad')).toHaveText('تعذّر القياس', { timeout: 5000 });
});

test('تأجيل توصية بلوحة المفاتيح ينقل التركيز إلى التالية ثم إلى «إظهار المؤجّلة»، والإظهار يعيده إلى أول تأجيل', async ({ page }) => {
  await page.goto('/#/advice');
  const snz = page.locator('#main [data-advsnooze]');
  await snz.first().waitFor({ timeout: 30000 });
  const ids = await snz.evaluateAll(bs => bs.map(b => b.dataset.advsnooze));
  expect(ids.length).toBeGreaterThan(1);
  await snz.first().focus();
  for (let i = 0; i < ids.length; i++) {
    await page.keyboard.press('Enter');
    await expect(page.locator(`#main [data-advsnooze="${ids[i]}"]`)).toHaveCount(0);
    const a = await active(page);
    if (i < ids.length - 1) expect(a.data.advsnooze).toBe(ids[i + 1]);
    else expect(a.data.advrestore).toBe('1');
  }
  await page.keyboard.press('Enter');
  await expect(snz).toHaveCount(ids.length);
  expect((await active(page)).data.advsnooze).toBe(ids[0]);
});
