# 部署、升级与恢复

## 自行部署与模型配置

AgentBuddy 以可自行部署的源码版本交付。个人使用无需服务器或域名：安装 README 中的依赖后，在仓库根目录执行 `uv run --project backend python scripts/start_local.py`，即可启动仅监听本机的工作台。`--check --no-browser` 可检查启动后自动停止服务。

每个实例的管理员在模型设置页填写自己的服务地址、API Key 和模型名。密钥保存在该实例的服务端数据库，项目不附带作者的模型配置、个人数据库或公共调用额度。模型请求直接由部署实例访问配置的服务商；上传文件与生成结果保存在部署机器的数据目录，模型处理任务所需内容会发送到所选模型服务。

本地快速启动使用 Vite，保持终端开启即可。下方 Linux/systemd/Nginx 方式用于用户自己的服务器常驻部署；本仓库不提供公共托管体验站。

## 范围

单机可信工作空间，API 和 Worker 各一个进程。前端独立构建，nginx 提供同源静态资源及 API/SSE 反代。默认页面 `/app`、API `/api/v1`。不引入 Redis、数据库服务或沙箱。

Linux 验收环境为本机 Ubuntu 24.04 WSL；独立生产主机的网络、域名、TLS 和长期服务运行仍应在目标主机验证。Qwen/Claude 实服务按用户要求暂缓，DeepSeek 已验证。

## Linux 准备与启动

1. 将仓库部署到 `/opt/agentbuddy`，使用 Python 3.12、uv、前端 package.json 的 pnpm 版本。运行 `uv sync --frozen --project backend --group dev`、`pnpm --dir frontend install --frozen-lockfile`、`pnpm --dir frontend build`。
2. 安装宿主依赖：`apt-get install libreoffice-writer libreoffice-calc libreoffice-impress fonts-noto-cjk nginx`。用 `soffice --version` 和 `fc-match 'Noto Sans CJK SC'` 检查；实际可用性以 Office 验收为准。
3. 创建专用 `agentbuddy` 用户和 `/var/lib/agentbuddy`、`/var/lib/agentbuddy-nginx`，目录归该用户所有。将 `deploy/agentbuddy.env.example` 放到 `/etc/agentbuddy.env`，按机器设置 DATA_DIR、API_PREFIX、SOFFICE_PATH。密钥通过模型设置页写入数据库，不放进版本库。
4. 用同一环境先执行 `python -m agentbuddy.persistence.migrate`。首次运行必须先迁移；后续迁移要求 API/Worker 都停止，程序会检查进程锁。
5. 将 frontend/dist 的内容复制到 `/opt/agentbuddy/www/app/`；该目录对应页面前缀。执行 `backend/.venv/bin/python scripts/render_nginx.py --root /opt/agentbuddy/www > deploy/nginx.conf`。这是独立 nginx 主配置，不是 sites-enabled 片段。
6. 用 `nginx -t -p /var/lib/agentbuddy-nginx/ -c /opt/agentbuddy/deploy/nginx.conf` 检查。将 deploy 下三个 `.service` 文件放到 `/etc/systemd/system/`，执行 daemon-reload，再启动 agentbuddy-api、agentbuddy-worker、agentbuddy-web。
7. 默认代理监听 `127.0.0.1:8080`；访问 `/app/`、嵌套路由刷新、`/api/v1/health` 和 `/api/v1/system/status`。需要远程访问时按实际部署修改监听/外层入口。

`health` 只表示 API 存活；system/status 才包含数据库、Worker 心跳、可用磁盘和渲染器定位。渲染器“已找到”不等于所有文件都能转换。

自定义前缀必须同时修改前端构建环境 APP_BASE_PATH/API_PREFIX、API 服务环境 API_PREFIX，以及 render_nginx 的 `--app-prefix` / `--api-prefix`。静态目录随页面前缀变化。不要只改其中一处。

nginx 已关闭代理缓冲并设置长读取超时；SSE 断线后通过事件游标重连，浏览器断开不取消任务。

## Windows 启动与停止

