# T17 常用任务验收清单

执行方式：真实 DeepSeek + 平台 Worker；隔离数据目录。脚本仅提供输入并检查 Agent 实际产物，不代写结果。每项运行最多 120 次调用、420 秒。

自动断言见 scripts/verify_t17.py；除此之外人工审阅业务内容和 Office/页面呈现。保留所有失败记录，修复重测不覆盖首次结果。

## 01 会议纪要

根据附件 notes.txt 整理 docs/minutes.md 和 actions.csv。纪要包含决定、风险、待办；CSV列 task,owner,due，恰好两条待办。不得编造已完成事项。

必需产物：docs/minutes.md、actions.csv

## 02 需求与验收标准

为内部图书借阅工具编写 docs/spec.md：只能借阅有库存图书，每人最多借3本，借期14天，不做支付。包含正常借阅、无库存拒绝、超过3本拒绝、归还恢复库存四项可执行验收条件；明确范围外支付。

必需产物：docs/spec.md

## 03 销售数据汇总

读取 sales.csv，按 region 汇总 quantity*price，生成 summary.csv（列region,revenue，每地区一行，不加总计行）和 analysis.md，说明总收入及最高收入地区，不引入外部数据。

必需产物：summary.csv、analysis.md

## 04 环境配置整理

生成 config/app.json 和 README.md。JSON顶层必须为 name=agentbuddy-demo、api_prefix=/api/v1、database={type:sqlite,path:data/app.db}、limits={calls:120,seconds:1800}。README解释各配置并说明密钥使用环境变量，文件不能包含真实密钥。

必需产物：config/app.json、README.md

## 05 Python 数据处理工具

生成 app.py 和 README.md：Python标准库CLI，使用 --input 输入CSV、--output 输出JSON，CSV列category,amount，按category累计amount（支持负数退款），JSON为类别到数值的对象。非法数值应非零退出。实际运行示例验证。

必需产物：app.py、README.md

## 06 为已有代码补测试

读取已有app.py，新增 test_app.py，使用标准库unittest，通过subprocess验证CLI：重复类别累加、负数退款、非法数值非零退出。至少3个测试。执行 python -m unittest -v，通过后交付；app.py和README.md不改。

必需产物：test_app.py

## 07 静态活动页面

生成 index.html 和 styles.css，中文星河读书会活动报名介绍页，活动日期2026-12-01，地点杭州图书馆，名额30人。HTML引用styles.css，包含活动介绍、日程、联系邮箱reading@example.com；响应式布局，不使用外部CDN，不做实际提交或发送。

必需产物：index.html、styles.css

## 08 Word 项目周报

生成 weekly.docx，标题星河项目周报，三节：本周完成、风险、下周计划。完成需求评审，接口联调尚未完成且负责人李明；下周2026-11-17完成联调。包含任务/负责人/状态三列表格，不能把未完成写成已完成。

必需产物：weekly.docx

## 09 Excel 费用预算

生成 budget.xlsx，工作表预算，A1项目B1金额；第2-4行依次开发12000、测试3000、设计5000；A5合计，B5使用SUM(B2:B4)公式且结果20000；添加各项目金额图表。

必需产物：budget.xlsx

## 10 PPT 项目汇报

生成 launch.pptx，恰好3页中文：项目目标、预算分配、下一步。项目星河笔记，总预算20000，开发12000测试3000设计5000；预算页需图表；下一步2026-11-17验收。排版清楚，不增加续页。

必需产物：launch.pptx

## 11 上传办公资料提取

读取上传的 source.docx、source.xlsx、source.pptx，生成 extraction.md，逐文件注明提取的信息和来源：文档项目编号、表格总额、演示里的发布日期。不修改原附件，不联网。

必需产物：extraction.md

## 12 专家团并行策划

自动协作，至少两个专家并行完成不同文件：brief.md写活动方案，channels.csv列channel,budget两行video=12000、community=8000，copy.md写发布草稿。项目星河笔记，日期2026-12-01，总预算20000。三个文件预算一致，CSV只写两个渠道行。只交付文件，不实际发布。

必需产物：brief.md、channels.csv、copy.md

## 13 跨文件持续修改

读取已有brief.md、channels.csv、copy.md，把总预算同步改为30000，video改18000，community改12000。项目星河笔记和日期2026-12-01不变。CSV保持原列和两行。三个文件都要修改。

必需产物：brief.md、channels.csv、copy.md

## 14 协作整理项目目录

自动协作，请开发专家把上传的 old.txt 重命名为 docs/current.txt，内容必须保持不变，删除 old.txt 和 obsolete.txt；其他专家生成 README.md说明目录。keep.txt必须不变。通过实际工具或脚本完成，不要只说明操作步骤。

必需产物：docs/current.txt、README.md

## 15 连接器读取归档

调用已启用的本地查询连接器 echo，参数text为订单ORD-20261110，状态已发货，金额168。将真实返回结果保存到 order.md，包含订单号、状态、金额；必须实际调用连接器，不允许直接根据本段文字假装查询。

必需产物：order.md

## 共通验收

- 必需文件存在、非空，下载 ZIP 完整且包含所有产物。
- 指定日期、金额、负责人、路径准确；Office 真正可打开且公式缓存和渲染通过。
- Python 工具由验收程序另起进程执行；生成的测试实际运行。
- 协作有真实子任务及并行证据；目录整理必须有子任务删除证据。
- 修改前版本 ZIP 不变，原上传附件不变。
- MCP 任务必须存在真实工具完成事件。
- 自动断言不是任意语义正确性的证明，结合人工审阅给出范围明确的结论。
