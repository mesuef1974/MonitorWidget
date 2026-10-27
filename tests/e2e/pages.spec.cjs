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
