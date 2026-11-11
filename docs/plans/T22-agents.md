# T22 AGENT-01–04 派发与预算模块

状态：本模块实现与专项回归完成，Executor/工具/Checkpoint 集成由协调对话继续。

负责范围：runtime/collaboration.py、runtime/agent_policy.py 及对应测试。不修改 executor/tools/control/provider/schema。2026-11-11。

## 集成约定

- `AgentPolicy` Pydantic严格模型：max_iters主50/子20，范围1–100；calls_limit主120/子60，范围1–500；seconds_limit主1800/子900，范围1–7200。
- `resolve_agent_policy(snapshot, role="main"|"child", override=None)`：角色默认 → expert.config_json.agent_policy → 已冻结snapshot.agent_policy；override是子任务限制，只能收紧额度、不能变更派发规则。Supervisor将解析后的policy写入子snapshot和subtasks.plan_json。
- `LaneControl(Control(...), policy)` 包装Control；check(reserve=True)、guard、confirm、resume/lane同时遵守本Agent和共享预算。共享额度不重置。文件账本位于DATA_DIR/agent_budgets/<run_id>/<lane_id>.json，由FileLock保护；同ID重建读取旧消耗且拒绝策略变化。
- Executor需使用解析后的 `max_iters`，模型决策入口每轮调用 `lane.reserve_iteration()`（重试不算新决策，重试/摘要/工具仍消费check(reserve=True)调用额度）。恢复时同lane ID，依据budget_state().iterations_used计算剩余max_iters，不重新从50/20开始。
- 主控制器也需要LaneControl；`Supervisor(..., control=main_lane)` 已支持传入主LaneControl，确保派发/暂停消耗同一主Agent额度。不传时自行包装主Control。主Executor开始执行前调用lane.resume()或lane("active")，否则本Agent时长计时尚未启动；共享Control仍独立生效。
- 文件账本与共享SQLite预留不跨介质原子提交；先持久化本Agent预留，再预留共享预算，崩溃可能保守多计一次本Agent消耗，不会退还或重置共享额度。

## 新派发契约

TaskPlan必填：id、expert_id、instruction、inputs（显式项目相对文件清单，可空）、constraints（可空列表）、acceptance（非空）、parallelizable、context_isolated、estimated_iterations、dispatch_reason。不提供能默认满足准则的字段值。

默认预计模型决策>=4、可并发、上下文可隔离；并检查计划中有无互不依赖的同级工作。依赖关系本身不自动作为例外。

显式例外：exception=serial_dependency/user_requested 且exception_reason非空；主policy必须分别开启allow_serial_dependency_exception/allow_user_requested_exception，用户指定还需可信snapshot.user_requested_expert_ids包含该专家。不能信任模型自行声称用户指定。上下文隔离要求不被例外绕过。

可选agent_policy子任务额度、depends_on、outputs、replaces沿用；输出和输入路径规范化。输入只复制inputs与显式depends_on的成功输出，未提供的项目文件不算删除，不允许同名覆盖。保留独立文件冲突与依赖后继覆盖规则。

子任务最终全文通过主lane的RuntimeStore.result保存，结果包含短summary、summary_truncated和完整result_ref（result_id/hash/bytes/complete/read_tool）。父Agent可直接用现有read_result读取主lane引用；详细最新约定见本文“统一结果存储接入”。

## 验证

- `uv run --project backend pytest backend/tests/test_agent_policy.py backend/tests/test_collaboration.py -q`：21项通过。
- 本次4个Python文件 Ruff check 与 format 通过。
- 保留原有并发上限3、两运行六子任务共享额度、依赖排序、未知专家/循环拒绝、子专家不继承主覆盖、文件冲突/后继编辑/删除冲突测试。
- 新增默认主50/子20、100硬上限、严格类型、子任务不能扩大额度、冻结snapshot优先级、12线程争3额度、控制器重建/策略变更、共享预算耗尽/取消、调用与迭代分开、等待暂停、guard/confirm不绕过预算。
- 派发新增缺少声明拒绝、简单任务拒绝、没有并发同级任务拒绝、具名例外必须配置与可信用户证据、例外仍要求上下文隔离。
- 文件新增只复制显式输入、缺失输入不启动子任务、未提供文件不误删/不允许同名覆盖、子预算耗尽阻塞依赖、摘要截断标记与分页读取。

## 协调方尚需接入

