// فحص الإعلانات والدرج في web/index.html على خادم الثوابت (البند (ب) من مراجعة الواجهة):
// مؤشر «مباشر» لا يعلن مع كل تحديث بل عند الانتقال بين مباشر ومتعذّر فقط، بنرات الخطأ role=alert،
// والدرج على الشاشة الضيقة: الفتح ينقل التركيز إلى أول رابط، Esc يغلقه ويعيد التركيز إلى الزر، وروابطه خارج Tab وهو مغلق.
const { test, expect } = require('@playwright/test');

const ready = page => page.waitForFunction(() => document.querySelector('#main .panel'));
const liveState = page => page.locator('#liveState').textContent();

test('مؤشر «مباشر» يعلن عند تغيّر الحالة فقط؛ الوقت المرئي خارج المنطقة الحية', async ({ page }) => {
  await page.goto('/#/cpu'); await ready(page);
  expect(await page.locator('#live').getAttribute('role')).toBeNull();
  expect(await page.locator('#liveState').getAttribute('role')).toBe('status');
  expect(await liveState(page)).toBe('');                                   // أول نجاح لا يُعلن
  const t1 = await page.locator('#liveTxt').textContent();
  await page.waitForTimeout(1100);
  await page.evaluate(() => show());
  await page.waitForFunction(t => document.querySelector('#liveTxt').textContent !== t, t1);   // الوقت المرئي تغيّر
  expect(await liveState(page)).toBe('');                                   // ولم يُعلن شيء

  await page.route('**/api/cpu', route => route.fulfill({ status: 500, json: { error: 'x' } }));
  await page.evaluate(() => show());
  await page.waitForFunction(() => document.querySelector('#live').classList.contains('err'));
  expect(await liveState(page)).toBe('تعذّر التحديث');
  await page.evaluate(() => show());
  await page.waitForTimeout(300);
  expect(await liveState(page)).toBe('تعذّر التحديث');                       // تكرار الفشل لا يكرّر الإعلان

  await page.unroute('**/api/cpu');
  await page.evaluate(() => show());
  await page.waitForFunction(() => !document.querySelector('#live').classList.contains('err'));
  expect(await liveState(page)).toBe('عاد التحديث المباشر');
});

test('بنرات الخطأ تحمل role=alert: في المصدر كلها، وحيًّا عند تعذّر الاتصال', async ({ page }) => {
  const src = await (await page.request.get('/')).text();
  expect(src.match(/<div class="banner"(?![^>]*role=)/g)).toBeNull();
  expect((src.match(/<div class="banner" role="alert">/g) || []).length).toBeGreaterThan(1);
  expect((src.match(/return nodata\("/g) || []).length).toBeGreaterThan(15);   // بنرات «تعذّر قراءة…» عبر دالة واحدة

  await page.route('**/api/cpu', route => route.fulfill({ status: 500, json: { error: 'x' } }));
  await page.goto('/#/cpu');
  const banner = page.locator('#main .banner');
  await expect(banner).toBeVisible();
  expect(await banner.getAttribute('role')).toBe('alert');
  expect(await banner.textContent()).toContain('تعذّر الاتصال بالخادم');
});

test('الدرج الضيق: الفتح ينقل التركيز إلى أول رابط، Esc يغلقه ويعيد التركيز إلى الزر، وروابطه مخفية وهو مغلق', async ({ page }) => {
  await page.setViewportSize({ width: 600, height: 800 });
  await page.goto('/#/overview'); await ready(page);
  const nav = page.locator('#nav'), btn = page.locator('#menuBtn');
  await expect(btn).toBeVisible();
  await page.waitForTimeout(400);
  expect(await nav.evaluate(n => getComputedStyle(n).visibility)).toBe('hidden');   // مغلق: خارج Tab وقارئ الشاشة
  expect(await btn.getAttribute('aria-expanded')).toBe('false');

  await btn.click();
  expect(await btn.getAttribute('aria-expanded')).toBe('true');
  expect(await nav.evaluate(n => getComputedStyle(n).visibility)).toBe('visible');
  expect(await page.evaluate(() => document.activeElement.closest('#nav a') === document.querySelector('#nav a'))).toBe(true);

  await page.keyboard.press('Escape');
  expect(await btn.getAttribute('aria-expanded')).toBe('false');
  expect(await page.evaluate(() => document.activeElement.id)).toBe('menuBtn');
  await page.waitForTimeout(400);
  expect(await nav.evaluate(n => getComputedStyle(n).visibility)).toBe('hidden');

  // النقر على رابط في الدرج المفتوح ينتقل ويغلقه
  await btn.click();
  await page.locator('#nav a[href="#/health"]').click();
  await page.waitForFunction(() => location.hash === '#/health' && document.querySelector('#main .panel'));
  expect(await btn.getAttribute('aria-expanded')).toBe('false');
  expect(await nav.evaluate(n => n.classList.contains('open'))).toBe(false);
});
