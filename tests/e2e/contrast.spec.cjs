// فحص تباين الألوان في web/css/tokens.css على خادم الثوابت (الدفعة 3 من إصلاحات الإتاحة):
// في الوضعين وفي اللوحات الأربع: ink2 وink3 وaccent-text على كل الأسطح وفوق accent-bg المركّب، وaccent-ink على accent-solid وaccent-hover ≥ 4.5:1 (WCAG 1.4.3)؛
// والعناصر التي تكتب نصًّا بلون الهوية (الصفحة الحالية في القائمة، الزر الأساسي، رابط التخطّي) تستعمل الرموز الصحيحة.
const { test, expect } = require('@playwright/test');

const THEMES = ['dark', 'light'];
const PALETTES = ['teal', 'blue', 'violet', 'graphite'];
const MIN = 4.5;
const NO_MOTION = '*{transition:none!important;animation:none!important}';

// يقرأ قيمة رمز محلول عبر عنصر مجسّ (rgb أو color(srgb) مع ألفا)، ويركّب الشفاف فوق السطح، ويحسب التباين وفق WCAG 2.x
const probe = () => {
  const el = document.createElement('span');
  document.body.appendChild(el);
  const parse = s => {
    const m = s.match(/^color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+))?\)/);
    if (m) return [+m[1] * 255, +m[2] * 255, +m[3] * 255, m[4] === undefined ? 1 : +m[4]];
    const n = s.match(/[\d.]+/g);
    return n && n.length >= 3 ? [+n[0], +n[1], +n[2], n[3] === undefined ? 1 : +n[3]] : null;
  };
  const rgb = v => { el.style.color = ''; el.style.color = `var(${v})`; return parse(getComputedStyle(el).color); };
  const over = (fg, bg) => fg.slice(0, 3).map((c, i) => c * fg[3] + bg[i] * (1 - fg[3]));
  const lum = ([r, g, b]) => { const f = c => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const cr = (a, b) => { const x = lum(a), y = lum(b); return Math.round(((Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)) * 100) / 100; };
  const surfaces = ['--bg', '--panel', '--panel2', '--raise'];
  const abg = rgb('--accent-bg');
  const rows = [];
  for (const ink of ['--ink2', '--ink3', '--accent-text']) for (const s of surfaces) {
    const a = rgb(ink), b = rgb(s);
    rows.push([`${ink} على ${s}`, a, b]);
    if (ink === '--accent-text' && a && b && abg) rows.push([`${ink} على accent-bg فوق ${s}`, a, over(abg, b)]);
  }
  rows.push(['--accent-ink على --accent-solid', rgb('--accent-ink'), rgb('--accent-solid')]);
  rows.push(['--accent-ink على --accent-hover', rgb('--accent-ink'), rgb('--accent-hover')]);
  const out = rows.map(([n, a, b]) => ({ n, ok: !!(a && b), cr: a && b ? cr(a, b) : 0 }));
  el.remove();
  return out;
};

test('ink2 وink3 وaccent-text على كل الأسطح، وaccent-ink على سطح الزر الأساسي ≥ 4.5:1 في الوضعين واللوحات الأربع', async ({ page }) => {
  await page.goto('/#/overview');
  await page.waitForFunction(() => document.querySelector('#main .panel'));
  await page.addStyleTag({ content: NO_MOTION });
  const bad = [];
  for (const theme of THEMES) for (const palette of PALETTES) {
    await page.evaluate(([t, p]) => { document.documentElement.dataset.theme = t; document.documentElement.dataset.palette = p; }, [theme, palette]);
    const rows = await page.evaluate(probe);
    for (const r of rows) if (!r.ok || r.cr < MIN) bad.push(`${theme}/${palette}: ${r.n} = ${r.ok ? r.cr : 'غير محلول'}`);
  }
  expect(bad, bad.join('\n')).toEqual([]);
});