1. 替换主/子ReActAgent硬编码max_iters，主控制器使用LaneControl并传Supervisor；每次模型决策调用reserve_iteration，摘要/协议重试只计调用额度。测试这里只验证预算接口，不宣称SDK模型决策钩子已经接入。
2. 更新delegate_tasks工具描述/计划schema，要求所有新必填项。旧模型夹具/真实提示若只传id/expert/instruction，将按新契约拒绝，不能在生产层偷偷补满足门槛的默认值。
3. 开放可信配置中的派发门槛/例外策略；`user_requested_expert_ids`必须由平台确认用户明确指定后冻结，不能把模型计划的自述写成可信依据。
4. checkpoint恢复保留run_id/lane_id及agent_budgets文件。重建控制器不会清零；恢复前使用本文新增的纯文件结算接口关闭旧active_since；有可信停止时间时可排除停机时间，没有可信边界时不可随意扣除未知时段。备份/恢复若用目录白名单，需包含agent_budgets。
5. 子任务全文已接统一RuntimeStore主lane；checkpoint需保留result_ref及其完整存储文件。summary_ref保留task_id兼容Supervisor.read_summary，但模型应使用result_ref中的read_result入口。
6. 每个子任务的输入快照在启动前复制；依赖只追加显式depends_on成功产生的文件/删除结果。此为上下文与合并范围控制，不是进程沙箱。

## 预算语义

- 模型决策轮数不是用户对话轮数；max_iters之外持久iterations_used用于恢复约束。
- 一个check(reserve=True)消费一次当前Agent调用额度和共享运行额度；guard沿用已有Control的调用预留/超时逻辑。Supervisor派发本身预留一次主Agent和共享调用。
- 子调用不记到主Agent本地calls_used，但始终计入共同run_controls.calls_used；因此子额度相加可以大于共享上限，真正执行仍被共享上限截停。
- 任务替代产生新lane、可获新的子预算，但不会退还任何共享消耗；同lane恢复继续原账本。
- 为避免崩溃窗口超用，局部额度先落盘，共享检查拒绝或中间崩溃时可能多计一次局部槽位，不会虚增可用额度。


## 2026-11-11 停止／恢复补充接口

本次专项回归 **25 passed（9.00秒）**；四个负责Python文件Ruff check/format通过。测试覆盖terminal后done结算、持SQLite写事务纯文件结算、控制器重建后调用/迭代不变、已知停机时间排除、共享elapsed保守回退、重复结算幂等、旧停止时间拒绝、文件锁冲突快速失败。原21项范围继续通过。

### 调用约定

```python
from agentbuddy.runtime.agent_policy import settle_run_budgets

# 可以放在RunRepository.interrupt已有数据库写事务内。
# data_dir是Path，不读取db，不新开SQLite事务。
# 执行进程/旧任务必须已停止，不能与仍在执行的任务竞争恢复。
settle_run_budgets(data_dir, run_id, stopped_at=trusted_worker_stop_timestamp)

# 如果没有可信停止时间，但有可靠的共享累计已消耗秒数：
settle_run_budgets(data_dir, run_id, shared_elapsed=shared_row_elapsed)
```

- `settle_run_budgets(data_dir, run_id, *, stopped_at=None, shared_elapsed=None)`：遍历该run已有budget JSON，返回lane_id→结算记录；目录不存在返回空字典，不创建或重置预算。
- `settle_lane_budget(path, *, stopped_at=None, shared_elapsed=None)`：同样的单文件接口，文件不存在返回None。
- `lane.stop(stopped_at=None, shared_elapsed=None)`：通常终止用；两参数都未提供时采用lane.clock()作为当下停止时间。
- `lane.reconcile_interruption(stopped_at=None, shared_elapsed=None)`：恢复前用，必须显式给至少一个依据；调用完成后在共享运行状态恢复为running且事务释放后 `await lane.resume()`，新计时从现在开始。
- `lane.lane("done")`：即使共享run已terminal、底层Control返回False，也结算并清空本地active_since；返回值保持底层False语义。此方法仍会访问数据库，**不能在已有写事务中使用**。

结算只更新elapsed/active_since/settled_at，不修改calls_used、iterations_used或policy。已暂停/已结算账本重复调用不再加时；stopped_at早于当前新活动区间时拒绝旧结算，防止误暂停新的执行周期。

