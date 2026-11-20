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
  await expect(panelOf(page, 'مسبّبات بطء الإقلاع').locator('.empty')).toContainText('لا قياس لزمن الإقلاع');
});

test('صفحة الصحة: تفصيل زمن الإقلاع ومسبّبات البطء مجموعةً بالاسم ومرتّبةً بالتأخير', async ({ page }) => {
  await page.goto('/#/health');
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  const rows = panelOf(page, 'زمن الإقلاع').locator('.list .li');
  await expect(rows.nth(0).locator('.d')).toContainText('حتى سطح المكتب 22.1');
  await expect(rows.nth(0).locator('.d')).toContainText('بعده 38.2');
  await expect(rows.nth(0).locator('.d')).toContainText('الأبطأ: Claude (30.3');   // الأكبر تأخيرًا لا الأول ورودًا
  await expect(rows.nth(3).locator('.d')).toHaveCount(0);                          // إقلاع من فحص قديم: بلا تفصيل ولا مسبّب
  const cul = panelOf(page, 'مسبّبات بطء الإقلاع');
  const tr = cul.locator('tbody tr');
  await expect(tr).toHaveCount(4);                                                 // المسبّب خارج نافذة الإقلاعات لا يظهر
  await expect(cul).not.toContainText('خارج النافذة');
  await expect(tr.nth(0)).toContainText('Claude');
  await expect(tr.nth(0)).toContainText('تطبيق');
  await expect(tr.nth(0)).toContainText('2 من 5');
  await expect(tr.nth(0).locator('.tag.bad')).toContainText('28.5');               // متوسط 30.3 و26.7
  await expect(tr.nth(0).locator('td.n').last()).toContainText('30.3');
  await expect(tr.nth(1)).toContainText('Web Management Service');
  await expect(tr.nth(1)).toContainText('خدمة');
  await expect(tr.nth(1).locator('.tag.warn')).toContainText('3.6');
  await expect(tr.nth(2).locator('td').first()).toHaveText('<b>Audio</b> Device'); // الاسم نصّ لا HTML، ومربوط بإقلاعه بفارق 40 ث
  await expect(tr.nth(2).locator('b')).toHaveCount(0);
  await expect(tr.nth(2)).toContainText('جهاز');
  await expect(tr.nth(3).locator('.tag.ok')).toContainText('0.3');
});

test('صفحة الصحة: فحص مرتفع قديم بلا مسبّبات يطلب تشغيل المثبّت، وقائمة فارغة تقول لا مسبّب', async ({ page }) => {
  let mode = 'old';
  await page.route('**/api/health', async route => {
    const res = await route.fetch(); const body = await res.json();
    if (mode === 'old') delete body.hw.bootCulprits; else body.hw.bootCulprits = [];
    await route.fulfill({ response: res, json: body });
  });
  await page.goto('/#/health');
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  await expect(panelOf(page, 'مسبّبات بطء الإقلاع').locator('.empty')).toContainText('شغّل المثبّت المحمي كمسؤول');
  await expect(panelOf(page, 'زمن الإقلاع').locator('.list .li').nth(0).locator('.d')).not.toContainText('الأبطأ');
  mode = 'none';
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  await expect(panelOf(page, 'مسبّبات بطء الإقلاع').locator('.empty')).toContainText('لم يسجّل ويندوز مسبّبًا');
});