test('الصفحة الحالية في القائمة والزر الأساسي ورابط التخطّي يستعملون accent-text وaccent-solid', async ({ page }) => {
  await page.goto('/#/overview');
  await page.waitForFunction(() => document.querySelector('.nav a[aria-current="page"]'));
  await page.addStyleTag({ content: NO_MOTION });
  for (const theme of THEMES) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; document.documentElement.dataset.palette = 'teal'; }, theme);
    const r = await page.evaluate(() => {
      const el = document.createElement('button'); el.className = 'btn primary'; document.body.appendChild(el);
      const tok = v => { const s = document.createElement('i'); document.body.appendChild(s); s.style.color = `var(${v})`; const c = getComputedStyle(s).color; s.remove(); return c; };
      const out = {
        nav: getComputedStyle(document.querySelector('.nav a[aria-current="page"]')).color,
        accentText: tok('--accent-text'),
        btnBg: getComputedStyle(el).backgroundColor, btnFg: getComputedStyle(el).color,
        accentSolid: tok('--accent-solid'), accentInk: tok('--accent-ink'),
        skipBg: getComputedStyle(document.querySelector('.skip')).backgroundColor,
      };
      el.remove();
      return out;
    });
    expect(r.nav, `${theme}: لون الصفحة الحالية`).toBe(r.accentText);
    expect(r.btnBg, `${theme}: خلفية الزر الأساسي`).toBe(r.accentSolid);
    expect(r.btnFg, `${theme}: نصّ الزر الأساسي`).toBe(r.accentInk);
    expect(r.skipBg, `${theme}: خلفية رابط التخطّي`).toBe(r.accentSolid);
  }
});

// حارس: لون accent الخام 3.0:1 في teal الفاتح، فلا يُكتب به نصّ؛ النص يأخذ accent-text. يمسح كل قواعد الأنماط المحمّلة.
test('لا قاعدة تلوّن نصًّا بـ accent الخام؛ النص بلون الهوية يستعمل accent-text', async ({ page }) => {
  await page.goto('/#/overview');
  await page.waitForFunction(() => document.querySelector('#main .panel'));
  const raw = await page.evaluate(() => {
    const out = [];
    const walk = rules => { for (const r of rules) { if (r.cssRules) walk(r.cssRules); if (r.style && r.style.getPropertyValue('color').trim() === 'var(--accent)') out.push(r.selectorText); } };
    for (const s of document.styleSheets) walk(s.cssRules);
    return out;
  });
  expect(raw, raw.join('\n')).toEqual([]);
  const r = await page.evaluate(() => {
    document.documentElement.dataset.theme = 'light'; document.documentElement.dataset.palette = 'teal';
    const box = document.createElement('div'); box.className = 'ask-msg';
    box.innerHTML = '<button class="btn" aria-pressed="true">مفيد</button>';
    document.body.appendChild(box);
    const tok = document.createElement('i'); document.body.appendChild(tok); tok.style.color = 'var(--accent-text)';
    const res = { btn: getComputedStyle(box.firstChild).color, accentText: getComputedStyle(tok).color };
    box.remove(); tok.remove();
    return res;
  });
  expect(r.btn, 'زر التقييم المضغوط في صفحة اسأل').toBe(r.accentText);
});

// عيّنات صفحة المظهر تطابق لون accent الفعلي لكل لوحة في الوضعين (لا قيمة منفصلة عن اللوحة)
test('عيّنة كل لوحة في صفحة المظهر بلون accent الفعلي لتلك اللوحة في الوضعين', async ({ page }) => {
  await page.goto('/#/appearance');
  await page.waitForFunction(() => document.querySelectorAll('#main .swatch i').length === 4);
  const bad = [];
  for (const theme of THEMES) for (const palette of PALETTES) {
    const r = await page.evaluate(([t, p]) => {
      const root = document.documentElement; root.dataset.theme = t;
      root.dataset.palette = 'teal';
      const sw = getComputedStyle(document.querySelector(`#main .swatch[data-palette="${p}"] i`)).backgroundColor;
      root.dataset.palette = p;
      const probe = document.createElement('i'); probe.style.color = 'var(--accent)'; document.body.appendChild(probe);
      const accent = getComputedStyle(probe).color; probe.remove();
      return { sw, accent };
    }, [theme, palette]);
    if (r.sw !== r.accent) bad.push(`${theme}/${palette}: العيّنة ${r.sw} واللوحة ${r.accent}`);
  }
  expect(bad, bad.join('\n')).toEqual([]);
});
