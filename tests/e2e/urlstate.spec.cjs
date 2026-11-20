// حالة الصفحة في الرابط (البند (ج) من مراجعة الواجهة) على خادم الثوابت:
// المدى الزمني ومرشّحات الصفحات تُقرأ من #/صفحة?… عند الدخول وتُكتب إليه عند كل تغيير (دون إدخال جديد في السجل)،
// المدى يبقى عبر الصفحات والمرشّحات تعود إلى الافتراضي حين تغيب، theme-color يتبع --bg، والخطوط الأولى تُحمَّل مسبقًا.
const { test, expect } = require('@playwright/test');

const ready = page => page.waitForFunction(() => document.querySelector('#main .panel'));
const pressed = (page, sel) => expect(page.locator(sel)).toHaveAttribute('aria-pressed', 'true');

test('المدى والجهاز يُقرآن من الرابط ويُكتبان إليه؛ المدى يبقى عبر الصفحات والمرشّح لا', async ({ page }) => {
  const calls = []; page.on('request', r => { if (r.url().includes('/api/io?')) calls.push(r.url()); });
  await page.route('**/api/io?*', async route => {   // ثوابت الخادم تعيد dev ثابتًا؛ الخادم الحي يصدّي الجهاز المطلوب، فنصدّيه هنا
    const d = await (await route.fetch()).json(); d.dev = new URL(route.request().url()).searchParams.get('dev'); await route.fulfill({ json: d });
  });
  await page.goto('/#/io?range=1h&dev=_total'); await ready(page);
  await pressed(page, '[data-range="1h"]');
  await pressed(page, '[data-iodev="_total"]');
  expect(calls.some(u => u.includes('range=1h') && u.includes('dev=_total'))).toBe(true);   // الطلب يحمل حالة الرابط

  const depth = await page.evaluate(() => history.length);
  await page.click('[data-range="6h"]');
  await page.waitForFunction(() => location.hash === '#/io?range=6h&dev=_total');
  await pressed(page, '[data-range="6h"]');
  expect(await page.evaluate(() => history.length)).toBe(depth);                            // تغيير المرشّح لا يضيف إدخالًا في السجل

  await page.click('#nav a[href="#/mem"]'); await ready(page);
  await page.waitForFunction(() => location.hash === '#/mem?range=6h');                      // المدى يبقى والجهاز خاص بصفحته
  await pressed(page, '[data-range="6h"]');
  await page.click('#nav a[href="#/procs"]'); await ready(page);
  await page.waitForFunction(() => location.hash === '#/procs');                                // صفحة بلا مدى: لا يُكتب فيها
  await page.click('#nav a[href="#/io"]'); await ready(page);
  await page.waitForFunction(() => location.hash === '#/io?range=6h');                       // بعد hashchange لا قبله
  await pressed(page, '[data-iodev="disk0"]');                                              // عاد إلى الافتراضي

  await page.goto('/#/mem?range=zzz&dev=../x'); await ready(page);                           // قيم غير صالحة تُهمل ولا تُكتب
  await page.waitForFunction(() => location.hash === '#/mem?range=6h');
  await pressed(page, '[data-range="6h"]');
});

test('مرشّحات السجل في الرابط: الشدّة والبحث والإزاحة، والتغيير يكتبها', async ({ page }) => {
  await page.goto('/#/history?sev=warn&q=%D8%AE%D8%B7%D8%A3&off=12&roff=8'); await ready(page);
  expect(await page.inputValue('#evSev')).toBe('warn');
  expect(await page.inputValue('#evQ')).toBe('خطأ');
  expect(await page.evaluate(() => [ui.evOff, ui.runOff])).toEqual([12, 8]);

  await page.selectOption('#evSev', 'err');
  await page.waitForFunction(() => location.hash === '#/history?sev=err&q=%D8%AE%D8%B7%D8%A3&roff=8');   // الإزاحة تعود إلى الصفر فتُحذف
  await page.fill('#evQ', 'cpu'); await page.press('#evQ', 'Enter');
  await page.waitForFunction(() => location.hash === '#/history?sev=err&q=cpu&roff=8');
});

test('جدول القاعدة وإزاحته في الرابط', async ({ page }) => {
  await page.goto('/#/dbx?tbl=events&toff=15'); await ready(page);
  await pressed(page, '[data-tbl="events"]');
  expect(await page.evaluate(() => ui.dbOff)).toBe(15);
  await page.click('[data-tbl="actions"]');
  await page.waitForFunction(() => location.hash === '#/dbx?tbl=actions');
  await pressed(page, '[data-tbl="actions"]');
});

test('theme-color يتبع لون الخلفية في الوضعين، والخطوط الأولى مُحمَّلة مسبقًا من fonts.css', async ({ page }) => {
  await page.goto('/#/procs'); await ready(page);
  const same = () => page.waitForFunction(() => document.querySelector('meta[name="theme-color"]').content === getComputedStyle(document.documentElement).getPropertyValue('--bg').trim());
  await same();
  const before = await page.evaluate(() => document.querySelector('meta[name="theme-color"]').content);
  const other = await page.evaluate(() => document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  await page.evaluate(m => applyAppearance({ mode: m, palette: 'teal', revision: 999 }), other);
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe(other);
  await same();
  expect(await page.evaluate(() => document.querySelector('meta[name="theme-color"]').content)).not.toBe(before);

  const pre = await page.evaluate(() => [...document.querySelectorAll('link[rel="preload"][as="font"]')].map(l => ({ href: l.getAttribute('href'), cors: l.hasAttribute('crossorigin'), type: l.type })));
  expect(pre.length).toBeGreaterThanOrEqual(2);
  const css = await (await page.request.get('/css/fonts.css')).text();
  for (const l of pre) {
    expect(l.cors).toBe(true); expect(l.type).toBe('font/woff2');
    expect(css).toContain(`url(${l.href})`);                                                // لا تحميل مسبق لملف لا يستعمله fonts.css
    expect((await page.request.get(l.href)).status()).toBe(200);
  }
});
