import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Alert, Button, Checkbox, Input, InputNumber, Modal, Tabs, Tag } from 'antd';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, queued, type Project } from './api/platform';
import { encodeFile, type Capability } from './Capabilities';
import type { Choices } from './RunPanel';
import { effectiveIds, toggleCapability } from './selection';
import { CapabilityVisual } from './CapabilityVisual';

export function SessionControls({ choices, onChange, onBusy, children, actions, active }: { choices: Choices; onChange: (v: Choices) => void; onBusy: (v: boolean) => void; children: ReactNode; actions: ReactNode; active: boolean }) {
  const [panel, setPanel] = useState<'expert' | 'capabilities' | 'advanced' | 'project' | null>(null);
  const [search, setSearch] = useState('');
  const { projectId } = useParams();
  const navigate = useNavigate();
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api<Project[]>('/projects') });
  const [names, setNames] = useState<Record<string, string>>({});
  const uploadInput = useRef<HTMLInputElement>(null);
  const latestChoices = useRef(choices);
  latestChoices.current = choices;
  const experts = useQuery({ queryKey: ['capabilities', 'expert'], queryFn: () => api<Capability[]>('/capabilities/expert') });
  const skills = useQuery({ queryKey: ['capabilities', 'skill'], queryFn: () => api<Capability[]>('/capabilities/skill') });
  const connectors = useQuery({ queryKey: ['capabilities', 'connector'], queryFn: () => api<Capability[]>('/capabilities/connector') });
  const selected = choices.selection;
  const mode = selected.mode ?? (selected.expert_id ? 'expert' : 'general');
  const expert = mode === 'expert' ? experts.data?.find(e => e.id === selected.expert_id) : undefined;
  const label = mode === 'team' ? '自动协作' : expert?.config_json.name ?? '通用助手';
  const skillIds = effectiveIds(expert ? expert.config_json.skill_ids ?? [] : (skills.data ?? []).filter(s => s.config_json.enabled).map(s => s.id), selected.add_skills, selected.remove_skills);
  const connectorIds = effectiveIds(expert?.config_json.connector_ids ?? [], selected.add_connectors, selected.remove_connectors);
  const chosen = [...(skills.data ?? []).filter(s => skillIds.has(s.id)), ...(connectors.data ?? []).filter(s => connectorIds.has(s.id))];
  const upload = useMutation({ mutationFn: async (file: File) => {
    const result = await queued<{ id: string }>('/uploads', { name: file.name, draft_id: choices.draft_id, content_base64: await encodeFile(file) });
    setNames(old => ({ ...old, [result.id]: file.name }));
    if (latestChoices.current.draft_id === choices.draft_id) {
      onChange({ ...latestChoices.current, upload_ids: [...latestChoices.current.upload_ids, result.id] });
    }
  } });
  useEffect(() => onBusy(upload.isPending), [upload.isPending, onBusy]);
  function toggle(row: Capability, enabled: boolean) {
    onChange({ ...choices, selection: toggleCapability(selected, row.kind === 'skill' ? 'skills' : 'connectors', row.id, enabled) });
  }
  function choose(mode: 'general' | 'expert' | 'team', id?: string) {
    onChange({ ...choices, selection: { ...selected, mode, expert_id: id } });
    setPanel(null);
  }
  const error = upload.error || experts.error || skills.error || connectors.error;
  return <>
    {error && <Alert type="error" title="能力或附件未加载完成" description={error.message} />}
    <div className="composer">
      <div className="composer-tags" aria-label="当前启用能力">
        {chosen.slice(0, 3).map(row => <Tag key={row.id} closable onClose={() => toggle(row, false)}><CapabilityVisual kind={row.kind} name={row.config_json.name} small /> {row.config_json.name}</Tag>)}
        {chosen.length > 3 && <button className="more-chip" onClick={() => setPanel('capabilities')}>+{chosen.length - 3} 项能力</button>}
        {!chosen.length && <span className="quiet">未添加额外能力，可直接开始对话</span>}
      </div>
      {children}
      {!!choices.upload_ids.length && <div className="attachment-list" aria-label="待发送附件">{choices.upload_ids.map((id, i) => <Tag key={id} closable onClose={() => onChange({ ...choices, upload_ids: choices.upload_ids.filter(key => key !== id) })}>▧ {names[id] ?? `附件 ${i + 1}`}</Tag>)}</div>}
      <div className="composer-toolbar">
        <div className="composer-tools">
          <input ref={uploadInput} className="file-input" aria-label="上传项目资料" type="file" disabled={upload.isPending} onChange={e => { const file = e.target.files?.[0]; if (file) upload.mutate(file); e.target.value = ''; }} />
          <Button type="text" className="attach-button" aria-label="添加附件" title="上传文件或 ZIP" loading={upload.isPending} onClick={() => uploadInput.current?.click()}>＋</Button>
          <Button type="text" aria-label="选择专家" onClick={() => { setSearch(''); setPanel('expert'); }}><CapabilityVisual kind="expert" name={label} small /> {label} <span className="chevron">⌄</span></Button>
          <Button type="text" aria-label="选择能力" onClick={() => { setSearch(''); setPanel('capabilities'); }}>✧ 能力 <span className="count-badge">{chosen.length}</span></Button>
          <Button type="text" aria-label="高级设置" title="任务预算" onClick={() => setPanel('advanced')}>···</Button>
        </div>
        <div className="composer-actions">{actions}</div>
      </div>
    </div>
    <div className="project-context"><Button type="text" aria-label="选择工作空间" onClick={() => { setSearch(''); setPanel('project'); }}>▱ <span>{projectId ? projects.data?.find(p => p.id === projectId)?.title ?? '当前项目' : '新项目工作空间'}</span>⌄</Button><span>资料与交付文件保存在项目中</span></div>
    <Modal title="项目工作空间" open={panel === 'project'} onCancel={() => setPanel(null)} footer={null} width={600}><p className="panel-description">选择已有项目继续对话，沿用该项目的资料和文件版本。新任务首次发送后创建项目。</p><Input.Search aria-label="搜索项目" placeholder="搜索已有项目" value={search} onChange={e => setSearch(e.target.value)} /><div className="project-options"><button onClick={() => { navigate('/chat'); setPanel(null); }}>＋ 新项目<span>回到新任务草稿</span></button>{projects.data?.filter(p => p.title.includes(search)).map(p => <button key={p.id} onClick={() => { navigate(`/projects/${p.id}`); setPanel(null); }}><span>{p.title}</span>{p.id === projectId && <small>当前项目</small>}</button>)}{projects.isError && <Alert type="error" title="项目列表加载失败" />}</div></Modal>
    <p className={`composer-hint ${!active && mode !== 'team' ? 'idle-hint' : ''}`}>{active ? '任务正在执行，能力调整将在下一轮生效。' : '描述你想完成的事，或添加文件一起处理。'}{mode === 'team' && ' 已开启多专家协作。'}</p>
    <Modal title="让谁来帮你？" open={panel === 'expert'} footer={null} onCancel={() => setPanel(null)} width={640}>
      <p className="panel-description">直接提问，或选择擅长这项工作的专家。</p>
      <Input.Search aria-label="搜索专家" placeholder="搜索专家" value={search} onChange={e => setSearch(e.target.value)} />
      <div className="expert-options">
        {!search && <><button className={`expert-option ${mode === 'general' ? 'selected' : ''}`} onClick={() => choose('general')}><span className="expert-avatar">✦</span><span><strong>通用助手</strong><small>日常问答、资料整理与文件处理</small></span>{mode === 'general' && <span className="selection-check">✓</span>}</button><button className={`expert-option ${mode === 'team' ? 'selected' : ''}`} onClick={() => choose('team')}><span className="expert-avatar lavender">♧</span><span><strong>自动协作</strong><small>由主专家组织团队，分工完成复杂任务</small></span>{mode === 'team' && <span className="selection-check">✓</span>}</button></>}
        {experts.data?.filter(e => e.config_json.enabled !== false && e.config_json.name.includes(search)).map(e => <button key={e.id} className={`expert-option ${expert?.id === e.id ? 'selected' : ''}`} onClick={() => choose('expert', e.id)}><CapabilityVisual kind="expert" name={e.config_json.name} /><span><strong>{e.config_json.name}</strong><small>{String(e.config_json.description ?? e.config_json.prompt ?? '使用这个专家的默认技能与连接器')}</small></span>{expert?.id === e.id && <span className="selection-check">✓</span>}</button>)}
      </div>
      <Link className="panel-link" to="/capabilities">查看全部专家与能力 ↗</Link>
    </Modal>
    <Modal title="本次对话的能力" open={panel === 'capabilities'} onCancel={() => setPanel(null)} width={600} footer={<Button type="primary" onClick={() => setPanel(null)}>完成</Button>}>
      <p className="panel-description">勾选即可使用。只影响当前对话，不修改专家模板。</p>
      <Input.Search aria-label="搜索能力" placeholder="搜索技能或连接器" value={search} onChange={e => setSearch(e.target.value)} />
      <Tabs items={(['skill', 'connector'] as const).map(kind => ({ key: kind, label: kind === 'skill' ? `技能 · ${skillIds.size}` : `连接器 · ${connectorIds.size}`, children: <div className="capability-options">{(kind === 'skill' ? skills.data : connectors.data)?.filter(row => row.config_json.name.includes(search)).map(row => <label className="capability-option" key={row.id}><CapabilityVisual kind={kind} name={row.config_json.name} /><span className="capability-copy"><strong>{row.config_json.name}</strong><small>{row.config_json.enabled === false ? '尚未启用，请前往能力目录配置' : String(row.config_json.description ?? (kind === 'skill' ? '为助手提供专门的处理能力' : '连接外部工具与资料'))}</small></span><Checkbox aria-label={row.config_json.name} checked={(kind === 'skill' ? skillIds : connectorIds).has(row.id)} disabled={kind === 'skill' && !row.config_json.enabled} onChange={e => toggle(row, e.target.checked)} /></label>)}{!(kind === 'skill' ? skills.data : connectors.data)?.length && <p className="empty-state">还没有可用{kind === 'skill' ? '技能' : '连接器'}，先到能力目录添加。</p>}</div> }))} />
      <div className="panel-footer"><Link to="/capabilities">管理能力 ↗</Link><Button type="text" onClick={() => onChange({ ...choices, selection: { ...selected, add_skills: [], remove_skills: [], add_connectors: [], remove_connectors: [] } })}>恢复专家默认</Button></div>
    </Modal>
    <Modal title="任务高级设置" open={panel === 'advanced'} onCancel={() => setPanel(null)} footer={<Button type="primary" onClick={() => setPanel(null)}>完成</Button>}>
      <p className="panel-description">通常无需调整。达到任一上限后停止任务，保留已生成的文件。</p>
      <div className="budget-fields"><label>执行时长上限（秒）<InputNumber aria-label="执行时长上限" min={1} max={7200} value={choices.seconds_limit} onChange={n => onChange({ ...choices, seconds_limit: n ?? 1800 })} /></label><label>模型与工具调用次数<InputNumber aria-label="调用次数上限" min={1} max={500} value={choices.calls_limit} onChange={n => onChange({ ...choices, calls_limit: n ?? 120 })} /></label></div>
      <div className="budget-fields"><label>Agent 最大决策轮数<InputNumber aria-label="Agent 最大决策轮数" min={1} max={100} value={choices.agent_policy?.max_iters ?? 50} onChange={n => onChange({ ...choices, agent_policy: { ...choices.agent_policy, max_iters: n ?? 50 } })} /></label><label>上下文压缩触发比例<InputNumber aria-label="压缩触发比例" min={0.65} max={0.9} step={0.05} value={choices.context_policy?.trigger_ratio ?? 0.8} onChange={n => onChange({ ...choices, context_policy: { ...choices.context_policy, trigger_ratio: n ?? 0.8 } })} /></label></div>
      <Checkbox checked={choices.agent_policy?.allow_serial_dependency_exception ?? false} onChange={e => onChange({ ...choices, agent_policy: { ...choices.agent_policy, allow_serial_dependency_exception: e.target.checked } })}>允许具名的串行依赖派发例外</Checkbox>
    </Modal>
  </>;
}
