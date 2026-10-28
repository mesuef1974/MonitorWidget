// الأداء والتحريك (البند (د) من مراجعة الواجهة) على خادم الثوابت:
// الرسم اللوحةَ تلو اللوحة (ما لم يتغيّر يبقى بعناصره والرسم البياني لا يُعاد بناؤه)، هالة العدّاد بلا filter،
// نبضة «مباشر» بـ transform/opacity على عنصر شبه، والمقياس بـ scaleX بدل width.
const { test, expect } = require('@playwright/test');

const ready = page => page.waitForFunction(() => document.querySelector('#main .panel'));

test('التحديث يستبدل اللوحة المتغيّرة وحدها: الرسم البياني وجدول «الآن» يبقيان بعناصرهما', async ({ page }) => {
  await page.goto('/#/cpu'); await ready(page);
  await page.waitForFunction(() => document.querySelector('#cpuChart svg'));
  await page.evaluate(() => {
    document.querySelector('#cpuChart svg').__keep = 1;
    [...document.querySelectorAll('#main .panel')].forEach(p => { p.__keep = 1; });
  });
  const total0 = await page.locator('#main .panel').first().locator('.kpi .num').first().textContent();

  await page.route('**/api/cpu', async route => {   // يتغيّر رقم واحد في لوحة الحمل؛ السلسلة والجداول كما هي
    const d = await (await route.fetch()).json(); d.total = d.total + 1.5; await route.fulfill({ json: d });
  });
  await page.evaluate(() => show());
  await page.waitForFunction(t => document.querySelector('#main .panel .kpi .num').textContent !== t, total0);

  const r = await page.evaluate(() => {
    const ps = [...document.querySelectorAll('#main .panel')];
    return { n: ps.length, kept: ps.map(p => !!p.__keep), chartKept: !!document.querySelector('#cpuChart svg')?.__keep, hasChart: !!document.querySelector('#cpuChart svg') };
  });
  expect(r.n).toBe(5);
  expect(r.kept).toEqual([false, true, true, true, true]);   // لوحة الحمل وحدها استُبدلت
  expect(r.chartKept).toBe(true);
  expect(r.hasChart).toBe(true);
});

test('العدّاد بلا filter: هالة حلقة أعرض شبه شفافة', async ({ page }) => {
  const src = await (await page.request.get('/')).text();
  expect(src).not.toContain('drop-shadow');
  for (const f of ['components', 'layout', 'base', 'utilities']) expect(await (await page.request.get(`/css/${f}.css`)).text()).not.toContain('drop-shadow');
  await page.goto('/#/overview'); await ready(page);
  await page.waitForFunction(() => document.querySelector('.gauge .arc-glow'));
  const g = await page.evaluate(() => {
    const glow = document.querySelector('.gauge .arc-glow'), arc = glow.parentElement.querySelector('.arc'), cs = getComputedStyle(glow);
    return { filter: getComputedStyle(arc).filter, w: parseFloat(cs.strokeWidth), op: parseFloat(cs.strokeOpacity), sameOff: glow.getAttribute('stroke-dashoffset') === arc.getAttribute('stroke-dashoffset'), sameStroke: glow.getAttribute('stroke') === arc.getAttribute('stroke') };
  });
  expect(g.filter).toBe('none');
  expect(g.w).toBeGreaterThan(12); expect(g.op).toBeLessThan(0.5); expect(g.op).toBeGreaterThan(0);
  expect(g.sameOff).toBe(true); expect(g.sameStroke).toBe(true);
});

test('نبضة «مباشر» على عنصر شبه بـ transform/opacity لا box-shadow', async ({ page }) => {
  await page.goto('/#/cpu'); await ready(page);
  await page.waitForFunction(() => !document.querySelector('#live').classList.contains('err'));
  const r = await page.evaluate(() => {
    const i = document.querySelector('#live i');
    let kf = null; for (const ss of document.styleSheets) for (const rule of ss.cssRules) if (rule instanceof CSSKeyframesRule && rule.name === 'pulse') kf = rule.cssText;
    return { self: getComputedStyle(i).animationName, after: getComputedStyle(i, '::after').animationName, kf, shadow: getComputedStyle(i).boxShadow };
  });
  expect(r.self).toBe('none');
  expect(r.after).toBe('pulse');
  expect(r.kf).toContain('transform'); expect(r.kf).toContain('opacity'); expect(r.kf).not.toContain('box-shadow');
  expect(r.shadow).toBe('none');
});

test('المقياس يملأ عرضه ويُقاس بـ scaleX من --p؛ لا انتقال على width', async ({ page }) => {
  for (const f of ['components']) expect(await (await page.request.get(`/css/${f}.css`)).text()).not.toMatch(/transition:[^;}]*\bwidth\b/);
  await page.goto('/#/cpu'); await ready(page);
  const m = await page.evaluate(() => [...document.querySelectorAll('#main .meter>i')].slice(0, 6).map(i => {
    const cs = getComputedStyle(i), p = parseFloat(i.style.getPropertyValue('--p'));
    return { p, tr: cs.transform, wEq: cs.width === getComputedStyle(i.parentElement).width, origin: cs.transformOrigin, pw: parseFloat(getComputedStyle(i.parentElement).width) };
  }));
  expect(m.length).toBeGreaterThan(2);
  for (const x of m) {
    expect(x.p).toBeGreaterThanOrEqual(0); expect(x.p).toBeLessThanOrEqual(1);
    expect(x.wEq).toBe(true);
    const sx = parseFloat(x.tr.replace('matrix(', ''));                                   // matrix(sx, 0, 0, 1, 0, 0)
    expect(Math.abs(sx - x.p)).toBeLessThan(0.002);
    expect(Math.abs(parseFloat(x.origin) - x.pw)).toBeLessThan(1);                           // الأصل في طرف البداية (اليمين في RTL)
  }
});
