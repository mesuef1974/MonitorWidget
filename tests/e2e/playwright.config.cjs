// إعداد Playwright للفحص المرئي؛ يشغّل خادم الثوابت tests/web_fixture_server.py ولا يحتاج المرصد الحيّ.
//   cd tests/e2e && npm ci && npx playwright install chromium && npx playwright test
const { defineConfig } = require('@playwright/test');
const path = require('path');
const PORT = 47910;

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: /.*\.spec\.cjs/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  outputDir: path.join(__dirname, 'test-results'),
  use: {
    baseURL: `http://127.0.0.1:${PORT}/`,
    viewport: { width: 1366, height: 800 },
    locale: 'ar-QA',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `python ${path.join(__dirname, '..', 'web_fixture_server.py')} ${PORT}`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 30000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
