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
  await page.evaluate(() => { location.hash = '#/procs'; });
  await page.waitForFunction(() => location.hash === '#/procs' && document.querySelector('#main .panel'));
  await page.evaluate(() => { location.hash = '#/procs?range=1h'; });   // الصفحة نفسها بمعاملات مختلفة: لا بلاغ ثانيًا
  await page.waitForTimeout(400);
  await page.evaluate(() => { location.hash = '#/ctrl'; });
  await page.waitForFunction(() => location.hash === '#/ctrl' && document.querySelector('#main .panel'));
  await page.waitForTimeout(400);
  expect(opens).toEqual([['POST', 'application/json', 'health'], ['POST', 'application/json', 'procs'], ['POST', 'application/json', 'ctrl']]);

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

// البند 26 من المجلس 19/11: المظهر في مكان واحد بالواجهة (لوحة «المظهر» في التحكم) والرابط القديم يصل إليها
test('المظهر في مكان واحد: لا صفحة «المظهر الموحد» في القائمة، و#/appearance يفتح التحكم بعيّنات الألوان', async ({ page }) => {
  await page.goto('/#/appearance');
  await page.waitForFunction(() => document.querySelectorAll('#main .swatch[data-palette]').length === 4);
  expect(await page.evaluate(() => location.hash)).toBe('#/ctrl');
  expect(await page.locator('#nav a[href="#/appearance"]').count()).toBe(0);
  expect(await page.locator('#main [data-mode]').count()).toBe(3);
  expect(await page.evaluate(() => typeof PAGES.appearance)).toBe('undefined');
});

// البند 27 من المجلس 19/11: تنظيف الذاكرة من مدخل واحد في الواجهة (زر الشريط) مع الويدجت
test('تنظيف الذاكرة من زر الشريط وحده: لا زر تنظيف في صفحة التحكم', async ({ page }) => {
  await page.goto('/#/ctrl');
  await page.waitForFunction(() => document.querySelectorAll('#main .swatch[data-palette]').length === 4);
  expect(await page.locator('#main [data-trim]').count()).toBe(0);
  expect(await page.locator('#boost').count()).toBe(1);
});

// البند 28 من المجلس 19/11: صفحة «المعالج» دُمجت في «العمليات»؛ الرابط القديم يصل إليها برسمها البياني
test('المعالج داخل العمليات: لا رابط #/cpu في القائمة، و#/cpu يفتح العمليات بالحمل والرسم والجداول', async ({ page }) => {
  await page.goto('/#/cpu');
  await page.waitForFunction(() => document.querySelector('#cpuChart svg'));
  expect(await page.evaluate(() => location.hash)).toBe('#/procs');
  expect(await page.locator('#nav a[href="#/cpu"]').count()).toBe(0);
  expect(await page.evaluate(() => typeof PAGES.cpu)).toBe('undefined');
  for (const h of ['الحمل الحالي', 'المعالج الكلي خلال ساعة', 'الملخص', 'متوسط آخر ساعة'])
    expect(await page.locator('#main section.panel h2', { hasText: h }).count(), h).toBeGreaterThan(0);
});

// البند 29 (أ) من المجلس 19/11: «المساحة» و«تنظيف المساحة» صفحة واحدة بقسمين؛ الرابط القديم يصل إليها
test('المساحة وتنظيفها صفحة واحدة: #/spaceclean يفتح #/space بقسم التحليل ثم قسم التنظيف', async ({ page }) => {
  test.setTimeout(60000);
  await page.goto('/#/spaceclean');
  await page.waitForFunction(() => location.hash === '#/space' && document.querySelectorAll('#main section.panel').length > 6, null, { timeout: 45000 });
  expect(await page.locator('#nav a[href="#/spaceclean"]').count()).toBe(0);
  expect(await page.evaluate(() => typeof PAGES.spaceclean)).toBe('undefined');
  const titles = await page.locator('#main section.panel h2').allTextContents();
  const c = titles.indexOf('القرص C'), q = titles.findIndex(t => t.startsWith('الحجر'));
  expect(c, titles.join(' | ')).toBe(0);
  expect(q, titles.join(' | ')).toBeGreaterThan(c);
  expect(titles).not.toContain('حالة التنظيف');
  await expect(page.locator('#main section.panel', { has: page.locator('h2', { hasText: /^القرص C$/ }) })).toContainText('محجوز بالحجر');
});

// البند 29-ب من المجلس 19/11: «التوصيات» و«التنبيهات» صندوق «يحتاج انتباهك» أعلى «نظرة عامة»؛ الحدود في التحكم
test('يحتاج انتباهك: أول لوحة في نظرة عامة بالتوصيات وتنبيهات 24 ساعة، والصفحتان القديمتان تُحوَّلان إليها', async ({ page }) => {
  const now = Math.floor(Date.now() / 1000);
  await page.route('**/api/alerts', async r => {
    const res = await r.fetch(), d = await res.json();
    d.log = [{ ts: now - 600, source: 'disk', sev: 'bad', title: 'تنبيه حديث للاختبار', detail: '' }, ...d.log];
    await r.fulfill({ response: res, json: d });
  });
  for (const h of ['#/advice', '#/alerts']) {
    await page.goto('/' + h);
    await page.waitForFunction(() => location.hash === '#/overview' && document.querySelector('#main section.panel h2'));
  }
  const first = page.locator('#main section.panel').first();
  await expect(first.locator('h2')).toHaveText('يحتاج انتباهك');
  await expect(first.locator('.card.lvl-warn')).toHaveCount(1);
  await expect(first.locator('.card.lvl-ok')).toHaveCount(0);   // «سليم» لا يظهر مع بنود أهم
  await expect(first.locator('details.att-log[open]')).toContainText('تنبيه حديث للاختبار');   // تنبيه حرج يفتح القائمة
  await expect(first.locator('details.att-log .li')).toHaveCount(1);   // تنبيهات الثوابت أقدم من 24 ساعة
  for (const k of ['advice', 'alerts']) {
    expect(await page.locator(`#nav a[href="#/${k}"]`).count()).toBe(0);
    expect(await page.evaluate(x => typeof PAGES[x], k)).toBe('undefined');
  }
  await page.goto('/#/ctrl');
  await expect(page.locator('#alForm h2')).toHaveText('حدود التنبيه');
});
