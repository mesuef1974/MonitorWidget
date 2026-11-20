// بنود الواجهة من مراجعة المنطق 19/11/2026 (خادم الثوابت): زر «تحرير الالتزام» يكشف خطة الالتزام (كان تنظيف الذاكرة)، ورد 428 لا يُعاد بلا حوار،
// والقراءات القديمة للمعالج والرسوميات لا تُعرض حيّة، وتوقع المساحة لا يعرض «+0.00» في حالة الجمع وإشارته إشارة المساحة الحرة،
// و«متوسط 10 دقائق» للجهاز كله من سلسلة المجموع لا مجموع أعلى 8 تطبيقات.
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const FIX = path.join(__dirname, '..', 'fixtures', 'api');
const fixture = name => JSON.parse(fs.readFileSync(path.join(FIX, name + '.json'), 'utf8'));
const kpiValue = (page, panelTitle, label) => page.locator('#main section.panel', { has: page.locator('h2', { hasText: panelTitle }) })
  .locator('.kpi', { has: page.locator('.l', { hasText: label }) }).locator('.v');

// «تحرير الالتزام» حلّ محلّ «تنظيف الذاكرة» (قرار المالك 20/11/2026 بعد قياس deep): الزر لا ينظّف بل يكشف خطة الالتزام
const planFocused = page => page.waitForFunction(() => !!document.activeElement?.closest('#commitPlan'));

test('زر «تحرير الالتزام» يفتح خطة الالتزام في «الذاكرة» ولا ينظّف، ولكل خطوة فعلها', async ({ page }) => {
  const trims = [];
  await page.route('**/api/trim-memory', r => { trims.push(1); return r.fulfill({ json: { ok: true } }); });
  await page.goto('/#/overview');
  await page.waitForFunction(() => document.querySelector('#main .panel'));
  await expect(page.locator('#boostTxt')).toHaveText('تحرير الالتزام');
  await page.locator('#boost').click();
  await page.waitForFunction(() => location.hash === '#/mem');
  await planFocused(page);
  const plan = page.locator('#commitPlan');
  await expect(plan.locator('[data-gpupurge]')).toHaveCount(1);                                   // مشروع على كرت الشاشة: تفريغ النماذج
  await expect(plan.locator('[data-dockerstop="demo-preview-a"][data-dockerpr="merged"]')).toHaveCount(1);   // حاويات طلب مدموج
  await expect(plan).toContainText('يحرّر نحو');
  await page.evaluate(() => { scrollTo(0, 0); document.activeElement?.blur(); });
  await page.locator('#boost').click();                                                           // وهو في «الذاكرة»: يكشف الخطة دون انتقال
  await planFocused(page);
  expect(trims).toEqual([]);
});

test('رابط الويدجت ‎#/mem?plan=1‎ يكشف الخطة ويُحذف الوسيط من الرابط', async ({ page }) => {
  await page.goto('/#/mem?plan=1');
  await planFocused(page);
  expect(await page.evaluate(() => location.hash)).toBe('#/mem');
});

test('رد 428 لا يُعاد آليًّا: حوار تأكيد أولًا، والرفض لا يرسل الرمز', async ({ page }) => {
  const bodies = [];
  await page.route('**/api/docker-stop', r => {
    const b = JSON.parse(r.request().postData()); bodies.push(b);
    return b.confirm ? r.fulfill({ json: { ok: true } }) : r.fulfill({ status: 428, json: { error: 'confirm_required', confirm: 'tok1', ttl: 120 } });
  });
  await page.goto('/#/overview');
  await page.waitForFunction(() => typeof post === 'function');
  let res = page.evaluate(() => post('/api/docker-stop', { project: 'x' }).then(() => 'ok', e => e.message));
  await page.getByRole('button', { name: 'إلغاء' }).click();
  expect(await res).toBe('أُلغيت العملية');
  expect(bodies).toEqual([{ project: 'x' }]);
  res = page.evaluate(() => post('/api/docker-stop', { project: 'x' }).then(() => 'ok', e => e.message));
  await page.getByRole('button', { name: 'تابع' }).click();
  expect(await res).toBe('ok');
  expect(bodies.slice(1)).toEqual([{ project: 'x' }, { project: 'x', confirm: 'tok1' }]);
  // المستدعي الذي أكّد بحواره يمرّر confirmed فلا يظهر حوار ثانٍ
  res = await page.evaluate(() => post('/api/docker-stop', { project: 'y' }, true).then(() => 'ok', e => e.message));
  expect(res).toBe('ok');
  await expect(page.locator('dialog')).toHaveCount(0);
});

