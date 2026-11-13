// فحص الأسماء التي يقرؤها قارئ الشاشة في web/index.html على خادم الثوابت (الدفعة 2 من إصلاحات الإتاحة):
// في كل صفحة: كل مفتاح تبديل وحقل ومجموعة وجدول له اسم، لا رأس عمود فارغ، ورؤوس الجداول رؤوس أعمدة؛
// وزر الويدجت اسمه ثابت وحالته في aria-pressed، وزر القائمة يعلن فتح الدرج.
const { test, expect } = require('@playwright/test');

const SLOW = new Set(['space', 'spaceclean', 'advice']);

// الاسم المتاح تقريبًا كما يحسبه المتصفح: aria-label، ثم label مرتبط أو حاوٍ، ثم النص المرئي
const audit = () => {
  const name = el => {
    if (el.getAttribute('aria-label')) return true;
    const by = el.getAttribute('aria-labelledby'); if (by && document.getElementById(by)?.textContent.trim()) return true;
    if (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) return true;
    if (el.closest('label')?.textContent.trim()) return true;
    return !!el.textContent.trim();
  };
  const bad = [];
  const m = document.querySelector('#main');
  m.querySelectorAll('[role=switch]').forEach(el => { if (!name(el)) bad.push('switch ' + (el.outerHTML.slice(0, 90))); });
  m.querySelectorAll('input, select, textarea').forEach(el => { if (!name(el)) bad.push('field ' + (el.id || el.name || el.outerHTML.slice(0, 60))); });
  m.querySelectorAll('[role=group]').forEach(el => { if (!el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby')) bad.push('group ' + el.className); });
  m.querySelectorAll('table').forEach(el => { if (!el.getAttribute('aria-label') && !el.querySelector('caption')) bad.push('table ' + el.className); });
  m.querySelectorAll('th').forEach(el => { if (!el.textContent.trim()) bad.push('empty th'); if (el.getAttribute('scope') !== 'col') bad.push('th no scope'); });
  return bad;
};

test('كل مفتاح وحقل ومجموعة وجدول في كل صفحة له اسم، ولا رأس عمود فارغ', async ({ page }) => {
  test.setTimeout(120000);
  await page.goto('/');
  await page.waitForFunction(() => typeof PAGES === 'object' && document.querySelector('#main'));
  const keys = await page.evaluate(() => Object.keys(PAGES));
  const found = [];
  for (const k of keys) {
    await page.evaluate(key => { location.hash = '#/' + key; }, k);
    await page.waitForFunction(() => { const m = document.querySelector('#main'); return m && (m.querySelector('.panel') || m.querySelector('.banner')); }, null, { timeout: 20000 });
    await page.waitForTimeout(SLOW.has(k) ? 1500 : 300);
    const bad = await page.evaluate(audit);
    for (const b of bad) found.push(`${k}: ${b}`);
    expect.soft(bad, `${k}: ${bad.join(' | ')}`).toEqual([]);
  }
  expect(found, found.join('\n')).toEqual([]);
});

test('مفاتيح الصفحات تحمل أسماءً مفهومة من بياناتها', async ({ page }) => {
  await page.goto('/#/startup');
  await page.waitForFunction(() => document.querySelector('#main table'));
  const first = page.locator('#main [role=switch]').first();
  expect(await first.getAttribute('aria-label')).toMatch(/^تفعيل .+ عند بدء التشغيل$/);
  expect(await page.locator('#main table').first().getAttribute('aria-label')).toBeTruthy();
  expect(await page.locator('#main th .sr').first().textContent()).toBe('إجراءات');

  await page.evaluate(() => { location.hash = '#/rules'; });
  await page.waitForFunction(() => location.hash === '#/rules' && document.querySelector('#main [data-tier]'));
  expect(await page.locator('#main [data-tier]').first().getAttribute('aria-label')).toMatch(/^إيقاف المستوى .+ مؤقتًا$/);
  expect(await page.locator('#main [data-rule]').first().getAttribute('aria-label')).toMatch(/^القاعدة .+/);
  // معايير القاعدة من السياسة تحت وصفها، والحالة والإجراء والتفصيل بالعربية لا بألفاظ المحرّك
  const row = page.locator('#main tr', { has: page.locator('[data-rule="user-temp"]') });
  expect(await row.locator('.crit').textContent()).toBe('%LOCALAPPDATA%\\Temp · أقدم من يوم');
  expect(await row.locator('td').nth(1).textContent()).toBe('حذف');
  expect(await row.locator('td').nth(3).locator('.tag').textContent()).toBe('تم');
  expect(await row.locator('td').nth(3).locator('div.note').textContent()).toBe('24,948 ملف');
});

test('زر الويدجت اسمه ثابت وحالته في aria-pressed؛ زر القائمة يعلن فتح الدرج', async ({ page }) => {
  await page.goto('/#/overview');
  await page.waitForFunction(() => document.querySelector('#main .panel'));
  const wb = page.locator('#widgetBtn');
  const txt = (await wb.textContent()).trim();
  await page.waitForFunction(() => document.querySelector('#widgetBtn').getAttribute('aria-pressed') === 'true');   // الثابت widget.json: running=true
  expect((await wb.textContent()).trim()).toBe(txt);
  expect(txt).toBe('الويدجت');

  const mb = page.locator('#menuBtn');
  expect(await mb.getAttribute('aria-controls')).toBe('nav');
  expect(await mb.getAttribute('aria-expanded')).toBe('false');
  await page.evaluate(() => document.querySelector('#menuBtn').click());   // الزر مخفي على الشاشة العريضة؛ النقر برمجي
  expect(await mb.getAttribute('aria-expanded')).toBe('true');
  expect(await page.locator('#nav').evaluate(n => n.classList.contains('open'))).toBe(true);
  await page.evaluate(() => { location.hash = '#/health'; });
  await page.waitForFunction(() => location.hash === '#/health');
  expect(await mb.getAttribute('aria-expanded')).toBe('false');
});
