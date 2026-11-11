import { defineConfig } from '@playwright/test';
const base = process.env.APP_BASE_PATH ?? '/app';
export default defineConfig({
  testDir: './tests/e2e',
  workers: 1,
  use: { baseURL: `http://127.0.0.1:5173${base}/`, headless: true, channel: process.env.PLAYWRIGHT_CHANNEL ?? 'chromium' },
  webServer: [
    { command: 'uv run --project ../backend uvicorn agentbuddy.main:create_app --factory --host 127.0.0.1 --port 8000', url: `http://127.0.0.1:8000${process.env.API_PREFIX ?? '/api/v1'}/health`, reuseExistingServer: false },
    { command: 'pnpm dev', url: `http://127.0.0.1:5173${base}/`, reuseExistingServer: false },
  ],
});
