// صفحة «اسأل المرصد»: الجواب يظهر من عمل خلفي، والأمر لا يُنفَّذ إلا بعد التأكيد، وأمر مجهول لا يصير زرًا.
const { test, expect } = require('@playwright/test');

const route = (page, done) => {
  const asked = [];
  page.route('**/api/ask**', r => {
    if (r.request().method() === 'POST') { asked.push(JSON.parse(r.request().postData())); return r.fulfill({ json: { id: 'j1' } }); }
    return r.fulfill({ json: done });
  });
  return asked;
};
const open = async page => { await page.goto('/#/ask'); await page.waitForFunction(() => document.querySelector('#askForm')); };

test('السؤال يُرسل ويظهر جوابه واسم النموذج', async ({ page }) => {
  const asked = route(page, { state: 'done', text: 'المساحة الحرة 115 GB', actions: [], tools: ['disk_status'], model: 'qwen/qwen3-8b', ms: 3200 });
  await open(page);
  await page.fill('#askInput', 'كم المساحة الحرة؟');
  await page.press('#askInput', 'Enter');
  await expect(page.locator('.ask-msg').nth(1)).toContainText('المساحة الحرة 115 GB');
  await expect(page.locator('.ask-msg').nth(1)).toContainText('qwen/qwen3-8b');
  expect(asked[0].q).toBe('كم المساحة الحرة؟');
});

test('بطاقة الأمر لا تنفذ إلا بعد التأكيد', async ({ page }) => {
  route(page, { state: 'done', text: 'اقترحت أمرًا', actions: [{ action: 'trim_memory', target: '', label: 'تنظيف الذاكرة' }], tools: [], model: 'm', ms: 1000 });
  const trims = [];
  await page.route('**/api/trim-memory', r => { trims.push(JSON.parse(r.request().postData())); return r.fulfill({ json: { ok: true } }); });
  await open(page);
  await page.locator('[data-askq]').last().click();
  const card = page.locator('[data-askrun]');
  await expect(card).toHaveText('تنظيف الذاكرة');
  await card.click();
  await expect(page.locator('dialog.dlg')).toContainText('تنظيف الذاكرة');
  expect(trims).toEqual([]);
  await page.locator('dialog.dlg button[value="yes"]').click();
  await expect(page.locator('.ask-msg .tag.ok')).toContainText('نُفّذ');
  expect(trims).toEqual([{ mode: 'light' }]);
});

test('أمر غير معروف وصفحة غير معروفة لا يظهران زرين', async ({ page }) => {
  route(page, { state: 'done', text: 'x', actions: [{ action: 'format_disk', target: '', label: 'مسح' }, { action: 'open_page', target: 'evil', label: 'فتح' }], tools: [], model: 'm', ms: 1000 });
  await open(page);
  await page.locator('[data-askq]').first().click();
  await expect(page.locator('.ask-msg').nth(1)).toContainText('x');
  await expect(page.locator('.ask-msg').nth(1).locator('button, a')).toHaveCount(0);
});

test('المحادثة تنتقل إلى آخر رد تلقائيًا', async ({ page }) => {
  route(page, { state: 'done', text: 'سطر
'.repeat(200) + 'النهاية', actions: [], tools: [], model: 'm', ms: 1000 });
  await open(page);
  await page.locator('[data-askq]').first().click();
  await expect(page.locator('.ask-msg').nth(1)).toContainText('النهاية');
  const gap = await page.locator('.ask-log').evaluate(l => ({ over: l.scrollHeight > l.clientHeight, left: l.scrollHeight - l.clientHeight - l.scrollTop }));
  expect(gap.over).toBe(true);
  expect(gap.left).toBeLessThanOrEqual(2);
});

test('الفشل يظهر رسالة واضحة', async ({ page }) => {
  route(page, { state: 'failed', error: 'LM Studio غير مفعّل.' });
  await open(page);
  await page.locator('[data-askq]').first().click();
  await expect(page.locator('.ask-msg.err')).toContainText('LM Studio غير مفعّل.');
});
