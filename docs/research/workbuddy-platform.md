# WorkBuddy 与多专家 Web 平台调研

调研日期：2026-11-10。第一版目标：Web 配置专家、绑定 Skills/MCP、运行任务。

## 官方已证实的方法

WorkBuddy 产品页区分领域专家、云端助理、多 Agent 协同；专家可结合岗位说明书、SOP 和知识库，项目空间可共享专家、Skill 和连接器。官网描述了拆分任务、并行执行、汇总结果的协作方式，但没有据此公开其内部编排算法。[官方产品页](https://cloud.tencent.com/product/workbuddy)

Managed Agents 是更接近本项目的企业托管开发形态：创建 Agent 时选择模型和 System Prompt，可通过 Python/TypeScript ADK 定义注册；为 Agent 绑定 Skill、MCP 连接器，并在独立凭证管理中配置 OAuth/API Key；再经过 Test Run、版本发布、运行追踪。一个 Skill 可绑定多个 Agent。其接入方式包括 API、VPC、A2A、IM 和 cron；企业绑定有文档所述访问限制，不能默认所有模式均可同时使用。[官方快速入门](https://cloud.tencent.com/document/product/1831/140236)

桌面本地文件操作与企业云端托管是不同执行边界，不能直接把桌面能力推断成 Web 服务的默认能力。

## 技术栈证据边界

已公开的是 Python/TypeScript ADK、MCP 连接器、OAuth/API Key 凭证管理、API/A2A 等接口及版本、Trace 能力。上述是产品对外能力，不等于后台全部实现语言。

本次一手资料未披露前端框架、数据库、队列、是否采用 LangGraph、具体沙箱及调度实现，因此不能将 Electron、React、Redis 或某个 Agent 框架写成 WorkBuddy 的已确认技术栈。

## 本项目建议架构（设计建议，不是腾讯内部实现）

采用配置驱动的专家模板和独立任务运行实例。专家定义角色、模型、可用 Skill 版本、MCP 工具白名单、知识来源及输出要求；每次运行单独保存状态、上下文、事件、产物与消耗。

Skill 描述完成任务的方法，可包含说明、脚本和资料；MCP 提供访问外部工具与资源的标准连接；专家负责在授权边界内选择方法与工具。Skill 不是独立 Agent，也不必每次触发额外模型调用。

推荐第一版：React/Next.js 管理界面，Python/FastAPI API 与独立执行 Worker，PostgreSQL 存配置、运行状态及事件，对象存储保存产物；初期用数据库任务领取即可，有实际吞吐需求再引入 Redis 队列。运行内核可使用可持久化的状态机框架，也可以先用清晰的自有循环；不要为了多专家提前建立复杂自治团队。

执行链路：选择专家 → 建立运行快照 → 加载可用 Skill 摘要 → 按需读取完整 Skill → 注入允许的工具 → 模型决定调用 → 服务端校验授权并执行 → 记录事件与产物 → 验收或停止。SSE 将事件流推送到界面。

建议数据实体：AgentDefinition、AgentVersion、SkillVersion、AgentSkillBinding、MCPServer、CredentialReference、AgentToolBinding、Run、RunEvent、Artifact。凭证引用绑定用户/租户，不将长期密钥放入 Prompt。

第一版只做单专家执行，同时支持多个专家模板；第二阶段增加总控专家调用子专家，并给子任务明确输入、输出、预算及超时。共享产物和摘要，避免共享所有历史上下文。必须区分“配置了多个专家”和“一次任务中多个 Agent 协作”。

最小闭环：创建专家、上传版本化 Skill、连接 MCP 并授权工具、启动/取消任务、显示工具调用事件、下载结果、查看失败原因。对外写入设置审批策略；重试需考虑幂等性，暂停和恢复需持久化检查点。质量指标包括任务完成率、产物验收率、工具失败率、耗时和单任务成本。
