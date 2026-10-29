// فحص بقاء التمرير الداخلي في web/index.html على خادم الثوابت: البطاقة التي يتغيّر قالبها كانت تُستبدل كلها في paint،
// فكانت قوائمها وجداولها المُمرَّرة تقفز إلى رأسها مع كل ضغطة زر (aria-pressed أو حالة تتغيّر) ومع كل تحديث؛ وهي الآن تُحدَّث في مكانها.
const { test, expect } = require('@playwright/test');

const ready = page => page.waitForFunction(() => document.querySelector('#main .panel'));
// كل عنصر في المحتوى يقبل التمرير فعلًا (محتواه أطول من صندوقه وoverflow يسمح) يُمرَّر إلى منتصفه، ويعاد مساره وموضعه
const scrollAll = page => page.evaluate(() => [...document.querySelectorAll('#main *')].filter(e => {
  const s = getComputedStyle(e); return (/auto|scroll/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 4) || (/auto|scroll/.test(s.overflowX) && e.scrollWidth > e.clientWidth + 4);
}).map((e, i) => { e.dataset.sk = i; e.scrollTop = (e.scrollHeight - e.clientHeight) >> 1; e.scrollLeft = -((e.scrollWidth - e.clientWidth) >> 1); return [i, e.scrollTop, e.scrollLeft]; }));
const readAll = page => page.evaluate(() => [...document.querySelectorAll('#main [data-sk]')].map(e => [+e.dataset.sk, e.scrollTop, e.scrollLeft]));

test('كل قائمة وجدول مُمرَّر يبقى في موضعه حين تُستبدل بطاقته، في كل الصفحات', async ({ page }) => {
  await page.goto('/#/overview'); await ready(page);
  const keys = await page.evaluate(() => Object.keys(PAGES));
  let covered = 0;
  for (const k of keys) {
    await page.goto(`/#/${k}`); await ready(page); await page.waitForTimeout(300);
    const before = await scrollAll(page);
    if (!before.length) continue;
    covered += before.length;
    // كل بطاقة تُعدّ متغيّرة فتُستبدل كلها، كما يحدث حين يغيّر زرٌّ حالتها
    await page.evaluate(() => { [...document.querySelectorAll('#main > *')].forEach(p => { p.__tpl = ''; }); redraw(); });
    expect(await page.evaluate(() => document.querySelectorAll('#main [data-sk]').length), `${k}: البطاقات لم تُستبدل`).toBe(0);
    // الوسم data-sk لا يُنسخ إلى البطاقة الجديدة؛ نعيد ترقيم المُمرَّرات بالترتيب نفسه ونقارن المواضع
    const after = await page.evaluate(() => [...document.querySelectorAll('#main *')].filter(e => {
      const s = getComputedStyle(e); return (/auto|scroll/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 4) || (/auto|scroll/.test(s.overflowX) && e.scrollWidth > e.clientWidth + 4);
    }).map((e, i) => [i, e.scrollTop, e.scrollLeft]));
    expect(after, `${k}: التمرير الداخلي قفز`).toEqual(before);
  }
  expect(covered).toBeGreaterThan(0);
});

test('الضغط على صف في قائمة جداول القاعدة لا يعيدها إلى رأسها، ولا تتحرك الصفحة', async ({ page }) => {
  await page.goto('/#/dbx'); await ready(page);
  const box = page.locator('#main .list.scrollbox').first();
  await expect(box).toBeVisible();
  const n = await box.locator('[data-tbl]').count();
  test.skip(await box.evaluate(e => e.scrollHeight <= e.clientHeight + 4), 'قائمة الجداول في الثوابت أقصر من أن تُمرَّر');
  await page.evaluate(() => scrollTo(0, 120));
  const y = await page.evaluate(() => scrollY);
  const top = await box.evaluate(e => (e.scrollTop = e.scrollHeight - e.clientHeight));
  await box.locator('[data-tbl]').nth(n - 1).click();
  await expect(box.locator('[data-tbl]').nth(n - 1)).toHaveAttribute('aria-pressed', 'true');
  expect(await page.locator('#main .list.scrollbox').first().evaluate(e => e.scrollTop)).toBe(top);
  expect(await page.evaluate(() => scrollY)).toBe(y);
});

test('البطاقة المتغيّرة تُحدَّث في مكانها: الصندوق المُمرَّر هو العنصر نفسه، وتمرير العجلة الجاري لا يُقطع', async ({ page }) => {
  await page.goto('/#/spaceclean'); await ready(page); await page.waitForTimeout(300);
  const max = await page.evaluate(() => {
    const e = [...document.querySelectorAll('#main *')].find(x => /auto|scroll/.test(getComputedStyle(x).overflowY) && x.scrollHeight > x.clientHeight + 200);
    window.__sc = e; e.scrollTo({ top: e.scrollHeight, behavior: 'smooth' }); return e.scrollHeight - e.clientHeight;
  });
  // إعادة رسم كل البطاقات أثناء التمرير الناعم، كما يفعل تقدّم المهمة الخلفية كل ثانية
  for (let i = 0; i < 3; i++) { await page.evaluate(() => { [...document.querySelectorAll('#main > *')].forEach(p => { p.__tpl = ''; }); redraw(); }); await page.waitForTimeout(60); }
  await page.waitForTimeout(1200);
  expect(await page.evaluate(() => window.__sc.isConnected)).toBe(true);
  expect(Math.abs(await page.evaluate(() => window.__sc.scrollTop) - max)).toBeLessThan(3);
});
