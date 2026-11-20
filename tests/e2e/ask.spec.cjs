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
  route(page, { state: 'done', text: 'اقترحت أمرًا', actions: [{ action: 'unload_model', target: '', label: 'إخراج النموذج من LM Studio' }], tools: [], model: 'm', ms: 1000 });
  const unloads = [];
  await page.route('**/api/llm-unload', r => { unloads.push(JSON.parse(r.request().postData())); return r.fulfill({ json: { ok: true } }); });
  await open(page);
  await page.locator('[data-askq]').last().click();
  const card = page.locator('[data-askrun]');
  await expect(card).toHaveText('إخراج النموذج من LM Studio');
  await card.click();
  await expect(page.locator('dialog.dlg')).toContainText('إخراج النموذج من LM Studio');
  expect(unloads).toEqual([]);
  await page.locator('dialog.dlg button[value="yes"]').click();
  await expect(page.locator('.ask-msg .tag.ok')).toContainText('نُفّذ');
  expect(unloads).toEqual([{}]);
});

test('أمر غير معروف وصفحة غير معروفة لا يظهران زرين', async ({ page }) => {
  route(page, { state: 'done', text: 'x', actions: [{ action: 'format_disk', target: '', label: 'مسح' }, { action: 'open_page', target: 'evil', label: 'فتح' }], tools: [], model: 'm', ms: 1000 });
  await open(page);
  await page.locator('[data-askq]').first().click();
  await expect(page.locator('.ask-msg').nth(1)).toContainText('x');
  await expect(page.locator('.ask-msg').nth(1).locator('button, a')).toHaveCount(0);
});

test('المحادثة تنتقل إلى آخر رد تلقائيًا', async ({ page }) => {
  route(page, { state: 'done', text: 'سطر\n'.repeat(200) + 'النهاية', actions: [], tools: [], model: 'm', ms: 1000 });
  await open(page);
  await page.locator('[data-askq]').first().click();
  await expect(page.locator('.ask-msg').nth(1)).toContainText('النهاية');
  const gap = await page.locator('.ask-log').evaluate(l => ({ over: l.scrollHeight > l.clientHeight, left: l.scrollHeight - l.clientHeight - l.scrollTop }));
  expect(gap.over).toBe(true);
  expect(gap.left).toBeLessThanOrEqual(2);
});

const seeded = page => page.route('**/api/assistant', r => r.fulfill({ json: {
  turns: [{ id: 1, role: 'user', content: 'سؤال قديم' }, { id: 2, role: 'assistant', content: 'جواب قديم', model: 'm', ms: 1000, rating: null }],
  facts: [{ id: 5, text: 'لا تقترح تفريغ الكرت' }], ratings: { good: 0, bad: 0 }, keepDays: 30 } }));
const collect = (page, path, json) => { const got = []; page.route(path, r => { got.push(JSON.parse(r.request().postData())); return r.fulfill({ json }); }); return got; };

test('المحادثة المحفوظة تعود ولا تُرسل سياقًا للنموذج', async ({ page }) => {
  await seeded(page);
  const asked = route(page, { state: 'done', text: 'جواب جديد', actions: [], tools: [], model: 'm', ms: 1000, turnId: 9 });
  await open(page);
  await expect(page.locator('.ask-msg').nth(1)).toContainText('جواب قديم');
  await page.fill('#askInput', 'سؤال جديد');
  await page.press('#askInput', 'Enter');
  await expect(page.locator('.ask-msg').nth(3)).toContainText('جواب جديد');
  expect(asked[0].history).toEqual([]);
});

test('تقييم الرد يُحفظ ويُلغى بالضغط ثانية', async ({ page }) => {
  await seeded(page);
  const rated = collect(page, '**/api/assistant-rate', { ok: true, ratings: { good: 0, bad: 1 } });
  await open(page);
  const bad = page.locator('[data-askrate="1:-1"]');
  await bad.click();
  await expect(bad).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('main')).toContainText('1 خاطئ');
  await bad.click();
  await expect(bad).toHaveAttribute('aria-pressed', 'false');
  expect(rated).toEqual([{ id: 2, value: -1 }, { id: 2, value: 0 }]);
});

test('مسح المحادثة لا يتم إلا بعد التأكيد', async ({ page }) => {
  await seeded(page);
  const cleared = collect(page, '**/api/assistant-clear', { ok: true });
  await open(page);
  await page.locator('[data-askclear]').click();
  expect(cleared).toEqual([]);
  await page.locator('dialog.dlg button[value="yes"]').click();
  await expect(page.locator('.ask-msg')).toHaveCount(0);
  expect(cleared).toHaveLength(1);
});

test('التعليمات الدائمة تُضاف وتُحذف', async ({ page }) => {
  await seeded(page);
  const sent = [];
  page.route('**/api/assistant-fact', r => {
    const b = JSON.parse(r.request().postData()); sent.push(b);
    return r.fulfill({ json: { ok: true, facts: b.remove ? [] : [{ id: 5, text: 'لا تقترح تفريغ الكرت' }, { id: 6, text: b.text }] } });
  });
  await open(page);
  await page.fill('#askFact', 'أجب بجملة واحدة');
  await page.locator('#askFactForm button').click();
  await expect(page.locator('.ask-facts li')).toHaveCount(2);
  await page.locator('[data-askfactdel="6"]').click();
  await expect(page.locator('.ask-facts li')).toHaveCount(0);
  expect(sent).toEqual([{ text: 'أجب بجملة واحدة' }, { remove: 6 }]);
});

test('الفشل يظهر رسالة واضحة', async ({ page }) => {
  route(page, { state: 'failed', error: 'LM Studio غير مفعّل.' });
  await open(page);
  await page.locator('[data-askq]').first().click();
  await expect(page.locator('.ask-msg.err')).toContainText('LM Studio غير مفعّل.');
});