test('المعالج والرسوميات: القراءة القديمة معلّمة ولا تُعرض حيّة', async ({ page }) => {
  const home = fixture('home');
  await page.route('**/api/home', r => r.fulfill({ json: { ...home, cpu: { ...home.cpu, age: 900 }, gpu: { ...home.gpu, age: 1200 } } }));
  await page.route('**/api/cpu', r => r.fulfill({ json: { ...fixture('cpu'), stale: true } }));
  await page.goto('/#/overview');
  const gpu = page.locator('#main section.panel', { has: page.locator('h2', { hasText: /^الرسوميات$/ }) });
  await expect(gpu.locator('.tag.warn')).toContainText('قراءة قديمة · قبل 20 د');
  await expect(gpu.locator('.meter')).toHaveCount(0);
  const g = page.locator('#main .gauge', { has: page.locator('.gl', { hasText: 'المعالج' }) });
  await expect(g.locator('.gv')).toHaveText('—');
  await expect(g.locator('.gs')).toContainText('قراءة قديمة · قبل 15 د');
  await page.goto('/#/procs');
  const now = page.locator('#main section.panel', { has: page.locator('h2', { hasText: 'الحمل الحالي' }) });
  await expect(now.locator('.meta .tag.warn')).toContainText('قراءة قديمة');
  await expect(now.locator('.meter')).toHaveCount(0);
});

test('المعالج والرسوميات: القراءة الحديثة بلا علامة قِدم', async ({ page }) => {
  await page.goto('/#/overview');
  const gpu = page.locator('#main section.panel', { has: page.locator('h2', { hasText: /^الرسوميات$/ }) });
  await expect(gpu.locator('.meter').first()).toBeVisible({ timeout: 30000 });
  await expect(page.locator('#main')).not.toContainText('قراءة قديمة');
});

test('توقع المساحة: حالة الجمع «—» لا «+0.00»، والإشارة إشارة المساحة الحرة', async ({ page }) => {
  const h = fixture('health');
  let fc = { freeGB: 106.4, slopeGBday: null, daysLeft: null, basis: 'قياسات', points: 2, state: 'جمع' };
  await page.route('**/api/health', r => r.fulfill({ json: { ...h, forecast: fc } }));
  await page.goto('/#/health');
  const v = kpiValue(page, 'توقع المساحة', 'تغيّر المساحة الحرة');
  await expect(v).toHaveText('—', { timeout: 30000 });
  await expect(page.locator('#main')).not.toContainText('+0.00');
  fc = { ...fc, slopeGBday: -1.5, daysLeft: 71, points: 12, state: 'جيد' };
  await page.reload();
  await expect(v).toContainText('−1.50', { timeout: 30000 });
  await expect(page.locator('#main section.panel', { has: page.locator('h2', { hasText: 'توقع المساحة' }) })).toContainText('المساحة الحرة تتناقص');
  fc = { ...fc, slopeGBday: 0.88, daysLeft: null, state: 'مستقر' };
  await page.reload();
  await expect(v).toContainText('+0.88', { timeout: 30000 });
});

test('متوسط 10 دقائق للجهاز كله من سلسلة المجموع لا مجموع أعلى 8 تطبيقات', async ({ page }) => {
  const c = fixture('cpu'), t10 = c.ts - 600, w = c.series.v.filter((v, i) => c.series.t[i] >= t10);
  const expected = (w.reduce((a, v) => a + v, 0) / w.length).toLocaleString('en', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const top8 = c.avg10.reduce((a, x) => a + x.avg, 0).toLocaleString('en', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  await page.goto('/#/procs');
  const v = kpiValue(page, 'الحمل الحالي', 'متوسط 10 دقائق');
  await expect(v).toHaveText(expected + 'نواة', { timeout: 30000 });
  expect(w.length).toBeGreaterThan(1);
  if (top8 !== expected) await expect(v).not.toHaveText(top8 + 'نواة');
});
