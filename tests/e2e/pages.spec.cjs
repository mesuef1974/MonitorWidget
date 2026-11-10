// فحص مرئي لكل صفحات web/index.html على خادم الثوابت (مبني على shots/lay/tour.cjs):
// لكل صفحة في PAGES وفي الوضعين الداكن والفاتح: تُعرض لوحات، لا تمدّد أفقي، الاتجاه RTL، ولا أخطاء وحدة التحكم ولا استثناءات.
const { test, expect } = require('@playwright/test');

const SLOW = new Set(['space', 'spaceclean', 'advice']);

test('كل الصفحات تُعرض في الوضعين بلا أخطاء ولا تمدّد أفقي', async ({ page }) => {
  test.setTimeout(240000);
  const errors = [];
  page.on('pageerror', e => errors.push('EXC ' + e.message.slice(0, 200)));
  page.on('console', m => { if (m.type() === 'error') errors.push('ERR ' + m.text().slice(0, 200)); });
  page.on('requestfailed', r => errors.push('NET ' + r.url().slice(0, 160) + ' ' + (r.failure()?.errorText || '')));

  await page.goto('/');
  await page.waitForFunction(() => typeof PAGES === 'object' && document.querySelector('#main'));
  const keys = await page.evaluate(() => Object.keys(PAGES));
  expect(keys.length).toBeGreaterThan(10);
  expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl');

  const seen = [];
  for (const theme of ['dark', 'light']) {
    for (const k of keys) {
      await page.evaluate(([t, key]) => { document.documentElement.dataset.theme = t; location.hash = '#/' + key; }, [theme, k]);
      await page.waitForFunction(() => {
        const m = document.querySelector('#main');
        return m && (m.querySelector('.panel') || m.querySelector('.banner'));
      }, null, { timeout: 20000 });
      await page.waitForTimeout(SLOW.has(k) ? 1500 : 400);
      const r = await page.evaluate(() => ({
        panels: document.querySelectorAll('#main .panel').length,
        banners: [...document.querySelectorAll('#main .banner')].map(b => b.textContent.trim().slice(0, 80)),
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        theme: document.documentElement.dataset.theme,
      }));
      seen.push(`${theme}/${k}: panels=${r.panels} overflow=${r.overflow}`);
      expect.soft(r.panels, `${theme}/${k}: لا لوحات (${r.banners.join(' | ')})`).toBeGreaterThan(0);
      expect.soft(r.overflow, `${theme}/${k}: تمدّد أفقي`).toBeLessThanOrEqual(1);
      expect.soft(r.theme, `${theme}/${k}: الثيم تغيّر`).toBe(theme);
    }
  }
  test.info().annotations.push({ type: 'pages', description: seen.join('\n') });
  expect(errors, errors.join('\n')).toEqual([]);
});

test('صفحة الصحة: لا تحذير مع WebView2، وتحذير ظاهر بسبب السقوط وتفاصيله عند Edge', async ({ page }) => {
  await page.goto('/#/health');
  await page.waitForFunction(() => document.querySelector('#main .panel'));
  expect(await page.locator('#main .banner.warn').count()).toBe(0);   // الثابت: engine=webview2 وfallback=false

  // الخادم يعيد shell من سقوط الغلاف نفسه (init_failed) رغم توفر المكتبات (ready=true)
  await page.route('**/api/health', async route => {
    const res = await route.fetch();
    const body = await res.json();
    body.shell = { ready: true, reason: '', engine: 'edge', lastReason: 'init_failed', ts: 1793104155, fallback: true, detail: 'init_failed: HRESULT 0x80070002' };
    await route.fulfill({ response: res, json: body });
  });
  await page.evaluate(() => { location.hash = '#/overview'; });
  await page.waitForFunction(() => location.hash === '#/overview' && document.querySelector('#main .panel'));
  await page.evaluate(() => { location.hash = '#/health'; });
  const banner = page.locator('#main .banner.warn');
  await expect(banner).toBeVisible({ timeout: 10000 });
  await expect(banner).toHaveAttribute('role', 'alert');
  const text = await banner.textContent();
  expect(text).toContain('Edge');
  expect(text).toContain('WebView2');
  expect(text).toContain('shell_error.log');
  expect(text).toContain('0x80070002');
  expect(text).not.toContain('أعد فتح النافذة');   // المكتبات متوفرة لكن التهيئة فشلت: إعادة الفتح لا تكفي
});

test('عدّاد فتح الصفحات: بلاغ واحد لكل انتقال إلى صفحة مختلفة، وصفحة التحكم تعرض الاستخدام والصفحات التي لم تُفتح', async ({ browser }) => {
  // العدّاد يتجاهل المتصفح المُساق آليًا (hwhealth.spec.cjs)، فهذا الاختبار يحاكي متصفح المالك: لا webdriver ولا Headless
  const ctx = await browser.newContext({ userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0' });
  await ctx.addInitScript(() => Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => false }));
  const page = await ctx.newPage();
  const opens = [];
  await page.route('**/api/page-open', async route => {
    const req = route.request();
    opens.push([req.method(), req.headers()['content-type'], req.postDataJSON().page]);
    await route.fulfill({ json: { ok: true } });
  });
  await page.goto('/#/health');
  await page.waitForFunction(() => location.hash === '#/health' && document.querySelector('#main .panel'));
  await page.evaluate(() => { location.hash = '#/cpu'; });
  await page.waitForFunction(() => location.hash === '#/cpu' && document.querySelector('#main .panel'));
  await page.evaluate(() => { location.hash = '#/cpu?range=1h'; });   // الصفحة نفسها بمعاملات مختلفة: لا بلاغ ثانيًا
  await page.waitForTimeout(400);
  await page.evaluate(() => { location.hash = '#/ctrl'; });
  await page.waitForFunction(() => location.hash === '#/ctrl' && document.querySelector('#main .panel'));
  await page.waitForTimeout(400);
  expect(opens).toEqual([['POST', 'application/json', 'health'], ['POST', 'application/json', 'cpu'], ['POST', 'application/json', 'ctrl']]);

  const panel = page.locator('#main .panel', { hasText: 'استخدام الصفحات' });
  await expect(panel).toBeVisible();
  const text = await panel.textContent();
  expect(text).toContain('8 من 14 يومًا');          // الثابت: since=2026-10-20 وelapsedDays=8 (المدة لم تكتمل)
  expect(text).toContain('2026-10-20');
  expect(text).toContain('نظرة عامة');               // overview في الثابت بـ 12 فتحة
  expect(text).toContain('12');
  expect(text).toContain('لم تُفتح');                // الصفحات الغائبة عن الثابت تظهر بصفر
  const rows = await panel.locator('tbody tr').count();
  const keys = await page.evaluate(() => Object.keys(PAGES).length);
  expect(rows).toBe(keys);                           // كل صفحة في PAGES لها صف، مفتوحة أو لا
  expect(await panel.locator('tbody tr').first().textContent()).toContain('نظرة عامة');   // الأكثر فتحًا أولًا
});