test('القائمة بلا «السجل» و«القاعدة» و«اسأل المرصد» و«حركة القرص»، وكلها تعمل برابط من صفحة التحكم', async ({ page }) => {
  await page.goto('/#/ctrl');
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  await expect(page.locator('#nav a[href="#/history"]')).toHaveCount(0);
  await expect(page.locator('#nav a[href="#/dbx"]')).toHaveCount(0);
  await expect(page.locator('#nav a[href="#/ask"]')).toHaveCount(0);   // البند 31 من المجلس 19/11
  await expect(page.locator('#nav a[href="#/io"]')).toHaveCount(0);
  await expect(panelOf(page, 'صفحات خارج القائمة').locator('a[href="#/ask"], a[href="#/io"]')).toHaveCount(2);
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

test('صفحة التحكم: لوحة الويدجت تعرض حالته وسقطاته وتوقفات واجهته وآخر خطأ نصًّا لا HTML', async ({ page }) => {
  await page.goto('/#/ctrl');
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  const w = panelOf(page, 'الويدجت');
  await expect(w.locator('.tag.bad')).toHaveText('سقط');
  await expect(w.locator('.kpi', { hasText: 'سقطات' })).toContainText('2');
  await expect(w.locator('.kpi', { hasText: 'توقفات الواجهة' })).toContainText('37');
  await expect(w.locator('.kpi', { hasText: 'توقفات الواجهة' })).toContainText('93.7');
  await expect(w.locator('.kpi', { hasText: 'إعادات تشغيل آلية' })).toContainText('1');
  await expect(w.locator('p.note').last().locator('bdi[dir=ltr]')).toContainText('<b>op_Subtraction</b>');   // النص الإنجليزي معزول باتجاهه
  await expect(w.locator('p.note b')).toHaveCount(0);
  let mode = 'running';
  await page.route('**/api/apphealth', async route => {
    const res = await route.fetch(); const body = await res.json();
    if (mode === 'running') body.widget = { running: true, state: 'running', starts: 1, fatals: 0, stalls: 0, stallMaxS: 0, stallSumS: 0, lastStart: 1793015330, lastFatal: null, lastLayout: null, relaunches: 0, wanted: false };
    else body.widget = null;
    await route.fulfill({ response: res, json: body });
  });
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  await expect(panelOf(page, 'الويدجت').locator('.tag.ok')).toHaveText('يعمل');
  await expect(panelOf(page, 'الويدجت').locator('.tag', { hasText: 'مخفيّ من التطبيق' })).toHaveCount(1);
  await expect(panelOf(page, 'الويدجت').locator('p.note')).toHaveCount(0);
  mode = 'none';
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  await expect(panelOf(page, 'الويدجت').locator('.empty')).toContainText('غير متاح');
});

test('صفحة الصحة: اتجاه زمن الإقلاع يعرض الحالة والمرحلة والمسبّب الجديد نصًّا والرسم، ونوع الخدمة المصحَّح', async ({ page }) => {
  let mode = 'slower';
  await page.route('**/api/health', async route => {
    const res = await route.fetch(); const body = await res.json();
    if (mode === 'learning') body.bootTrend = { n: 2, need: 7, state: 'learning', recentSec: 40.1, baseSec: null, deltaSec: null, deltaPct: null, phase: null, newCulprits: [], lastTs: 1793015330, points: [{ ts: 1793015330, bootSec: 40.1 }] };
    if (mode === 'empty') body.bootTrend = { n: 0, need: 7, state: 'learning', recentSec: null, baseSec: null, deltaSec: null, deltaPct: null, phase: null, newCulprits: [], lastTs: null, points: [] };
    if (mode === 'none') body.bootTrend = null;
    await route.fulfill({ response: res, json: body });
  });
  await page.goto('/#/health');
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  const tr = panelOf(page, 'اتجاه زمن الإقلاع');
  await expect(tr.locator('.tag.bad')).toContainText('أبطأ من المعتاد');
  await expect(tr.locator('.tag.bad')).toHaveText('أبطأ من المعتاد');                    // الفرق في وسم مستقل لا داخل وسم الحالة
  await expect(tr.locator('.pill-row .tag').nth(1)).toContainText('11.2');
  await expect(tr.locator('.tag.warn').nth(0)).toHaveText('الزيادة بعد ظهور سطح المكتب');
  await expect(tr.locator('.tag.warn').nth(1)).toContainText('<i>New</i> Updater');   // الاسم نصّ لا HTML
  await expect(tr.locator('.tag i')).toHaveCount(0);
  await expect(tr.locator('.kpi .l', { hasText: 'المعتاد' })).toHaveCount(1);
  await expect(tr.locator('#btChart svg')).toHaveCount(1);
  const row = panelOf(page, 'مسبّبات بطء الإقلاع').locator('tbody tr').filter({ hasText: 'Lenovo.Modern.ImController' });
  await expect(row.locator('td').nth(1)).toHaveText('خدمة');   // حدث 101 (تطبيق) لكن الخادم عرفه خدمة
  mode = 'learning';
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  await expect(tr.locator('.tag').first()).toContainText('يتعلّم المعتاد');
  await expect(tr.locator('.tag').first()).toContainText('2 من 7');
  await expect(tr.locator('#btChart')).toHaveCount(0);   // نقطة واحدة لا تُرسم
  mode = 'empty';
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  await expect(tr.locator('.empty')).toContainText('لا إقلاع محفوظ بعد');
  mode = 'none';
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  await expect(tr.locator('.empty')).toHaveText('غير متاح');
});

test('صفحة الطاقة: السكون والاستيقاظ بجلساته وأسبابه ومصادره، والفوري والبطيء معلَّمان، وحالتاه الفارغتان', async ({ page }) => {
  let mode = 'full';
  await page.route('**/api/power*', async route => {
    const res = await route.fetch(); const body = await res.json();
    if (mode === 'empty') body.sleep = { days: 14, n: 0, instant: 0, slow: 0, avgWakeS: null, maxWakeS: null, slowest: null, instantSources: [], items: [] };
    if (mode === 'none') { body.sleep = null; body.status.noBattery = true; }
    await route.fulfill({ response: res, json: body });
  });
  await page.goto('/#/power');
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 4);
  const sl = panelOf(page, 'السكون والاستيقاظ');
  await expect(sl.locator('.kpi .l', { hasText: 'مرات السكون' })).toHaveCount(1);
  await expect(sl.locator('.kpi .l', { hasText: 'استيقاظ قبل دقيقتين' })).toHaveCount(1);
  const rows = sl.locator('tbody tr');
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText('خمول الجهاز');
  await expect(rows.nth(0).locator('.tag')).toHaveCount(0);
  await expect(rows.nth(1).locator('.tag.warn')).toHaveCount(2);                      // المدة القصيرة والخروج البطيء
  await expect(rows.nth(1).locator('td').nth(3)).toHaveText('جهاز: <b>USB</b> Root Hub'); // النص من سجل ويندوز لا يُفسَّر HTML
  await expect(rows.nth(1).locator('b')).toHaveCount(0);
  await expect(rows.nth(2).locator('.tag', { hasText: 'سبات' })).toHaveCount(1);
  await expect(rows.nth(2).locator('td').nth(2)).toHaveText('—');
  await expect(sl.locator('p.note .tag')).toHaveCount(2);                                // الأجهزة المسموح لها بالإيقاظ
  await expect(sl.locator('p.note .tag').nth(0)).toHaveText('<u>HID</u> Keyboard Device'); // اسم الجهاز نصّ لا HTML
  await expect(sl.locator('p.note u')).toHaveCount(0);
  await expect(sl.locator('p.note.ellipsis').first()).toHaveText('powercfg /devicedisablewake "<u>HID</u> Keyboard Device"');
  await expect(sl.locator('p.note.ellipsis').last()).toHaveText('powercfg /systemsleepdiagnostics');
  mode = 'empty';
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 4);
  await expect(sl.locator('.empty')).toContainText('لا سكون مسجّل خلال 14');
  mode = 'none';
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 2);
  await expect(sl.locator('.empty')).toHaveText('غير متاح');   // تظهر حتى بلا بطارية: السكون يخصّ المكتبي أيضًا
});

test('البند 30: لا لوحة WSL في «الذاكرة» (مكانها «WSL وDocker»)، ولا لوحة الويدجت في «الصحة» (مكانها «التحكم»)', async ({ page }) => {
  await page.goto('/#/mem');
  await page.waitForFunction(() => document.querySelectorAll('#main .panel').length > 3);
  await expect(panelOf(page, 'إعداد WSL')).toHaveCount(0);
  await expect(panelOf(page, 'أكبر البرامج')).toHaveClass('panel c12');
  await page.evaluate(() => { location.hash = '#/wsl'; });
  await page.waitForFunction(() => location.hash === '#/wsl' && document.querySelectorAll('#main .panel').length > 3);
  await expect(panelOf(page, 'إعداد .wslconfig')).toHaveCount(1);
  await page.evaluate(() => { location.hash = '#/health'; });
  await page.waitForFunction(() => location.hash === '#/health' && document.querySelectorAll('#main .panel').length > 3);
  await expect(page.locator('#main .panel .ph h2').filter({ hasText: /^الويدجت$/ })).toHaveCount(0);
  await expect(page.locator('#main .banner.warn')).toHaveCount(0);
});
