// الشريط اللاصق في كل العروض: لا ينضغط العنوان إلى عرض صفري ولا ينكسر، ولا تلتف نصوص الأدوات داخلها؛
// عند الضيق تلتف الأدوات صفًا ثانيًا تحت العنوان فلا يتجاوز الشريط صفّين (كان 239px بين 861 و1280).
const { test, expect } = require('@playwright/test');

test('الشريط لا يتجاوز صفّين في كل العروض والعنوان والأدوات بعرضها الطبيعي', async ({ page }) => {
  for (const width of [1366, 1280, 1100, 1024, 900, 860, 600, 390]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/#/overview');
    await page.waitForFunction(() => document.querySelector('#main .panel'));
    const m = await page.evaluate(() => {
      const bar = document.querySelector('.bar');
      const kids = [...bar.children].filter(k => k.offsetWidth > 0 && !k.classList.contains('grow'));
      return {
        bar: bar.getBoundingClientRect().height,
        titleW: document.querySelector('.bar .grow').getBoundingClientRect().width,
        titleH: document.querySelector('.bar .grow').getBoundingClientRect().height,
        tallest: Math.max(...kids.map(k => k.getBoundingClientRect().height)),
      };
    });
    if (width >= 600) expect(m.bar, `${width}px: ارتفاع الشريط`).toBeLessThan(130);   // الهاتف: الأدوات قد تلتف صفين تحت العنوان
    expect(m.titleW, `${width}px: عرض العنوان`).toBeGreaterThan(120);   // لا ينضغط إلى صفر (كان 0 بين 861 و1280)
    expect(m.titleH, `${width}px: ارتفاع العنوان`).toBeLessThan(100);   // عنوان ووصف (قد يلتف الوصف سطرين في الهاتف كما في main)، لا حرفًا في كل سطر (كان 206)
    expect(m.tallest, `${width}px: أطول أداة`).toBeLessThan(42);
  }
});
