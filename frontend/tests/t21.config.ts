import { defineConfig } from '@playwright/test';

if (!process.env.T21_UI_URL || !process.env.DATA_DIR) throw new Error('Run scripts/verify_t21_ui.py for isolation');
export default defineConfig({
  testDir: './e2e', workers: 1, timeout: 60000,
  outputDir: process.env.T21_RESULTS,
  reporter: [['list'], ['json', { outputFile: `${process.env.T21_RESULTS}/results.json` }]],
  use: { baseURL: `${process.env.T21_UI_URL}/app/`, headless: true,
    actionTimeout: 15000,
    channel: process.env.PLAYWRIGHT_CHANNEL ?? 'chromium', screenshot: 'only-on-failure', trace: 'retain-on-failure' },
});
