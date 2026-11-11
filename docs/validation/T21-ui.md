# T21 最新 UI E2E

状态：本次 UI 负责范围完成；后端偶发 Windows 文件发布异常保留待查。
日期：2026-11-11。保留 T20 A 版，不修改后端、数据库 schema、README 或 Git。

## 最终结果

- 完整执行现有 `frontend/tests/e2e`，并扩展其中业务链路：**4 passed，0 skipped，0 flaky，47.6秒**。
- `pnpm --dir frontend build`：TypeScript 与 Vite 生产构建通过；共享 chunk 超过500 kB的原有提示仍在。
- `pnpm --dir frontend test`：2文件、7项通过。
- 三个新增 Python 验证脚本 compileall 通过。
- 机器可读结果：`data/validation/t21-ui-20261111-160334/results/results.json`。
- 真实 MCP 外部调用记录：`data/validation/t21-ui-20261111-160334/external-calls.jsonl`，恰好一条 `{"text":"T21 exact message"}`。

## 环境与复现

在仓库根目录 PowerShell 执行：

```powershell
$env:PLAYWRIGHT_CHANNEL='chrome'
backend/.venv/Scripts/python.exe scripts/verify_t21_ui.py
```

脚本为 API、Vite、模型协议夹具分别选择空闲端口，启动独立 Worker；不复用/停止已有8000、5173服务。最终运行 API端口53243、前端53244、协议夹具53246（以实际服务日志为准）。业务数据位于系统临时目录，每轮新建，确切路径写入证据目录 `data-directory.txt`；最终为 `<system-temp>/agentbuddy-t21-ui-example`。日志和测试结果保留在仓库 `data/validation/t21-ui-时间戳/`，脚本退出后只停止自己创建的进程，不删除调试数据。

真实链路：Chrome → React/Vite → FastAPI → SQLite队列 → 独立Worker → AgentScope → 本地模型协议夹具/stdio MCP → 文件版本。使用合成测试密钥，无任何真实供应商请求。Qwen风格路径仅用于本地协议夹具，不代表Qwen实服务验收，也未改变Qwen/Claude暂缓决定。

## 本轮实际断言

1. 模型连接创建、文本与工具探测、设为默认；自动协作/通用助手切换，示例只回填。
2. 首轮执行中刷新后恢复，第二轮保留上下文，再刷新不重复消息。
3. Skill ZIP 导入启用、专家绑定Skill并使用、附件上传、生成项目文件；文件预览响应和ZIP下载有效。
4. 再次修改 report.txt，新URL返回第二版内容，旧URL仍返回原内容；刷新后两个版本仍存在。
5. 上传并创建项目后回到新对话，不再残留已提交附件，能发送后续任务。
6. 确认卡展示具体工具 `record_message` 和参数 `T21 exact message`；等待确认时刷新仍恢复确认卡。
7. 确认前外部账本为空；拒绝后仍为空。第二次批准后恰好执行一次，参数与展示内容一致；结束刷新不出现重复确认，也不新增执行记录。
8. 故意声明缺失文件触发真实任务失败；“填入继续执行草稿”可见，点击后包含原失败任务和核对提示。刷新后草稿仍在，前后run ID列表相同，不自动提交，无运行中取消按钮。该场景 worker.log 中的 delivery_incomplete 是预期失败证据。
9. 服务状态页嵌套路由刷新、健康检查503后重试恢复、独立Worker存储诊断。

## 发现的问题与修复

### 真实前端缺陷：新对话复用旧附件

160208 扩展测试通过生成和再次修改后，回到新建对话发送任务，API返回409“附件不属于当前草稿或项目”。截图/trace 显示新页面仍有“附件1”。原因：Chat.tsx 发送成功回调只清理项目 choices，未清理 `agentbuddy-draft-new-choices`，恢复了已归属旧项目的上传ID。

修改前已在本文记录必要性。仅在发送成功且原页面为新对话时保存清空 upload_ids、生成新 draft_id 的新对话配置，保留能力/预算选择；原项目配置不变。改动为 `frontend/src/Chat.tsx` onSuccess 中一行。协调方新增的失败 Alert 与继续草稿按钮未修改。最终完整E2E验证修复生效。

### 测试准备与选择器

- API直接建立连接器绕过React Query失效通知，测试在准备后刷新页面。
- Ant Design“双中文字按钮自动加空格”使“完成”匹配失败，选择器兼容“完 成”。
- 首次自带Chromium报spawn UNKNOWN；使用本机Chrome成功。

### 保留后端异常，不宣称已修复

`data/validation/t21-ui-20261111-160042/worker.log` 记录普通生成report.txt时，`runtime/files.py` 的 `os.replace(temporary, destination)` 抛出 Windows PermissionError WinError 5，导致没有成功版本。此前两轮相同生成路径成功。该轮trace和错误截图保留在其results目录。

后续将业务数据放到系统临时目录，最终测试通过，但这不能证明根因是目录位置、同步软件或杀毒，也不能证明异常已修复。本对话没有修改发布逻辑，交由后端协调方判断是否需复现与处理。

## 变更文件与边界

- `frontend/tests/e2e/chat.spec.ts`：本地模型地址参数化；扩展修改、确认、失败草稿断言。
- `frontend/tests/t21.config.ts`：隔离运行配置、报告与失败trace。
- `scripts/verify_t21_ui.py`：独立服务生命周期、临时业务数据、空闲端口与证据目录。
- `scripts/t21_provider_fixture.py`：确定性本地原生协议响应，支持改文件和调用确认工具。
- `scripts/t21_mcp_fixture.py`：真实stdio MCP传输，将执行次数落到隔离账本。
- `frontend/src/Chat.tsx`：上述最小附件草稿修复。
- 本验收文档。

“完整执行”指现有E2E目录全部测试及上述新增断言，不代表穷尽所有产品场景。实际多专家并行产物、办公视觉审阅、生产环境发布、真实供应商联通不由此测试证明，仍由各专项验收覆盖。本轮没有新建截图设计评审，T20 A视觉样式没有改动。

