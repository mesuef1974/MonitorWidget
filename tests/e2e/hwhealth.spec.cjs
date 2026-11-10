// صحة العتاد: لوحتا «صحة البطارية» و«الإيقافات المفاجئة» في صفحة الطاقة، و«زمن الإقلاع» في صفحة الصحة،
// وخروج «السجل» و«القاعدة» من القائمة مع بقائهما برابط، وتجاهل العدّاد للجولات الآلية.
const { test, expect } = require('@playwright/test');

const panelOf = (page, title) => page.locator('#main .panel').filter({ has: page.locator('.ph h2', { hasText: title }) });

test('صفحة الطاقة: صحة البطارية وتحذير الإجراء الحرج، والإيقافات بأسبابها، والملاحظات بالعربية', async ({ page }) => {
  await page.goto('/#/power');
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 4);
  const batt = panelOf(page, 'صحة البطارية');
  await expect(batt.locator('.kpi .l', { hasText: 'السعة الآن' })).toHaveCount(1);
  await expect(batt.locator('.kpi .l', { hasText: 'التقادم' })).toHaveCount(1);
  await expect(batt.locator('.kpi .l', { hasText: 'دورات الشحن' })).toHaveCount(1);
  await expect(batt.locator('.tag.bad')).toContainText('سبات');             // الثابت: الإجراء «سبات» والسبات معطّل
  await expect(batt.locator('.tag', { hasText: 'السبات معطّل' })).toHaveCount(1);
  await expect(batt.locator('p.note')).toContainText('ينطفئ فجأة');
  const shut = panelOf(page, 'الإيقافات المفاجئة');
  await expect(shut.locator('tbody tr')).toHaveCount(3);
  await expect(shut.locator('tbody tr').nth(0)).toContainText('فرغت البطارية');
  await expect(shut.locator('tbody tr').nth(0)).toContainText('على البطارية');
  await expect(shut.locator('tbody tr').nth(2).locator('td.n').first()).toHaveText('—');   // بلا قراءة بطارية
  const notes = panelOf(page, 'ملاحظات');
  await expect(notes.locator('.li').first()).toContainText('إجراء البطارية الحرجة لا ينفع');
  await expect(notes.locator('.li .tag').first()).toHaveText('خطر');
});

test('صفحة الطاقة: الإجراء السليم أخضر، ولا إيقافات = حالة فارغة، وبلا بطارية لا لوحتان', async ({ page }) => {
  let mode = 'ok';
  await page.route('**/api/power*', async route => {
    const res = await route.fetch(); const body = await res.json();
    if (mode === 'ok') {
      body.critical = { ok: true, action: 3, actionName: 'إيقاف تشغيل', actionAc: 3, level: 5, lowLevel: 10, hibernate: false, risk: null, riskText: null };
      body.shutdowns = { days: 14, items: [], counts: {} };
    } else { body.status.noBattery = true; }
    await route.fulfill({ response: res, json: body });
  });
  await page.goto('/#/power');
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 4);
  const batt = panelOf(page, 'صحة البطارية');
  await expect(batt.locator('.tag.ok')).toContainText('إيقاف تشغيل');
  await expect(batt.locator('.tag.bad')).toHaveCount(0);
  await expect(panelOf(page, 'الإيقافات المفاجئة').locator('.empty')).toContainText('لا إيقاف مفاجئ');
  mode = 'nobatt';
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 2);
  await expect(panelOf(page, 'صحة البطارية')).toHaveCount(0);
  await expect(panelOf(page, 'الإيقافات المفاجئة')).toHaveCount(0);
});

test('صفحة الصحة: زمن الإقلاع من الفحص المرتفع، وحالة فارغة حين لا قياس', async ({ page }) => {
  await page.goto('/#/health');
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  const boot = panelOf(page, 'زمن الإقلاع');
  await expect(boot.locator('.kpi .l', { hasText: 'آخر إقلاع' })).toHaveCount(1);
  await expect(boot.locator('.kpi .l', { hasText: 'المتوسط' })).toHaveCount(1);
  await expect(boot.locator('.kpi', { hasText: 'آخر إقلاع' })).toContainText('60.3');   // أحدث قياس في الثابت
  await expect(boot.locator('.kpi', { hasText: 'الأبطأ' })).toContainText('74.1');
  await expect(boot.locator('.list .li')).toHaveCount(5);
  await page.route('**/api/health', async route => {
    const res = await route.fetch(); const body = await res.json(); body.hw.boots = [];
    await route.fulfill({ response: res, json: body });
  });
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  await expect(panelOf(page, 'زمن الإقلاع').locator('.empty')).toContainText('لا قياس لزمن الإقلاع');
});

test('القائمة بلا «السجل» و«القاعدة»، وهما تعملان برابط من صفحة التحكم', async ({ page }) => {
  await page.goto('/#/ctrl');
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  await expect(page.locator('#nav a[href="#/history"]')).toHaveCount(0);
  await expect(page.locator('#nav a[href="#/dbx"]')).toHaveCount(0);
  await expect(page.locator('#nav a[href="#/ctrl"]')).toHaveCount(1);
  const other = panelOf(page, 'صفحات خارج القائمة');
  await other.locator('a[href="#/history"]').click();
  await page.waitForFunction(() => document.querySelector('#title')?.textContent === 'السجل' && document.querySelector('#main .panel'));
  await page.goto('/#/ctrl');
  await page.waitForFunction(() => document.querySelector('#title')?.textContent === 'الإعدادات والتحكم' && document.querySelector('#main .panel'));
  await panelOf(page, 'صفحات خارج القائمة').locator('a[href="#/dbx"]').click();
  await page.waitForFunction(() => document.querySelector('#title')?.textContent === 'قاعدة البيانات' && document.querySelector('#main .panel'));
});

test.describe('عدّاد فتح الصفحات والجولات الآلية', () => {
  const visit = async page => {
    const opens = [];
    page.on('request', r => { if (r.url().endsWith('/api/page-open')) opens.push(JSON.parse(r.postData()).page); });
    await page.goto('/#/overview');
    await page.waitForFunction(() => document.querySelector('#main .panel'));
    await page.goto('/#/power');
    await page.waitForFunction(() => document.querySelector('#title')?.textContent === 'الطاقة والحرارة');
    await page.waitForTimeout(300);
    return opens;
  };
  test('المتصفح المُساق آليًا (navigator.webdriver) لا يُعدّ فتحًا', async ({ page }) => {
    expect(await page.evaluate(() => navigator.webdriver)).toBe(true);
    expect(await visit(page)).toEqual([]);
  });
  test('متصفح المالك (لا webdriver ولا Headless) يُعدّ فتحه لكل صفحة مرة', async ({ browser }) => {
    const ctx = await browser.newContext({ userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0' });
    await ctx.addInitScript(() => Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => false }));
    const page = await ctx.newPage();
    expect(await visit(page)).toEqual(['overview', 'power']);
    await ctx.close();
  });
  test('متصفح بلا واجهة (Headless في user agent) لا يُعدّ ولو أخفى webdriver', async ({ browser }) => {
    const ctx = await browser.newContext({ userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/140.0.0.0 Safari/537.36' });
    await ctx.addInitScript(() => Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => false }));
    const page = await ctx.newPage();
    expect(await visit(page)).toEqual([]);
    await ctx.close();
  });
});
