import type { Capability } from './Capabilities';

export function capabilityInfo(row: Pick<Capability, 'kind' | 'config_json'>) {
  const name = row.config_json.name;
  const category = /Excel|表格/.test(name) ? 'sheet' : /PowerPoint|PPT|演示/.test(name) ? 'slides' : /Word|文档/.test(name) ? 'document' : /开发|代码/.test(name) ? 'code' : 'research';
  const content = {
    sheet: { symbol: 'X', color: 'green', summary: '把数据整理成清晰的表格，用公式计算，用图表表达。', tags: ['数据整理', '公式计算', 'Excel'], examples: ['生成一份项目预算表，列出开发、测试和设计费用，并用公式求和。', '读取我上传的销售表，按地区汇总销售额并生成新表格。', '修改本项目生成的预算表，增加一项 3000 元的测试费用。'] },
    slides: { symbol: 'P', color: 'orange', summary: '把想法变成结构清晰的演示文稿，适合汇报与项目介绍。', tags: ['汇报演示', '内容提炼', 'PPT'], examples: ['制作三页项目介绍 PPT，包含目标、实施计划和预期成果。', '根据上传的资料，整理一份五页的项目汇报演示文稿。', '修改本项目生成的 PPT，把最后一页改为下一步行动。'] },
    document: { symbol: 'W', color: 'blue', summary: '整理报告、周报和会议纪要，生成可下载的 Word 文档。', tags: ['报告撰写', '内容整理', 'Word'], examples: ['帮我写一份项目周报，包含本周进展、风险和下周计划。', '根据上传的会议记录生成 Word 纪要，并列出负责人和截止日期。', '修改本项目生成的报告，在最后增加一节下一步计划。'] },
    code: { symbol: '</>', color: 'purple', summary: '从需求分析到源码文件，帮助你实现功能并检查运行结果。', tags: ['需求分析', '项目开发', '测试验证'], examples: ['帮我做一个读书会活动介绍网页，交付 HTML 和 CSS 文件。', '用 Python 写一个 CSV 汇总工具，并补充使用说明与测试。', '检查我上传的代码，列出问题和修改计划，再完成修复。'] },
    research: { symbol: '▤', color: 'teal', summary: '梳理资料、提炼重点，把零散信息整理成有结构的项目文件。', tags: ['资料整理', '信息提炼', '项目文件'], examples: ['整理我上传的资料，生成一份注明资料来源的 Markdown 报告。', '把会议记录整理成纪要和待办清单，标出负责人和截止日期。', '对比上传的两份方案，列出共同点、差异和待确认的问题。'] },
  }[category];
  if (row.kind === 'connector') return { symbol: '↗', color: 'teal', summary: String(row.config_json.description || '连接外部工具与资料，为对话提供已配置的服务能力。'), tags: ['外部工具', '连接服务'], examples: [`查看「${name}」提供的工具，说明可以帮我完成哪些任务。`, `使用「${name}」查询我指定的信息，并整理查询结果。`, `把「${name}」查询到的结果整理成项目中的 Markdown 文件。`] };
  if (!/Excel|表格|PowerPoint|PPT|演示|Word|文档|开发|代码|资料|项目文件/.test(name)) return { symbol: '✧', color: 'teal', summary: String(row.config_json.description || (row.kind === 'expert' ? '按已配置的角色和工作方法，帮助你完成指定任务。' : '为对话提供已安装的专门处理能力。')), tags: ['自定义能力'], examples: [`说明「${name}」能帮助我完成什么，以及需要我提供哪些资料。`, `我想使用「${name}」处理一个任务，请先帮我梳理目标和交付要求。`, `根据我提供的资料，按照「${name}」的工作方法制定处理计划。`] };
  return content;
}

export function CapabilityVisual({ name, kind, small = false }: { name: string; kind: string; small?: boolean }) {
  const info = capabilityInfo({ kind, config_json: { name } });
  return <span className={`cap-visual ${info.color} ${small ? 'small' : ''}`} aria-hidden="true">{kind === 'expert' ? <svg viewBox="0 0 64 64" fill="none"><rect width="64" height="64" rx="20" fill="currentColor" opacity=".12"/><path d="M9 64c1-16 10-22 23-22s22 6 23 22" fill="currentColor"/><path d="m24 44 8 11 8-11" fill="#fff"/><rect x="26" y="36" width="12" height="12" rx="5" fill="#e7ad87"/><ellipse cx="32" cy="27" rx="15" ry="18" fill="#f4c9a6"/><path d="M17 29C9 8 28 3 38 10c13-1 14 15 9 21l-3-13c-8 4-14 2-18-2l-8 13Z" fill="#34465d"/><path d="M23 27h6m6 0h6m-12 0h6" stroke="#34465d" strokeWidth="2"/><rect x="21" y="24" width="10" height="8" rx="3" stroke="#34465d" strokeWidth="1.5"/><rect x="33" y="24" width="10" height="8" rx="3" stroke="#34465d" strokeWidth="1.5"/><path d="M28 37q4 3 8 0" stroke="#b77e63" strokeWidth="1.5" strokeLinecap="round"/></svg> : <span>{info.symbol}</span>}</span>;
}
