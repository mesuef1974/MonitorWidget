// التركيز والتفاعل في CSS (البند (و) من مراجعة الواجهة) على خادم الثوابت:
// هامش تمرير يتبع ارتفاع الشريط اللاصق فلا يغطي العنصر المركَّز، لا تأخير نقر ولا وميض لمس على الأزرار والروابط،
// الدرج والحوار وصناديق التمرير تحبس التمرير داخلها، والرقائق والمفاتيح والعيّنات لها حالة تحويم.
const { test, expect } = require('@playwright/test');

const ready = page => page.waitForFunction(() => document.querySelector('#main .panel'));
const css = (page, sel, prop) => page.locator(sel).first().evaluate((el, p) => getComputedStyle(el).getPropertyValue(p), prop);

test('هامش التمرير أعلى الصفحة يتبع ارتفاع الشريط اللاصق في العرض الواسع والضيق', async ({ page }) => {
  for (const width of [1366, 600]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/#/overview'); await ready(page);
    await page.waitForFunction(() => document.documentElement.style.getPropertyValue('--bar-h'));
    const barH = await page.locator('.bar').evaluate(el => el.getBoundingClientRect().height);
    const pad = parseFloat(await page.evaluate(() => getComputedStyle(document.documentElement).scrollPaddingTop));
    expect(pad, `${width}px`).toBeGreaterThanOrEqual(barH);
    expect(pad, `${width}px`).toBeLessThan(barH + 40);
  }
});

test('الأزرار والروابط والحقول بلا تأخير نقر ولا وميض لمس', async ({ page }) => {
  const cases = [['overview', ['.nav a', '.btn']], ['ctrl', ['.chips button', '.swatch']], ['rules', ['.switch']], ['ctrl', ['.inp']]];
  for (const [pg, sels] of cases) {
    await page.goto(`/#/${pg}`); await ready(page);
    for (const sel of sels) {
      expect(await css(page, sel, 'touch-action'), `${pg} ${sel}`).toBe('manipulation');
      expect(await css(page, sel, '-webkit-tap-highlight-color'), `${pg} ${sel}`).toMatch(/rgba\(0, 0, 0, 0\)|transparent/);
    }
  }
});

test('الدرج والحوار وصناديق التمرير تحبس التمرير داخلها', async ({ page }) => {
  await page.goto('/#/dbx'); await ready(page);
  expect(await css(page, '#nav', 'overscroll-behavior-y')).toBe('contain');
  expect(await css(page, '#main .scrollbox', 'overscroll-behavior-y')).toBe('contain');
  await page.evaluate(() => { confirmDlg('اختبار', 'نفّذ'); });
  expect(await css(page, 'dialog.dlg', 'overscroll-behavior-y')).toBe('contain');
});

test('الرقائق والمفاتيح والعيّنات تتغيّر عند التحويم', async ({ page }) => {
  const probe = async (sel, prop) => {
    const el = page.locator(sel).first();
    await page.mouse.move(0, 0);
    const before = await el.evaluate((e, p) => getComputedStyle(e).getPropertyValue(p), prop);
    await el.hover();
    await page.waitForTimeout(250);   // انتقال .15s
    const after = await el.evaluate((e, p) => getComputedStyle(e).getPropertyValue(p), prop);
    expect(after, sel).not.toBe(before);
  };
  await page.goto('/#/ctrl'); await ready(page);
  await probe('.chips button:not([aria-pressed="true"])', 'background-color');
  await probe('.swatch:not([aria-pressed="true"])', 'border-color');
  await page.goto('/#/rules'); await ready(page);
  await probe('.switch:not(:disabled)', 'border-color');
});
