import { expect, test } from '@playwright/test';
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

test('configures a native model, probes tools, chats and restores after refresh', async ({ page }) => {
  test.setTimeout(120000);
  test.skip(process.env.T04_E2E !== '1', 'Requires isolated local protocol server');
  await page.goto('./settings/models');
  await page.getByLabel('连接名称', { exact: true }).fill('本地协议连接');
  await page.getByLabel('API 基地址', { exact: true }).fill(process.env.T21_MODEL_URL ?? 'http://127.0.0.1:8099/qwen_dashscope');
  await page.getByLabel('API 密钥', { exact: true }).fill('local-test-key');
  await page.getByRole('button', { name: '保存连接', exact: true }).click();
  await expect(page.getByRole('cell', { name: '本地协议连接', exact: true })).toBeVisible();
  await page.getByLabel('所属连接', { exact: true }).click();
  await page.locator('.ant-select-item-option-content').filter({ hasText: '本地协议连接' }).click();
  await page.getByLabel('模型名称', { exact: true }).fill('local-fixture-model');
  await page.getByRole('button', { name: '保存模型', exact: true }).click();
  const row = page.getByRole('row').filter({ hasText: 'local-fixture-model' });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: '运行探测', exact: true }).click();
  await expect(page.getByText('文本与工具探测通过', { exact: true })).toBeVisible({ timeout: 20000 });
  await expect(row.getByRole('button', { name: '设为默认', exact: true })).toBeEnabled();
  await row.getByRole('button', { name: '设为默认', exact: true }).click();
  await expect(row.getByRole('button', { name: '默认模型', exact: true })).toBeVisible();
  await page.getByRole('link', { name: '对话', exact: true }).click();
  await page.getByRole('button', { name: '选择专家', exact: true }).click();
  await page.getByRole('button', { name: /自动协作.*由主专家/ }).click();
  await expect(page.getByText(/已开启多专家协作/)).toBeVisible();
  await page.getByRole('button', { name: '选择专家', exact: true }).click();
  await page.getByRole('button', { name: /通用助手.*日常问答/ }).click();
  await page.getByRole('button', { name: /写一份项目周报/ }).click();
  await expect(page.getByRole('textbox', { name: '消息', exact: true })).toHaveValue('生成一份 Word 项目周报');
  await page.getByRole('button', { name: '高级设置', exact: true }).click();
  await page.getByLabel('Agent 最大决策轮数', { exact: true }).fill('35');
  await page.getByLabel('压缩触发比例', { exact: true }).fill('0.75');
  await page.getByRole('checkbox', { name: '允许具名的串行依赖派发例外' }).check();
  await page.getByRole('dialog').getByRole('button', { name: /^完\s*成$/ }).click();
  const firstRequest = page.waitForRequest(r => r.url().endsWith('/api/v1/runs') && r.method() === 'POST');
  await page.getByRole('textbox', { name: '消息', exact: true }).fill('slow 第一轮');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  const submitted = (await firstRequest).postDataJSON();
  expect(submitted.agent_policy).toMatchObject({ max_iters: 35, allow_serial_dependency_exception: true });
  expect(submitted.context_policy).toMatchObject({ trigger_ratio: 0.75 });
  await expect(page).toHaveURL(/projects\//);
  await page.reload();
  await expect(page.getByText('本地协议测试回答：你好', { exact: true })).toBeVisible({ timeout: 20000 });
  await page.getByRole('textbox', { name: '消息', exact: true }).fill('继续第二轮');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText('本地协议测试回答：已保留上轮上下文', { exact: true })).toBeVisible({ timeout: 20000 });
  await page.reload();
  await expect(page.getByText('本地协议测试回答：已保留上轮上下文', { exact: true })).toBeVisible();
  await expect(page.getByText('本地协议测试回答：你好', { exact: true })).toHaveCount(1);
  await page.getByRole('link', { name: '专家与技能', exact: true }).click();
  await page.getByRole('button', { name: '管理专家', exact: true }).click();
  await page.getByRole('tab', { name: 'Skills', exact: true }).click();
  await page.getByLabel('导入 Skill', { exact: true }).setInputFiles('tests/fixtures/skill.zip');
  const skillCards = page.locator('.ant-card').filter({ has: page.locator('.ant-card-head-title').filter({ hasText: '项目文件整理' }) });
  await expect(skillCards).toHaveCount(2, { timeout: 15000 });
  await skillCards.last().getByRole('switch').click();
  await expect(skillCards.last().getByRole('switch')).toBeChecked();
  await page.getByRole('tab', { name: '专家', exact: true }).click();
  await page.getByLabel('能力名称', { exact: true }).fill('文件专家');
  await page.getByLabel('专家指令', { exact: true }).fill('使用工具生成项目文件');
  await page.getByLabel('专家 Skills', { exact: true }).click();
  await page.locator('.ant-select-item-option-content').filter({ hasText: '项目文件整理' }).last().click();
  await page.getByLabel('能力名称', { exact: true }).click();
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(page.locator('.ant-card-head-title').filter({ hasText: '文件专家' })).toBeVisible();
  await page.locator('.ant-card').filter({ has: page.locator('.ant-card-head-title').filter({ hasText: '文件专家' }) }).getByRole('button', { name: '使用专家', exact: true }).click();
  await expect(page).toHaveURL(/chat$/);
  await expect(page.getByRole('button', { name: '选择专家', exact: true })).toContainText('文件专家');
  await page.getByLabel('上传项目资料').setInputFiles({ name: 'input.txt', mimeType: 'text/plain', buffer: Buffer.from('original input') });
  await expect(page.locator('.attachment-list').getByText(/input.txt/)).toBeVisible();
  await page.getByRole('textbox', { name: '消息', exact: true }).fill('GENERATE_TEST_FILE');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByText(/成功版本 ·/)).toBeVisible({ timeout: 20000 });
  await expect(page.getByRole('link', { name: 'report.txt', exact: true })).toBeVisible();
  const preview = await page.request.get((await page.getByRole('link', { name: 'report.txt', exact: true }).getAttribute('href'))!);
  expect(preview.ok()).toBeTruthy();
  expect(preview.headers()['content-type']).toContain('text/plain');
  const links = page.getByRole('link', { name: '下载 ZIP', exact: true });
  await expect(links).toHaveCount(2);
  const response = await page.request.get((await links.last().getAttribute('href'))!);
  expect(response.ok()).toBeTruthy();
  expect(response.headers()['content-type']).toContain('application/zip');
  await page.reload();
  await expect(page.getByText(/成功版本 ·/)).toBeVisible();

  if (process.env.T21_UI_URL) {
    // Read the published file, revise through the actual composer, then verify
    // that the previous immutable version is still downloadable and unchanged.
    const originalUrl = (await page.getByRole('link', { name: 'report.txt', exact: true }).getAttribute('href'))!;
    expect(await (await page.request.get(originalUrl)).text()).toBe('verified project file');
    await page.getByRole('textbox', { name: '消息', exact: true }).fill('REVISE_TEST_FILE 修改 report.txt 为第二版');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await expect(page.getByText('T21 revision completed', { exact: true })).toBeVisible({ timeout: 25000 });
    await expect(page.getByRole('link', { name: 'report.txt', exact: true })).toHaveCount(2);
    const revisedUrl = (await page.getByRole('link', { name: 'report.txt', exact: true }).last().getAttribute('href'))!;
    expect(revisedUrl).not.toBe(originalUrl);
    expect(await (await page.request.get(revisedUrl)).text()).toBe('revised project file version two');
    expect(await (await page.request.get(originalUrl)).text()).toBe('verified project file');
    await page.reload();
    await expect(page.getByRole('link', { name: 'report.txt', exact: true })).toHaveCount(2);

    // Only fixture setup uses the API. Approval/rejection and execution all go
    // through the real UI, API, Worker, AgentScope and stdio MCP transport.
    const connectorResponse = await page.request.post('/api/v1/capabilities/connector', { data: { config: {
      name: 'T21 消息记录', enabled: true, transport: 'stdio',
      command: process.env.T21_PYTHON, args: [process.env.T21_MCP_SCRIPT],
      effects: { record_message: 'external_write' },
    } } });
    expect(connectorResponse.ok()).toBeTruthy();
    const connector = (await connectorResponse.json()).data;
    await page.getByRole('link', { name: /新建对话/ }).click();
    // API fixture setup bypasses the UI mutation's React Query invalidation.
    await page.reload();
    await expect(page.locator('.attachment-list')).toHaveCount(0);
    await page.getByRole('button', { name: '选择能力', exact: true }).click();
    await page.getByRole('tab', { name: /^连接器 ·/ }).click();
    await page.getByRole('checkbox', { name: /T21 消息记录/ }).check();
    await page.getByRole('button', { name: /^完\s*成$/ }).click();
    const ledger = join(process.env.DATA_DIR!, 'external-calls.jsonl');
    const calls = () => existsSync(ledger) ? readFileSync(ledger, 'utf8').trim().split('\n').filter(Boolean) : [];
    for (const approved of [false, true]) {
      await page.getByRole('textbox', { name: '消息', exact: true }).fill(`CONFIRM_TEST:${connector.id} 发送 T21 exact message`);
      await page.getByRole('button', { name: '发送', exact: true }).click();
      const confirmation = page.locator('.ant-card').filter({ hasText: '确认具体操作后执行' });
      await expect(confirmation).toContainText('T21 exact message', { timeout: 20000 });
      await expect(confirmation).toContainText('record_message');
      expect(calls()).toHaveLength(0);
      await page.reload();
      await expect(confirmation).toBeVisible();
      await confirmation.getByRole('button', { name: approved ? '确认执行' : '拒绝执行', exact: true }).click();
      await expect(confirmation).toHaveCount(0);
      await expect(page.getByText('T21 confirmation completed', { exact: true })).toHaveCount(approved ? 2 : 1, { timeout: 20000 });
      expect(calls()).toHaveLength(approved ? 1 : 0);
    }
    expect(JSON.parse(calls()[0])).toEqual({ text: 'T21 exact message' });
    await page.reload();
    await expect(page.getByText('T21 confirmation completed', { exact: true })).toHaveCount(2);
    await expect(page.getByRole('button', { name: '确认执行', exact: true })).toHaveCount(0);
    expect(calls()).toHaveLength(1);

    await page.getByRole('link', { name: /新建对话/ }).click();
    await page.getByRole('textbox', { name: '消息', exact: true }).fill('MISSING_TEST_FILE 生成 report.txt');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    const resume = page.getByRole('button', { name: '填入继续执行草稿', exact: true });
    await expect(resume).toBeVisible({ timeout: 20000 });
    const projectId = page.url().split('/').pop();
    const beforeRuns = await (await page.request.get(`/api/v1/projects/${projectId}/runs`)).json();
    await resume.click();
    await expect(page.getByRole('textbox', { name: '消息', exact: true })).toHaveValue(/请从最后成功版本继续完成以下任务[\s\S]*MISSING_TEST_FILE/);
    await page.reload();
    await expect(resume).toBeVisible();
    await expect(page.getByRole('textbox', { name: '消息', exact: true })).toHaveValue(/MISSING_TEST_FILE/);
    const afterRuns = await (await page.request.get(`/api/v1/projects/${projectId}/runs`)).json();
    expect(afterRuns.data.map((run: { id: string }) => run.id)).toEqual(beforeRuns.data.map((run: { id: string }) => run.id));
    await expect(page.getByRole('button', { name: '取消本轮任务', exact: true })).toHaveCount(0);
    // UI-only recovery error contract; real OS-kill/resume is covered in backend.
    await page.route('**/runs/*/checkpoint', route => route.fulfill({ json: { data: { available: true, safe: true, resumable: true } } }));
    await page.route('**/runs/*/resume', route => route.fulfill({ status: 409, json: { error: { code: 'CHECKPOINT_FILES_CHANGED', message: '检查点之后文件发生变化，不能自动恢复' } } }));
    await page.reload();
    await page.getByRole('button', { name: '从检查点恢复', exact: true }).click();
    await expect(page.getByText('检查点之后文件发生变化，不能自动恢复', { exact: true })).toBeVisible();

  }

});