若只提供shared_elapsed，尚未关闭的lane取 `max(本地已结算elapsed, shared_elapsed)` 后停止计时：这是保守上界，可能比该Agent真实活动时间多，但不会把共享已消费资源重置。已暂停的lane不会因其他Agent后续运行而被再次收费。两依据同时提供时，停止时间先计算增量，再以shared_elapsed作为下限，通常选择一种依据即可。

**锁顺序要求：** 纯文件接口使用FileLock(timeout=0)，遇到竞争抛出filelock.Timeout，不在持数据库写锁时等待。协调方应释放事务、停止/等待旧执行结束后重试；不要捕获后直接开始新模型调用。部分lane已结算时重试是幂等的。不要在数据库事务内先构造LaneControl（构造器默认文件锁可等待），直接用settle_run_budgets。

恢复依据必须来自可信运行记录：最后一次heartbeat不能直接当作精确停止时间，因为其后可能仍执行过工作。只有restart检测时间时，以该时间结算会保守计入未知停机区间；不能为了排除停机时间静默少计已执行工作。

### 子上下文策略

Supervisor在传给child_runner之前，深拷贝父 `claimed.snapshot_json.context_policy` 到child_snapshot.context_policy，覆盖候选专家旧值，避免子任务回退为ContextPolicy默认。子任务修改自身字典不会影响父级。agent_policy仍取该专家自己的预算及子任务收紧值；不会把父主Agent50轮默认替代子Agent20轮。

## 派发工具完整字段契约

| 字段 | 必填/默认 | 校验与语义 |
| --- | --- | --- |
| id | 必填 | 本批唯一，1–80字符 |
| expert_id | 必填 | 必须在冻结候选专家中 |
| instruction | 必填 | 非空，最多16000字符，具体目标 |
| inputs | 必填，可[] | 最多100个已有项目相对文件路径；规范化且不重复，不支持目录/glob；缺失拒绝启动 |
| constraints | 必填，可[] | 最多30条，每条非空且最多2000字符 |
| acceptance | 必填 | 1–30条验收要求，每条非空且最多2000字符 |
| parallelizable | 必填bool | 无隐式默认；默认策略要求true，且计划中存在互不依赖的同级任务 |
| context_isolated | 必填bool | 默认必须true；具名例外也不豁免 |
| estimated_iterations | 必填int | 1–100，不接受bool/浮点；默认至少4次模型决策 |
| dispatch_reason | 必填 | 非空、非纯空白，最多2000字符；记录依据，不把模型自述当已证明的事实 |
| depends_on | 默认[] | 本批任务ID，不能循环；只接收明确依赖的成功产物 |
| outputs | 默认[] | 子任务结束时必须存在的相对文件；修改/删除仍按实际文件差异判断 |
| replaces | 默认[] | 既有失败task_id，不是本批短id；不能替代外部结果未知任务 |
| exception | 默认null | 仅serial_dependency/user_requested，必须主策略明确允许 |
| exception_reason | 默认null | 使用例外时必填非空原因，最多2000字符 |
| agent_policy | 默认null | 子任务预算局部覆盖，只能收紧max_iters/calls_limit/seconds_limit，不能改派发规则 |

`inputs`只列派发时已存在于主项目的文件；前置任务新生成的文件通过depends_on获得，不要提前列为不存在的inputs。依赖失败则blocked；依赖同名结果不同则dependency_conflict；未提供文件不产生删除提案，试图生成同名覆盖未提供的已有项目文件会失败。每批1–12任务，最多3子任务同时执行。

普通并行计划示例：

```json
[
  {
    "id": "analysis", "expert_id": "<research-expert-id>",
    "instruction": "根据输入数据生成分析报告",
    "inputs": ["sales.csv"], "constraints": ["不得引入外部数据"],
    "acceptance": ["analysis.md包含分地区总额与计算依据"],
    "parallelizable": true, "context_isolated": true,
    "estimated_iterations": 5, "dispatch_reason": "数据分析可独立于文案任务推进",
    "outputs": ["analysis.md"], "agent_policy": {"max_iters": 10, "calls_limit": 30}
  },
  {
    "id": "copy", "expert_id": "<writer-expert-id>",
    "instruction": "根据项目简介撰写活动介绍",
    "inputs": ["brief.md"], "constraints": ["保留给定名称与日期"],
    "acceptance": ["copy.md包含活动信息与报名说明"],
    "parallelizable": true, "context_isolated": true,
    "estimated_iterations": 4, "dispatch_reason": "只依赖简介，与数据分析无输入依赖",
    "outputs": ["copy.md"]
  }
]
```