沿用 README 的 API、Worker、Vite 三进程开发启动方式。办公渲染器支持 SOFFICE_PATH、PATH 及本机 data/runtime/libreoffice/program/soffice.com。API/Worker 必须使用同一个 DATA_DIR。

正常停止 Worker 使用 Ctrl+C，等待活动任务收尾；超过 worker_shutdown_seconds 后取消并标记中断。独立 Windows Worker 使用 Job Object，进程意外结束时系统终止其后代进程；Linux 服务用 KillMode=control-group。直接嵌入 Python 的 Worker.run 不提供 Windows 全进程树约束；正式运行使用 `python -m agentbuddy.worker`。

## 备份

1. 停止用户提交任务，等待本轮完成或明确取消；清空排队操作。停止 Worker 和 API。备份工具同时争取两把进程锁，存在在途/排队工作时拒绝。
2. 执行：

```text
python -m agentbuddy.backup backup /backup/agentbuddy-20261110.zip --data-dir /var/lib/agentbuddy
```

3. 备份包含 SQLite backup 快照、projects/uploads/versions、Skills 与进程 MCP 包、文件清单及 SHA256；先检查 DB 完整性/外键和资产/Skill 引用。它排除临时目录、渲染器安装、测试数据和备份目录，避免重复备份；宿主依赖应按部署步骤重建。
4. 将服务环境文件与部署版本信息随备份单独保存。数据库包含模型/连接器凭证，备份应与原数据采用相同的保管方式。
5. 备份输出必须在源 DATA_DIR 外，已有备份不会被覆盖。复制单个正在写 WAL 的 sqlite 文件不是完整备份。

## 恢复与升级

```text
python -m agentbuddy.backup restore /backup/agentbuddy-20261110.zip --data-dir /var/lib/agentbuddy-restored
python -m agentbuddy.backup verify /var/lib/agentbuddy-restored
```

- 目标必须不存在。先在临时目录核对完整文件清单、每个 hash、数据库和引用；全部成功后才原子重命名为目标目录。损坏包不留下可启动的恢复目录。
- 指向新目录启动 API/Worker，核对项目、历史下载和模型探测，再继续一个测试任务。旧目录保留，确认后再切换服务环境 DATA_DIR。
- 当前进程 MCP 的包参数路径会按新 DATA_DIR 重定位；跨操作系统的 Python 命令路径、系统依赖、外部凭证可用性需要重新配置/测试。历史运行快照不会被改写或自动重放。
- 升级：停止接单/服务 → 完整备份 → 更新代码及锁定依赖 → 迁移 → 冒烟 → 恢复接单。失败则停服务、用对应旧代码和完整备份恢复到独立目录；不执行未经验证的 downgrade。
- 单文件和 ZIP 下载会验证内容 hash。ZIP 条目使用固定元数据，恢复不会仅因文件时间变化而改变下载字节。

## 定位与维护

- Worker 无心跳：看系统页，再查 `journalctl -u agentbuddy-worker`；禁止同时启动第二个 Worker。
- 模型保存后不可用：重新运行文本/工具联合探测；不会静默换供应商。Qwen/Claude 暂缓实测不影响已配置 DeepSeek。
- 任务失败：先看 run_id 的活动记录、子任务、确认卡片、脚本日志、预算和部分产物。重启后运行中任务标 interrupted，不自动重复外部写操作。
- 等待输入：所有活动子任务停下才暂停计时；其他正在运行的子任务仍占用预算。恢复须重新取得全局执行名额。
- Office 失败：检查 SOFFICE_PATH、中文字体、输入格式和逐页报告。外部文件只基础提取，不保证宏/复杂版式保真。
- 磁盘不足：发布前检查复制大小并预留 10 MiB；复制失败不改旧版本。先备份再人工处理旧数据，平台不自动清理成功版本。
- 迁移/备份报锁冲突：停止对应 API/Worker，确认进程确实退出再重试；不要删除锁文件强行绕过锁。

Windows 进程树行为依据 [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)，并由本项目强制终止测试验证。服务文件是可修改部署模板；不代表已替用户发布到公网。
