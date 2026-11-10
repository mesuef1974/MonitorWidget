// صفحة المساحة: بطاقة لكل قرص ثابت. C فيه الحر والمستخدم وتغيّر 24 ساعة، وكل قرص آخر (D) تحت C ببطاقته: الحر والمستخدم وأكبر مجلد وعمر القياس.
const { test, expect } = require('@playwright/test');

test('بطاقة القرص C ثم بطاقة القرص D تحتها، وكلتاهما بالمستخدم والحر', async ({ page }) => {
  await page.goto('/#/space');
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  const titles = await page.locator('#main .panel .ph h2').allTextContents();
  const ic = titles.findIndex(t => t.trim() === 'القرص C'), id = titles.findIndex(t => t.trim().startsWith('القرص D'));
  expect(ic).toBeGreaterThanOrEqual(0);
  expect(id).toBe(ic + 1);   // D تحت C مباشرة
  for (const i of [ic, id]) {
    const card = page.locator('#main .panel').nth(i);
    await expect(card.locator('.kpi .l', { hasText: 'المستخدم' })).toHaveCount(1);
    await expect(card.locator('.kpi .l', { hasText: 'حرّ' })).toHaveCount(1);
    await expect(card.locator('.meter')).toHaveCount(1);
  }
  await expect(page.locator('#main .panel').nth(ic).locator('.kpi .l', { hasText: 'التغير خلال 24 ساعة' })).toHaveCount(1);
  await expect(page.locator('#main .panel').nth(id).locator('.kpi .l', { hasText: 'أكبر مجلد' })).toHaveCount(1);
});