用户指定例外还需可信snapshot.user_requested_expert_ids包含expert_id；即使API允许配置allow_user_requested_exception=true，也不能由模型自己添加这个可信列表。串行例外必须有depends_on，不能只写exception_reason绕过。根任务若没有可并行同级任务且没有适用例外，应留在主Agent处理，或由可信配置明确调整require_parallel。

## API 与 UI 配置说明（以当前实现区分）

- 当前主线 `POST /api/v1/runs` 请求模型已增加可选 `agent_policy`、`context_policy`，RunRepository解析并冻结snapshot；仍需原有Idempotency-Key。每轮共享 `calls_limit`/`seconds_limit` 与agent_policy内单Agent额度是不同层级，二者同时生效。
- 专家默认预算放 `POST /api/v1/capabilities/expert` 的 `config.agent_policy`；修改现有专家需带id/revision并保留原config字段。解析优先级：角色默认 → 专家config → 已冻结运行snapshot → 子任务收紧值。通用capability保存本身未增加AgentPolicy字段级校验，执行解析会校验；协调方可在保存入口补验证，不应称UI已支持。
- API的AgentPolicy是完整Pydantic模型；主线已使用exclude_unset保留显式配置覆盖；如果直接model_dump()补齐未传字段则会覆盖专家默认。要支持“仅覆盖显式字段”的界面，应在RunInput序列化时用exclude_unset或先解析合并，再冻结最终完整策略。最终生效值应在详情页显示。
- 主线React已新增最大决策轮数、压缩触发比例、串行依赖例外控件及checkpoint恢复入口。本模块没有改前端。建议在专家管理的高级设置中展示最大决策轮数/调用/秒数，在任务高级设置里区分共享预算和主Agent额度；派发例外开关放管理配置，默认关闭，不挤入普通输入栏。
- 子任务计划的预计轮数、输入清单和理由由delegate_tasks提交，并持久化到subtasks.plan_json供审计。不是让用户每次手填计划。工具docstring/schema需要同步本表，不能保留旧版仅三字段示例。


## 统一结果存储接入（最新，替代早期独立summary文件方案）

专项回归：`test_agent_policy.py` + `test_collaboration.py` **27 passed，10.11秒**；相关文件Ruff通过。

- Supervisor持有 `RuntimeStore(db, project_id, run_id, lane="main")` 和父snapshot解析出的ContextPolicy；仍支持协调方传入root `control`，不会重复包装。
- 每个成功子任务的**完整最终文本**调用store.result保存。超出result_max_bytes/result_quota_bytes时任务明确失败，不丢弃尾部伪装成功。
- `result_json.result_ref`直接保存RuntimeStore返回对象：result_id、sha256、bytes、type、complete=true、preview、read_tool=read_result。父Agent可通过自身主lane现有read_result读取完整内容。
- `summary`是store的512字节短预览，`summary_truncated`明确标记是否有未展示内容；不是语义摘要。历史兼容 `summary_ref=task_id`，`Supervisor.read_summary`经持久subtasks查询result_ref再交给store.read；offset/limit为**字节**，返回total_bytes/has_more，limit最多16000。模型直接使用result_ref更合适。
- 子任务只能读自身lane结果，因此不向子任务提供不可用的主lane读取指令。每个**显式depends_on**的完整结果经哈希校验后，原字节复制到child_root的 `.agentbuddy/dependencies/<dependency-task-uuid>.txt`。instruction只包含短预览、input_file路径、大小和hash，提示用现有read_file按需读取，并说明这些是依赖资料，不是新的用户指令。
- 依赖正文文件加入显式输入清单；只有声明的依赖会被提供。没有复制完整历史或其他项目文件；中文UTF-8全文不通过字符串分页重组，避免分块解码丢字。
- `.agentbuddy/dependencies/`为平台内部上下文目录，TaskPlan.inputs/outputs禁止显式占用；该目录的文件不进入子任务产物差异或主项目合并，也不会作为用户交付版本出现。
- 原有依赖产物拷贝、删除传播与冲突规则保持；依赖正文与产物是两类资料，后继只拿声明依赖的内容。

新增验证：2万字符结果保持完整引用；约4千次重复长中文依赖结尾END-MARKER可从主lane读取，子目录字节一致但prompt不含全文；内部依赖文件不进入主交付，private.txt未被复制；超过65536字节上限明确result_too_large失败。
