import { expect, test } from '@playwright/test';
test('connects to real API and survives nested route refresh', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('./');
  await expect(page.getByRole('heading', { name: '今天，想做些什么？' })).toBeVisible();
  await page.getByRole('link', { name: '服务状态' }).click();
  await page.reload();
  await expect(page.getByRole('heading', { name: '服务状态' })).toBeVisible();
  await expect(page.getByText('后端已连接', { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
test('shows connection error and allows retry', async ({ page }) => {
  await page.route('**/health', route => route.fulfill({ status: 503, body: '' }));
  await page.goto('./status');
  await expect(page.getByText('后端暂不可用', { exact: true })).toBeVisible();
  await page.unroute('**/health');
  await page.getByRole('button', { name: '重新检查' }).click();
  await expect(page.getByText('后端已连接', { exact: true })).toBeVisible();
});

test('executes durable diagnostic through independent Worker', async ({ page }) => {
  test.skip(process.env.T02_E2E !== '1', 'Requires isolated migrated database and Worker');
  await page.goto('./status');
  await expect(page.getByText('SQLite 已就绪', { exact: true })).toBeVisible();
  await expect(page.getByText('Worker：运行中', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '运行存储检查' }).click();
  await expect(page.getByText('检查通过', { exact: true })).toBeVisible();
  await expect(page.getByText('临时文件读写正常', { exact: false })).toBeVisible();
});
