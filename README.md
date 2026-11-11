# AgentBuddy

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**把想法交给 AI，把项目文件带回来。**

AgentBuddy 是一个面向个人和小团队的 Web 多智能体工作台。你可以直接描述任务，也可以选择合适的专家，上传资料，让它调用技能和工具完成工作。从整理研究资料、编写代码，到生成报告、表格和演示文稿，结果都能作为项目文件保存、下载，并通过后续对话继续修改。

**部署在你自己的电脑或服务器，使用你自己的模型。** 无需先购买服务器或域名，本地启动即可通过浏览器使用。每个部署实例自行配置模型地址、API Key 和可用模型；模型请求由该实例的后端发出，费用归对应模型账号，项目不提供共享 API Key 或公共模型额度。

项目采用 [MIT 许可证](LICENSE)，欢迎自行部署、修改和扩展。

我们希望让“配置 AI 能力”和“使用 AI 做事”自然地连在一起：常用功能放在对话入口，需要时再展开配置，让注意力留在任务本身。

[实测表现](#实测表现) · [快速开始](#快速开始) · [使用指南](#使用指南) · [部署与维护](#部署与维护) · [开发与验证](#开发与验证)

## 能帮你做什么

- **给专家配上真正能执行的技能。** 创建自己的专家，绑定 Skills 和 MCP 工具。Skill 可以包含操作说明、参考资料、模板和脚本，让专家既知道怎么做，也具备完成任务的手段。
- **一个专家处理，或让多个专家协作。** 日常任务由通用助手或指定专家完成；选择“自动协作”后，由主专家拆分任务，安排多个专家并行处理，再汇总交付。
- **能力随对话调整。** 开发专家也能临时使用 Excel 技能。当前对话的调整不会改写专家模板，适合在同一任务中组合不同能力。
- **交付文件，也保留修改过程。** 上传文件或 ZIP，生成代码、Markdown、Word、Excel、PowerPoint 等项目文件。继续对话即可提出修改，新版本单独保存，旧版本仍可下载。
- **执行过程留在工作台里。** 任务在后台运行，刷新页面后可以继续查看状态和结果。发送消息、发布内容等外部写操作会先展示具体内容，由用户确认后执行。
- **从现成入口开始。** 专家、技能和连接器以目录展示，提供用途说明和示例；模型选择放在输入栏中，不必每次做任务都重新配置一遍。

### 从这些任务试起

| 你想完成的事 | 可以这样开始 |
| --- | --- |
| 整理会议资料 | “把会议记录整理成纪要和待办表，标出负责人和截止日期。” |
| 分析数据 | “读取销售表，按地区汇总金额，生成结果表格并说明主要变化。” |
| 准备演示 | “根据这些资料，做一份项目介绍 PPT，把待确认的信息单独标出来。” |
| 多专家协作 | 选择自动协作：“分别准备活动方案、渠道预算和介绍网页，交付完整文件项目。” |
| 持续修改 | “保留原有内容，把待办按负责人分组，生成一个新版本。” |

## 实测表现

我们用真实模型完成任务，并检查回答和交付文件的实际内容。2026-11-11，使用 `deepseek-v4-flash` 对问答、结构化提取、JSON 文件生成和 CSV 数据汇总四类轻量任务各运行 3 次，完成了一轮串行单用户基线评测。

| 指标 | 本轮结果 |
| --- | --- |
| 任务成功率 | **12/12（100%）**，回答与文件内容均通过断言 |
| 模型调用 | 43 次，失败 0 次 |
| 工具调用 | 27 次，全部无异常返回 |
| Token 消耗 | 输入 94,955，输出 4,325；平均每任务约 **8,273 Token** |
| 请求级 Prompt Cache 命中率 | **97.67%**（42/43 次请求有缓存读取） |
| 输入 Token 缓存读取占比 | **85.18%**（80,884 / 94,955） |

| 延迟指标 | P50 | P95 | P99 |
| --- | ---: | ---: | ---: |
| 任务入队到完成 | 2.474 秒 | 7.919 秒 | 7.919 秒 |
| 单次模型请求 | 1.049 秒 | 1.567 秒 | 1.704 秒 |
| 模型首个有效输出块 | 0.735 秒 | 1.048 秒 | 1.128 秒 |

首个有效输出块包含文本、推理或工具参数，是客户端首输出延迟。以上为本轮小样本结果：12 次任务的 P95/P99 均落在最大值，适合作为后续优化的比较基线，不代表并发容量或复杂任务的总体表现。

评测脚本可重复运行，逐任务记录耗时、模型用量、缓存读取与工具事件。完整统计口径、任务明细和复现命令见 [Agent 运行指标实测报告](docs/validation/agent-metrics-baseline.md)。

## 技术选择

项目采用前后端分离架构。对话和能力配置在浏览器中完成，耗时任务交给独立 Worker，配置与执行记录持久化到 SQLite。文件保存在部署机器的磁盘上，无需额外部署 Redis，可在本地电脑或服务器运行，通过浏览器访问。

| 部分 | 技术 | 在项目中的作用 |
| --- | --- | --- |
| Web 界面 | React、TypeScript、Vite、Ant Design、TanStack Query | 对话、能力目录、模型设置和任务状态展示 |
| API 与后台任务 | Python 3.12、FastAPI、独立 Python Worker | 接收任务、后台执行，通过 SSE 同步进度 |
| 智能体编排 | AgentScope 2.0 | 组织专家执行、工具调用与多专家协作 |
| 持久化 | SQLite、SQLAlchemy、Alembic、本机文件目录 | 保存配置、任务、事件和文件版本 |
| 模型与工具 | DeepSeek / Qwen / Claude 原生接口适配、MCP | 使用统一模型配置，连接远程工具或后端进程型服务 |
| 办公交付 | python-docx、openpyxl、python-pptx、LibreOffice | 生成文档，执行渲染检查和表格重算 |

这些选择服务于一个目标：以较少的部署依赖，运行一个可以持续扩展的 AI 工作台。模型连接配置在服务端统一维护，使用者从可用列表中选择模型。

## 快速开始

### 1. 环境与安装

Python 3.12、uv、Node.js 24 LTS、pnpm 10.15.1。依赖由 backend/uv.lock 与 frontend/pnpm-lock.yaml 锁定，AgentScope 限定 2.0 系列。

先下载 Release 源码并解压，或克隆仓库：

```powershell
git clone https://github.com/6lcx/agentbuddy.git
cd agentbuddy
```

在仓库根目录执行：

```powershell
uv sync --project backend --locked
pnpm --dir frontend install --frozen-lockfile
```

安装后，一条命令启动本地工作台：

```powershell
uv run --project backend python scripts/start_local.py
```

脚本会初始化数据库、启动 API/Worker/Web 并打开浏览器。保持终端打开，完成或取消任务后按 `Ctrl+C` 停止；日志位于 `data/logs/`。默认仅监听本机，数据保存在本地 `data/`。已有服务占用端口时会提示退出，不会替你停止其他服务。

首次进入后点击“设置模型连接”，填入**你自己的**模型地址、密钥和模型名称，完成探测并设为默认即可开始。该本地启动方式使用 Vite；服务器常驻部署见 [部署维护手册](docs/deployment.md)。生成 Word/Excel/PPT 还需安装 LibreOffice，详见下方办公文件说明。

以下分步启动方式供手动管理服务时使用，与上面的启动脚本二选一。

### 2. 初始化数据库

安装后、首次启动前在根目录执行：

```powershell
uv run --project backend python -m agentbuddy.persistence.migrate
```

该命令使用 Alembic 升级到当前版本，可重复执行；不自动清空数据。默认数据目录是仓库根目录 data，与启动工作目录无关。API、迁移、Worker 如需更换目录，都设置相同绝对路径的 DATA_DIR。目录包含 SQLite、tmp、skills、projects 和 uploads。

升级 schema 前停止 API 和 Worker；迁移与 Worker 共用实例锁，Worker 正在运行时迁移会拒绝执行。不做自动降级；后续版本升级前备份数据库及文件目录。

### 3. 启动服务

**终端一：后端 API**（仓库根目录）

```powershell
uv run --project backend uvicorn agentbuddy.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

**终端二：Web 前端**（仓库根目录）

```powershell
pnpm --dir frontend dev
```

**终端三：任务 Worker**（仓库根目录）

```powershell
uv run --project backend python -m agentbuddy.worker
```

Worker 默认每 0.5 秒检查队列、5 秒心跳，30 秒无心跳显示过期；Ctrl+C 停止领取，最多等待 10 秒让当前 Operation 完成，超时后取消并标失败。强制结束后在途记录在下次 Worker 持锁启动时标失败，不自动重放，queued 保留。`--once` 处理一个有限批次后退出（最多一条 Operation 和两条业务 Run），适合诊断。重试需要重新提交 Operation。

启动后可访问：

| 入口 | 地址 |
| --- | --- |
| Web 工作台 | [打开工作台](http://127.0.0.1:5173/app/) |
| API 文档 | [查看接口](http://127.0.0.1:8000/api/v1/docs) |
| API 健康检查 | [检查连接](http://127.0.0.1:8000/api/v1/health) |

Vite 开发代理转发 API，无需在浏览器直接访问后端端口。健康接口只检查 API 存活；数据库与 Worker 状态请在工作台的“服务状态”页查看。

## 使用指南

### 配置模型，开始对话

升级前停止 API、Worker，执行上述迁移命令，再分别启动三个进程。最新迁移为 0004_collaboration，保留已有 Operation 数据。

1. 打开 `/app/settings/models`，添加 Qwen、DeepSeek 或 Claude 连接，填写 API 基地址和密钥。
2. 添加具体模型 ID，设置上下文与输出上限；运行文本/工具联合探测。该按钮会实际请求所配置的服务，Worker 必须运行。
3. 探测通过后设为默认模型，或在 `/app/chat` 选择可用模型，发送消息。
4. 首次发送创建项目；继续发送保留历史上下文。刷新页面或中断浏览器连接不会停止 Worker；左侧历史列表可重新进入对话。

密钥只写入服务端 credentials 表；读取配置不回显。编辑连接时密钥留空会保留旧值。修改连接或模型会使探测状态失效；新任务需要重新探测，已入队任务使用创建时的不可变配置和凭证引用。

API 基地址示例：Qwen 使用 `https://dashscope.aliyuncs.com/api/v1`（原生文本 Generation，地域/工作空间按实际账号调整）；DeepSeek 使用 `https://api.deepseek.com`；Claude 使用 `https://api.anthropic.com/v1`。填写基地址，不包含末尾的 `/messages`、`/chat/completions` 或 Generation 具体路径。不存在预置真实密钥或假可用模型。

当前已支持单专家、Skills、MCP、上传和文件版本。任务默认 1800 秒/120 次调用，上限 7200 秒/500 次；模型瞬时故障有限重试，重试计入预算。所有在途工作进入等待后暂停活动计时并释放执行名额。支持显式自动协作和 Word/Excel/PPT 文件交付。

POST /runs 必须带 Idempotency-Key；同 key 同请求返回原 project_id/run_id，同 key 不同内容或项目已有在途运行返回 409。SSE 在 /runs/{id}/events，支持 after 和 Last-Event-ID；事件持久化，完整 message.completed 为最终回答依据。纯问答不创建文件版本。

### 管理专家、技能与文件项目

1. 启动 Worker 后，能力目录 `/app/capabilities` 可见平台预置“项目文件整理” Skill。
2. 导入 Skill ZIP：根目录放 `skill.json`、`SKILL.md`；manifest 包含 name、description、version、scripts、requires。导入不执行脚本，启用后才能选择；可上传新版本并切回历史版本。
3. 新建专家，设置角色指令、可用模型、默认 Skills、连接器与允许的协作者。指定专家仅调用配置的协作者；自动协作由固定总控从已启用专家组队。
4. 连接器支持 HTTP（transport=http、url、可选 headers）和后端进程（transport=stdio、command、args、env）。也可上传含 mcp.json 和 Python entry 的包。测试与工具发现由 Worker 执行；默认外部写操作确认一次，明确 effects 中为 read 的工具直接调用。
5. 对话可选专家、临时增减 Skills/连接器、调整预算并上传文件/ZIP。会话选择不改模板；切换只影响下一轮。使用专家默认模型时不必另选模型。
6. 等待确认卡片显示具体操作；澄清卡片可补充文字。可取消本轮任务。成功文件在版本区下载，旧版保留；失败部分产物明确单独显示，不覆盖成功版。

输入、工作区和成功版本分别保存。检查报告列明路径/hash/JSON/CSV/Python 结构检查；不把语法通过冒充项目测试通过，Office 另附实际重算/渲染检查报告。包脚本在普通进程运行，本平台没有沙箱。

上传、包解析通过后台 Operation 处理，需 Worker 在线。API 上传采用 JSON base64（浏览器自动编码），单文件原始大小 50 MiB；Skill/MCP 包 20 MiB。ZIP 展开有大小、数量、重复路径和越界检查。

验证记录：[T05–T10](docs/plans/T05-T10.md)。`scripts/verify_t04.py` 已扩展到能力目录、Skill 导入启用、专家绑定、上传、版本生成与下载，继续保留原有 T04 回归。

DeepSeek 已有真实服务验证，源码和示例中不包含真实密钥。使用你有权调用的模型配置复现实测：

```powershell
uv run --project backend python scripts/verify_deepseek.py --source-env <已授权的env文件绝对路径>
```

加 `--configure-platform` 才会把来源的 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL 写入本机平台配置；普通验证在临时目录中生成 V1/V2 并核对实际内容。Qwen/Claude 真实联通仍待各自配置，禁止把本地协议测试视为它们已实测。

### 多专家协作与办公文件

Worker 首次启动会补齐预置专家和 Skills。能力目录点击“使用专家”或“用于新对话”进入草稿；示例按钮只填入输入框，不自动发送。普通对话默认不组队，选择“自动协作”才使用固定总控；子任务在页面独立展示。

办公依赖：安装 LibreOffice，并将 SOFFICE_PATH 指向其可执行文件；也支持 PATH 的 soffice。例如 Windows 环境可将其指向安装目录中的 `program/soffice.com`。服务器需要中文字体，Windows 使用微软雅黑，Linux 建议安装 Noto Sans CJK。Python 库通过 `uv sync --project backend --group dev` 安装。

DOCX 支持标题/段落/表格；XLSX 支持数据/公式/柱状图，发布前真实重算；PPTX 支持中文正文、自动续页和柱状图页。外部文件仅基础提取，自产文件通过同名 `.agentbuddy.json` 内容模型修改。PDF/PNG 预览及报告随下载包提供；校验失败不会发布成功版本。复杂版式、宏、旧版二进制 Office 不在范围内。

已授权模型组合测试：

```powershell
uv run --project backend python scripts/verify_team_office.py --source-env <已授权的env文件绝对路径>
```

脚本创建独立 data/validation 目录，使用真实 DeepSeek、三个办公子专家、LibreOffice 与两轮文件版本；包含实际 API 调用费用。不会修改平台默认模型。测试目录含凭证数据库，受 `.gitignore` 排除，请勿打包上传整个 data 目录。

开发 Skill 的 scripts/run_python.py 可执行项目内 Python 文件，退出码和日志作为测试证据。其他语言需要相应脚本或 MCP 才能声明已运行验证。阶段记录见 [T11–T15](docs/plans/T11-T15.md)。T16 本轮可执行验收已完成；Qwen/Claude 实服务与目标生产环境发布复核保留。

## 部署与维护

### 后台队列与状态

打开服务状态页，确认 SQLite 已就绪、Worker 运行中，点击“运行存储检查”。API 仅持久化入队，独立 Worker 实际检查临时文件读写及数据库设置，然后保存结果和事件；不调用模型，也不消费业务 Run 预算。

接口均带 API_PREFIX：

- GET /system/status：数据库就绪和 Worker 状态；未迁移返回 503。
- POST /system/diagnostics：返回 202 和 Operation ID。
- GET /operations/{id}：排队/运行/成功/失败及结果。
- GET /operations/{id}/events?after=0&limit=100：持久事件，按递增 ID 查询；不存在返回 404。

Operation 单槽执行，业务 Run 使用两个独立并发名额，同一项目最多一轮在途；数据库状态和事件同事务提交。短 BEGIN IMMEDIATE 事务避免重复领取，外部执行期间不持有写事务；数据库锁等待最多 5 秒。Worker 独占本机文件锁，心跳过期仅告警，不绕过仍持有的实例锁抢占执行。

### 自定义路径前缀

后端启动终端设置 `$env:API_PREFIX='/custom/api/v1'`；前端启动终端设置同一个 `API_PREFIX`，并设置 `$env:APP_BASE_PATH='/workspace'`。前端访问 `/workspace/`，后端访问 `/custom/api/v1/health`。路径不重叠，不包含查询参数。修改后重启服务；生产前端须重新构建。

示例文件位于各工程的 `.env.example`。前端 Vite 从 frontend/.env 读取；后端从启动工作目录的 .env 读取，因此根目录启动时优先使用终端环境变量，或将后端示例复制到根目录 .env。不提交真实密钥。

API_PROXY_TARGET 默认 http://127.0.0.1:8000，仅为前端开发代理配置。生产部署由 Nginx 保留 API 前缀转发到 Uvicorn，前端 dist 挂载在页面基路径，并为嵌套路由设置 index.html 回退；FastAPI 不提供前端页面。生产代理部署验收属于 T16。

### 部署、备份与恢复

部署、前缀配置、服务启动、升级与故障定位见 [部署维护手册](docs/deployment.md)。提供 Linux systemd 模板及 nginx 配置生成器；API 和 Worker 均应单实例运行。

离线完整备份和恢复：

```powershell
uv run --project backend python -m agentbuddy.backup backup C:/backups/agentbuddy.zip --data-dir C:/path/to/data
uv run --project backend python -m agentbuddy.backup restore C:/backups/agentbuddy.zip --data-dir C:/path/to/new-restored-data
uv run --project backend python -m agentbuddy.backup verify C:/path/to/new-restored-data
```

先停止 API/Worker 并完成或取消在途任务；恢复只写入不存在的新目录。备份包含 SQLite、历史文件、Skills 与 MCP 包；不会自动安装宿主依赖或重放旧任务。服务环境文件应单独保管。

Qwen/Claude 实服务按用户决定暂缓。T16 的本轮范围与证据见 [验收记录](docs/validation/T16.md)，不要把本地协议通过写成供应商实服务通过。

## 开发与验证

### 基础检查

```powershell
uv run --project backend ruff check backend
uv run --project backend ruff format --check backend
uv run --project backend pytest backend/tests
pnpm --dir frontend typecheck
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright install chromium
pnpm --dir frontend test:e2e
```

本机也可使用已安装的 Edge：设置 `$env:PLAYWRIGHT_CHANNEL='msedge'` 后执行 `pnpm --dir frontend test:e2e`，无需下载 Chromium。历史验证使用过 Edge，最新 UI 业务回归使用 Chrome（设置 `PLAYWRIGHT_CHANNEL=chrome`）；测试自带 Chromium 在验证机器上出现过 `spawn UNKNOWN`。

E2E 自动启动并停止前后端，需空闲 8000/5173 端口。运行自定义路径验证时，在同一终端设置 API_PREFIX 和 APP_BASE_PATH 后重新执行 test:e2e；两项配置由测试启动的前后端继承。

AgentScope 冒烟采用本地模型替身驱动真实框架，不调用远程服务，不验证 Qwen/DeepSeek/Claude 联通。测试范围和证据见 [T01](docs/validation/T01.md)，后续任务见 [Plan](plan.md)。

### 队列与路径前缀验证

```powershell
uv run --project backend ruff check backend scripts
uv run --project backend pytest backend/tests
$env:PLAYWRIGHT_CHANNEL='msedge'
uv run --project backend python scripts/verify_t02.py
```

verify_t02.py 创建隔离临时目录并迁移，启动真实 Worker，分别运行默认和自定义前缀下的浏览器测试，结束后清理；不操作默认 data。需要空闲 8000/5173 端口及已安装的浏览器。默认 E2E 仅验证基础页面；设置 T02_E2E=1 的完整模式由脚本负责。

详细证据：[T02 验证记录](docs/validation/T02.md)。Windows 本地验证记录见该文档；Linux 与部署验证范围见 [T16](docs/validation/T16.md)，目标生产环境仍需单独复核。

### 模型协议验证与接口类型

```powershell
uv run --project backend pytest backend/tests
$env:PLAYWRIGHT_CHANNEL='msedge'
uv run --project backend python scripts/verify_t04.py
```

脚本启动仅用于测试的本地原生协议服务（8099 端口）、隔离数据库与真实 Worker，浏览器验证配置→探测→两轮对话→刷新。使用固定的无效测试凭证，不会连接真实模型服务。需要空闲 8000、5173、8099 端口。

```powershell
uv run --project backend python scripts/export_api.py
pnpm --dir frontend generate:api
pnpm --dir frontend typecheck
```

OpenAPI 快照在 frontend/openapi.json，生成类型在 frontend/src/api/schema.d.ts。模型与对话响应类型从该文件引用；修改后端契约时同步生成。TypeScript 锁定 5.9.3，满足 openapi-typescript 的受支持版本范围。

历史验证记录：[T03](docs/validation/T03.md)、[T04](docs/validation/T04.md)。DeepSeek 实服务已完成验证；Qwen/Claude 仍待真实配置。本地模拟协议通过不代表实际提供方联通。

### 最新 UI 业务回归

```powershell
$env:PLAYWRIGHT_CHANNEL='chrome'
uv run --project backend python scripts/verify_t21_ui.py
```

脚本选择空闲端口，创建隔离数据，启动实际 API、Worker 和前端；模型由本地协议夹具提供，不调用真实供应商。覆盖模型配置、聊天恢复、Skill 导入、专家绑定、附件上传、两版文件、外部操作拒绝/批准和失败续做草稿。结束后停止本次启动的服务，保留证据目录，不停止已有服务。详见 [T21 UI 验收](docs/validation/T21-ui.md)。

### 项目文档

| 文档 | 内容 |
| --- | --- |
| [产品规格](docs/specs/platform.md) | 当前功能范围与行为约定 |
| [实施计划](plan.md) | 模块设计和任务安排 |
| [部署维护手册](docs/deployment.md) | 部署、升级与故障定位 |
| [15 类任务验收](docs/validation/T17.md) | 常用业务任务的实际验收记录 |
| [可靠性验收](docs/validation/T21.md) | 历史压缩、交付检查、恢复及已知限制 |
| [最新 UI 验收](docs/validation/T21-ui.md) | 浏览器业务链路与回归证据 |
| [项目原始对话记录](agentbuddy-codex-conversations-2026-11-11.zip) | 两条 Codex 项目对话的正文快照，含 Markdown、JSONL 和导出说明；图片仅保留引用 |
| [Spec / Plan / Test / Check 文档包](agentbuddy-spec-plan-test-check-2026-11-11.zip) | 规格、主计划、阶段计划、测试与验收记录，附阅读说明和文件校验清单 |
