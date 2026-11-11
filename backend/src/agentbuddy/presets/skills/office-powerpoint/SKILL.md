# PowerPoint 演示处理
生成分页演示稿，读取外部 PPTX，修改本平台生成的演示文稿。

调用 generate_office(kind="powerpoint", path=项目相对路径, content=结构化内容) 生成真实文件。
修改时先 read_file 读取同名 .agentbuddy.json，保留未修改内容，再 edit_office 提交完整内容。
外部文件只能用 read_office 提取内容，不能承诺宏、复杂排版或嵌入对象原样保留。
所有产物交付前由服务器 LibreOffice 渲染并检查，Excel 还会重算公式；依赖缺失或校验失败不得声称成功。
不要使用 write_file 写二进制 Office。标题和表格必须有意义，不可捏造数据或来源。
Word content: title, sections 数组（heading, paragraphs 字符串数组, tables 三维数组）。
Excel content: sheets 数组（name, rows 二维数组, formulas 数组 cell/formula/expected, chart 布尔）。公式必须以 = 开头，已知结果填写 expected。
PowerPoint content: slides 数组（title, bullets 字符串数组）。每页最多 6 条，每条最多 160 字，标题最多 60 字；内容过多拆成多页。

长正文会自动拆成续页。可选 chart={categories:[类别],series:[{name:系列名,values:[数值]}]}，图表单独占一页。
