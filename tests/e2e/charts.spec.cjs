// فحص إتاحة الرسوم البيانية في web/index.html على خادم الثوابت (البند (أ) من مراجعة الواجهة):
// كل رسم يحمل ملخصًا نصيًا (role=img + aria-label) باسم اللوحة وآخر قيمة وأدناها وأعلاها، وسطح الرسم قابل للتركيز
// وتقرأه لوحة المفاتيح (الأسهم، Home/End، Esc) مع إعلان القيم في منطقة حية؛ والتحديث الدوري لا يستبدل رسمًا مركَّزًا.
const { test, expect } = require('@playwright/test');

const ready = page => page.waitForFunction(() => document.querySelector('#main .plot svg[role=img]'));

test('كل رسم له ملخص نصي باسم اللوحة وقيمه، وسطحه قابل للتركيز وموصوف بالملخص', async ({ page }) => {
  await page.goto('/#/mem'); await ready(page);
  const charts = await page.evaluate(() => [...document.querySelectorAll('#main .plot')].map(p => ({
    tab: p.getAttribute('tabindex'), role: p.getAttribute('role'), name: p.getAttribute('aria-label') || '',
    desc: document.getElementById(p.getAttribute('aria-describedby') || '')?.getAttribute('aria-label') || '',
    title: p.closest('.panel')?.querySelector('h2')?.textContent || '',
  })));
  expect(charts.length).toBeGreaterThan(0);
  for (const c of charts) {
    expect(c.tab).toBe('0');
    expect(c.role).toBe('group');
    expect(c.name).toContain(c.title);
    expect(c.desc).toMatch(/^رسم بياني «.+» من \d\d:\d\d إلى \d\d:\d\d، [\d,]+ نقطة\. .+: آخر قيمة .+، أدنى .+، أعلى .+\.$/);
    expect(c.desc).toContain(c.title);
  }
  // مثال محدد: رسم «الفعلية والالتزام» يذكر السلسلتين وقيمًا مئوية
  const mem = charts.find(c => c.title === 'الفعلية والالتزام');
  expect(mem.desc).toMatch(/فعلية: آخر قيمة [\d.]+%/);
  expect(mem.desc).toMatch(/التزام: آخر قيمة [\d.]+%/);
});

test('الأسهم وHome/End تحرّك المؤشر وتعلن القيم، وEsc يخفيه، والإعلان لا يحدث بالمؤشر', async ({ page }) => {
  await page.goto('/#/mem'); await ready(page);
  const plot = page.locator('#memChart .plot');
  const state = () => plot.evaluate(p => ({
    tip: getComputedStyle(p.querySelector('.tip')).display, cur: p.querySelector('.cur').style.left,
    time: p.querySelector('.tip b')?.textContent || '', live: p.querySelector('.sr').textContent,
  }));
  await plot.focus();
  let s = await state();
  expect(s.tip).toBe('block');            // التركيز يُظهر المؤشر على آخر نقطة دون إعلان
  expect(s.cur).toBe('100%');
  expect(s.live).toBe('');
  const last = s.time;

  await page.keyboard.press('ArrowLeft');
  s = await state();
  expect(s.live).toMatch(/^\d\d:\d\d: فعلية [\d.]+%، التزام [\d.]+%$/);
  expect(s.live.startsWith(s.time)).toBe(true);
  expect(parseFloat(s.cur)).toBeLessThan(100);

  await page.keyboard.press('Home');
  s = await state();
  expect(s.cur).toBe('0%');
  const first = s.time;
  expect(first).not.toBe(last);
  expect(await plot.evaluate(p => p.querySelector('.tip').style.left)).not.toBe('auto');   // في النصف الأيسر يُوضع التلميح يمين المؤشر

  await page.keyboard.press('End');
  s = await state();
  expect(s.cur).toBe('100%');
  expect(s.time).toBe(last);
  expect(await plot.evaluate(p => p.querySelector('.tip').style.left)).toBe('auto');       // في النصف الأيمن يُوضع يساره

  await page.keyboard.press('Shift+ArrowLeft');
  s = await state();
  expect(parseFloat(s.cur)).toBeLessThan(95);   // عشر الطول تقريبًا

  await page.keyboard.press('Escape');
  s = await state();
  expect(s.tip).toBe('none');

  // المؤشر يُظهر التلميح دون أن يعلن
  await plot.evaluate(p => p.querySelector('.sr').textContent = '');
  const box = await plot.boundingBox();
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height / 2);
  s = await state();
  expect(s.tip).toBe('block');
  expect(s.live).toBe('');

  await page.keyboard.press('Tab');   // مغادرة الرسم تُخفي المؤشر وتمسح الإعلان
  await page.mouse.move(0, 0);
  s = await state();
  expect(s.tip).toBe('none');
  expect(s.live).toBe('');
});

test('التحديث الدوري لا يستبدل رسمًا مركَّزًا بلوحة المفاتيح', async ({ page }) => {
  await page.goto('/#/mem'); await ready(page);
  const plot = page.locator('#memChart .plot');
  await plot.focus();
  await page.keyboard.press('ArrowLeft');
  const before = await plot.evaluate(p => p.querySelector('svg').id);
  await page.evaluate(() => show());
  await page.waitForTimeout(300);
  expect(await page.locator('#memChart .plot svg').getAttribute('id')).toBe(before);   // لم يُعَد الرسم
  expect(await page.evaluate(() => document.activeElement.classList.contains('plot'))).toBe(true);
});
