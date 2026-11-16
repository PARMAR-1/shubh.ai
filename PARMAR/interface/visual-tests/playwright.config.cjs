const path = require('node:path');
const { defineConfig } = require('@playwright/test');

const outputDirectory = path.join(__dirname, 'artifacts');
const port = Number(process.env.PARMAR_VISUAL_TEST_PORT || 8080);
const baseURL = `http://127.0.0.1:${port}`;

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: '**/*.spec.cjs',
  globalTeardown: require.resolve('./render-visual-report.cjs'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 45_000,
  reporter: [
    ['list'],
    ['html', { outputFolder: path.join(outputDirectory, 'html-report'), open: 'never' }],
    ['json', { outputFile: path.join(outputDirectory, 'playwright-results.json') }],
  ],
  outputDir: path.join(outputDirectory, 'test-results'),
  use: {
    baseURL,
    browserName: 'chromium',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'python PARMAR/interface/visual-tests/serve-ui.py',
    cwd: path.resolve(__dirname, '../../..'),
    url: `${baseURL}/`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      PARMAR_ENVIRONMENT: 'test',
      PARMAR_UI_HOST: '127.0.0.1',
      PARMAR_DEVELOPMENT_AUTH_ENABLED: 'false',
      PARMAR_VISUAL_TEST_PORT: String(port),
    },
  },
});
