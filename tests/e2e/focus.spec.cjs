// فحص إدارة التركيز في web/index.html على خادم الثوابت (الدفعة الأولى من إصلاحات الإتاحة):
// التركيز يبقى على الزر بعد إعادة الرسم وبعد تحديث ببيانات مختلفة، النموذج المعدَّل لا يُستبدل بالتحديث حتى يُحفظ،
// حوار الفعل الهدّام يبدأ على «إلغاء» والعادي على التأكيد ولكل حوار اسم، والانتقال بين الصفحات ينقل التركيز إلى المحتوى.
const { test, expect } = require('@playwright/test');

const ready = page => page.waitForFunction(() => document.querySelector('#main .panel'));
const active = page => page.evaluate(() => { const a = document.activeElement; return { id: a?.id || '', tag: a?.tagName || '', value: a?.value ?? null, data: { ...(a?.dataset || {}) } }; });

test('التركيز على زر داخل المحتوى يبقى بعد redraw وبعد تحديث ببيانات مختلفة', async ({ page }) => {
  await page.goto('/#/ctrl'); await ready(page);
  await page.locator('#main button[data-mode="light"]').focus();
  await page.evaluate(() => redraw());
  expect((await active(page)).data.mode).toBe('light');

  // تحديث فعلي: maintenance.on يُقلب فيتغيّر التوقيع وتُستبدل الصفحة كلها
  const before = await page.locator('#main [data-maint]').getAttribute('aria-checked');
  await page.route('**/api/maintenance', async route => { const res = await route.fetch(); const body = await res.json(); body.on = !body.on; await route.fulfill({ response: res, json: body }); });
  await page.evaluate(() => show());
  expect(await page.locator('#main [data-maint]').getAttribute('aria-checked')).not.toBe(before);   // الصفحة أُعيد رسمها فعلًا
  expect((await active(page)).data.mode).toBe('light');
});

test('قيمة معدَّلة في نموذج الحدود لا تضيع عند التحديث، وتُستبدل بعد الحفظ', async ({ page }) => {
  await page.goto('/#/ctrl'); await ready(page);
  const inp = page.locator('#alForm input[name="mem.ramWarn"]');
  await expect(inp).toHaveValue('80');
  await inp.fill('77');
  await page.locator('#alReset').focus();   // التركيز خارج الحقل: الحارس القديم كان يستبدل النموذج هنا
  await page.route('**/api/alerts', async route => {
    if (route.request().method() !== 'GET') return route.fulfill({ json: { ok: true } });
    const res = await route.fetch(); const body = await res.json(); body.cfg.mem.ramBad = 93; await route.fulfill({ response: res, json: body });
  });
  await page.evaluate(() => show());
  await expect(inp).toHaveValue('77');
  await expect(page.locator('#alForm input[name="mem.ramBad"]')).toHaveValue('92');   // لم يُستبدل النموذج

  await page.locator('#alForm button[type=submit]').click();
  await expect(page.locator('#alForm input[name="mem.ramBad"]')).toHaveValue('93');   // بعد الحفظ صار نظيفًا فاستُبدل بقيم الخادم
});

test('حوار الفعل الهدّام يبدأ على «إلغاء» والعادي على التأكيد، ولكل حوار اسم من نصه', async ({ page }) => {
  await page.goto('/#/overview'); await ready(page);
  const open = (text, label, danger) => page.evaluate(([t, l, d]) => {
    window.__dlg = confirmDlg(t, l, d);
    const a = document.activeElement, dlg = document.querySelector('dialog[open]'), lbl = dlg.getAttribute('aria-labelledby');
    return { value: a.value, label: a.textContent, name: document.getElementById(lbl)?.textContent || '' };
  }, [text, label, danger]);
  expect(await open('حذف نهائي؟', 'احذف', true)).toEqual({ value: 'no', label: 'إلغاء', name: 'حذف نهائي؟' });
  await page.keyboard.press('Enter');   // Enter على الزر المركَّز يختار «إلغاء» لا الحذف
  expect(await page.evaluate(() => window.__dlg)).toBe(false);
  expect(await page.locator('dialog').count()).toBe(0);

  expect(await open('تشغيل المهمة؟', 'تأكيد', false)).toEqual({ value: 'yes', label: 'تأكيد', name: 'تشغيل المهمة؟' });
  await page.keyboard.press('Escape');
  expect(await page.evaluate(() => window.__dlg)).toBe(false);
});

test('الانتقال بين الصفحات ينقل التركيز إلى المحتوى؛ أول تحميل لا يسرقه', async ({ page }) => {
  await page.goto('/#/overview'); await ready(page);
  expect((await active(page)).tag).toBe('BODY');
  await page.locator('#nav a[href="#/health"]').click();
  await page.waitForFunction(() => location.hash === '#/health' && document.querySelector('#main .panel'));
  await expect.poll(async () => (await active(page)).id).toBe('main');   // لوحات الصفحة السابقة تحقق شرط الانتظار قبل رسم الجديدة؛ التركيز ينتقل بعد الرسم
});
