// زر تفريغ ذاكرة كروت الشاشة في صفحة الرسوميات (خادم الثوابت): عام لكل الكروت، ومفتاح المالك لإعادة تشغيل dwm،
// والتأكيد الثاني لا يظهر إلا حين يحجز dwm ذاكرة كبيرة والمفتاح مفعّل؛ لا تشغيل فعلي هنا (POST مستبدل).
const { test, expect } = require('@playwright/test');

const ready = page => page.waitForFunction(() => document.querySelector('#main .panel'));
const last = { running: false, ts: Math.floor(Date.now() / 1000) - 60, beforeMB: 900, afterMB: 300, freedMB: 600, sharedBeforeMB: 13277, sharedAfterMB: 13200,
  sharedFreedMB: 77, dwmMB: 11682, dwm: false, dwmOn: true, steps: [], text: 'لا نموذج محمّل في LM Studio.', err: null, todayFreedMB: 11604, todayCount: 3 };

test('صفحة الرسوميات: زر عام لكل الكروت ومفتاح dwm مسمّى، والتأكيد الثاني بعد المرور الآمن فقط', async ({ page }) => {
  const posts = [];
  await page.route('**/api/gpu-purge**', async route => {
    if (route.request().method() === 'POST') { posts.push(JSON.parse(route.request().postData() || '{}')); return route.fulfill({ json: { ok: true } }); }
    return route.fulfill({ json: last });
  });
  await page.goto('/#/gpu'); await ready(page);
  const btn = page.locator('[data-gpupurge]');
  await expect(btn).toHaveText('تفريغ ذاكرة كروت الشاشة');
  await expect(page.locator('[data-gpudwm]')).toHaveAttribute('aria-label', 'السماح بإعادة تشغيل مدير سطح المكتب');
  await expect(page.locator('[data-gpudwm]')).toHaveAttribute('aria-checked', 'true');
  await expect(page.locator('.note', { hasText: 'لكل الكروت' })).toContainText('المشتركة: 13,277 ← 13,200 MB');
  const freed = page.locator('.gpu-freed .kpi');   // مؤشر المحرَّر: آخر تفريغ (600 + 77 MB) ومجموع اليوم من السجل
  await expect(freed.nth(0)).toContainText('حُرّر في آخر تفريغ');
  await expect(freed.nth(0).locator('.v')).toHaveText('677MB');
  await expect(freed.nth(0)).toContainText('NVIDIA 600 MB · المشتركة 77 MB');
  await expect(freed.nth(1).locator('.v')).toHaveText('11.3GB');
  await expect(freed.nth(1)).toContainText('مرّات التفريغ: 3');
  await btn.click();
  await expect(page.locator('dialog.dlg')).toContainText('LM Studio وOllama');
  await page.locator('dialog.dlg button[value="yes"]').click();
  const second = page.locator('dialog.dlg', { hasText: 'مدير سطح المكتب (dwm) يحجز 11.4 GB' });
  await expect(second).toBeVisible({ timeout: 5000 });
  await expect(second.locator('button[value="no"]')).toBeFocused();   // الخطوة الأخطر تبدأ والتركيز على «إلغاء»
  await second.locator('button[value="no"]').click();
  expect(posts).toEqual([{ dwm: false }]);
});

test('صفحة الرسوميات: لوحة الذكاء المحلي تنبّه عند اجتماع نموذجين وتعرض الاستخدام', async ({ page }) => {
  await page.goto('/#/gpu'); await ready(page);
  const ai = page.locator('.panel').filter({ has: page.getByRole('heading', { name: 'الذكاء المحلي', exact: true }) });
  await expect(ai.locator('.tag.bad').first()).toContainText('نموذجان معًا');
  await expect(ai.locator('tbody tr')).toHaveCount(2);
  await expect(ai).toContainText('qwen3-vl-4b-instruct');
  await expect(ai).toContainText('خلال 14 يومًا: 3 مرة');
});
