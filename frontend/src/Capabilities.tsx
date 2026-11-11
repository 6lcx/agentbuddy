import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Drawer, Input, Select, Space, Switch, Tabs, Tag } from 'antd';
import { useNavigate } from 'react-router-dom';
import { api, queued, type Model } from './api/platform';
import { CapabilityCatalog } from './CapabilityCatalog';

export type Capability = { id: string; kind: string; revision: number; config_json: { name: string; enabled?: boolean; prompt?: string; model_id?: string; skill_ids?: string[]; connector_ids?: string[]; collaborator_ids?: string[]; [key: string]: unknown } };
export async function encodeFile(file: File) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}

export function Capabilities() {
  const cache = useQueryClient();
  const navigate = useNavigate();
  function useCapability(row: Capability, prompt?: string) {
    let draft: { selection?: Record<string, unknown>; [key: string]: unknown } = {};
    try { draft = JSON.parse(localStorage.getItem('agentbuddy-draft-new-choices') ?? '{}'); } catch { /* start fresh */ }
    const selection: Record<string, unknown> = { add_skills: [], remove_skills: [], ...draft.selection };
    const next = row.kind === 'expert' ? { ...selection, mode: 'expert', expert_id: row.id } : row.kind === 'skill' ? { ...selection, add_skills: [...new Set([...(selection.add_skills as string[]), row.id])], remove_skills: (selection.remove_skills as string[]).filter(id => id !== row.id) } : { ...selection, add_connectors: [...new Set([...((selection.add_connectors as string[] | undefined) ?? []), row.id])], remove_connectors: ((selection.remove_connectors as string[] | undefined) ?? []).filter(id => id !== row.id) };
    localStorage.setItem('agentbuddy-draft-new-choices', JSON.stringify({ draft_id: crypto.randomUUID(), upload_ids: [], seconds_limit: 1800, calls_limit: 120, ...draft, selection: next }));
    if (prompt) localStorage.setItem('agentbuddy-draft-new', prompt);
    navigate('/chat');
  }
  const [kind, setKind] = useState('expert');
  const [managing, setManaging] = useState(false);
  const [probeId, setProbeId] = useState<string>();
  const probe = useQuery({ queryKey: ['connector-probe', probeId], enabled: !!probeId, queryFn: async () => { const response = await fetch(`${__API_PREFIX__}/operations/${probeId}`); const value = await response.json(); if (value.status === 'succeeded') void cache.invalidateQueries({ queryKey: ['capabilities'] }); return value as { status: string; error_json?: { message: string } }; }, refetchInterval: q => ['succeeded', 'failed'].includes(q.state.data?.status ?? '') ? false : 1000 });
  const [versions, setVersions] = useState<{ row: Capability; packages: { package_id: string; name: string; version?: string }[] }>();
  const [editing, setEditing] = useState<Capability>();
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [model, setModel] = useState<string>();
  const [skills, setSkills] = useState<string[]>([]);
  const [collaborators, setCollaborators] = useState<string[]>([]);
  const [connectors, setConnectors] = useState<string[]>([]);
  const [config, setConfig] = useState('{"transport":"http","url":"http://localhost:8080/mcp","effects":{}}');
  const rows = useQuery({ queryKey: ['capabilities', kind], queryFn: () => api<Capability[]>(`/capabilities/${kind}`) });
  const allSkills = useQuery({ queryKey: ['capabilities', 'skill'], queryFn: () => api<Capability[]>('/capabilities/skill') });
  const allConnectors = useQuery({ queryKey: ['capabilities', 'connector'], queryFn: () => api<Capability[]>('/capabilities/connector') });
  const models = useQuery({ queryKey: ['models'], queryFn: () => api<Model[]>('/models') });
  const refresh = () => void cache.invalidateQueries({ queryKey: ['capabilities'] });
  const action = useMutation({ mutationFn: async (operation: () => Promise<unknown>) => operation(), onSuccess: refresh });
  function edit(row: Capability) { setEditing(row); setName(row.config_json.name); setPrompt(row.config_json.prompt ?? ''); setModel(row.config_json.model_id); setSkills(row.config_json.skill_ids ?? []); setConnectors(row.config_json.connector_ids ?? []); setCollaborators(row.config_json.collaborator_ids ?? []); setConfig(JSON.stringify(row.config_json, null, 2)); }
  return <><CapabilityCatalog kind={kind} onKind={value => { setKind(value); setEditing(undefined); setName(''); }} rows={rows.data ?? []} loading={rows.isPending} onManage={() => setManaging(true)} onUse={useCapability} />{rows.error && <Alert type="error" title="能力目录暂时无法加载" description={rows.error.message} />}<Drawer title="管理能力" open={managing} onClose={() => setManaging(false)} size="large" className="management-drawer"><h2>能力配置</h2><Tabs activeKey={kind} onChange={value => { setKind(value); setEditing(undefined); setName(''); }} items={[{ key: 'expert', label: '专家' }, { key: 'skill', label: 'Skills' }, { key: 'connector', label: '连接器' }]} />
    {probeId && <p>连接测试：{probe.data?.status ?? 'queued'} {probe.data?.error_json?.message}</p>}
    {(action.error || rows.error) && <Alert type="error" title="操作失败" description={(action.error || rows.error)?.message} />}
    {kind === 'connector' && <Card title="导入进程型 MCP 包"><p>ZIP 根目录包含 mcp.json（name、entry、args、requires、effects）和 Python 入口；导入不会启动进程。</p><input aria-label="导入 MCP" type="file" accept=".zip" onChange={e => { const file = e.target.files?.[0]; if (file) action.mutate(async () => queued('/connectors/import', { name: file.name, content_base64: await encodeFile(file), draft_id: 'mcp-import' })); }} /></Card>}
    {kind === 'skill' ? <Card title="导入 Skill ZIP"><p>根目录需要 SKILL.md 和 skill.json（name、description、scripts、requires）。安装不会执行脚本；启用后供对话选择。</p><input aria-label="导入 Skill" type="file" accept=".zip" onChange={e => { const file = e.target.files?.[0]; if (file) action.mutate(async () => queued('/skills/import', { name: file.name, content_base64: await encodeFile(file), draft_id: 'skill-import' })); }} /></Card> : <Card title={editing ? '编辑配置' : '新建配置'}>
      <Input aria-label="能力名称" placeholder="名称" value={name} onChange={e => setName(e.target.value)} />
      {kind === 'expert' ? <><Input.TextArea aria-label="专家指令" placeholder="角色与工作方法" value={prompt} onChange={e => setPrompt(e.target.value)} />
        <Select aria-label="专家模型" placeholder="模型" value={model} onChange={setModel} options={models.data?.filter(m => m.available).map(m => ({ value: m.id, label: m.model_id }))} style={{ width: '100%' }} />
        <Select aria-label="专家 Skills" mode="multiple" placeholder="默认 Skills" value={skills} onChange={setSkills} options={allSkills.data?.filter(s => s.config_json.enabled).map(s => ({ value: s.id, label: s.config_json.name }))} style={{ width: '100%' }} />
        <Select aria-label="允许协作专家" mode="multiple" placeholder="允许调用的协作专家（单层）" value={collaborators} onChange={setCollaborators} options={rows.data?.filter(r => r.id !== editing?.id).map(r => ({ value: r.id, label: r.config_json.name }))} style={{ width: '100%' }} />
        <Select aria-label="专家连接器" mode="multiple" placeholder="连接器" value={connectors} onChange={setConnectors} options={allConnectors.data?.map(s => ({ value: s.id, label: s.config_json.name }))} style={{ width: '100%' }} /></> : <><p>HTTP：transport、url；进程：transport=stdio、command、args、env。effects 中仅明确标为 read 的工具直接执行，其余确认一次。</p><Input.TextArea aria-label="连接器配置" rows={7} value={config} onChange={e => setConfig(e.target.value)} /></>}
      <Button loading={action.isPending} onClick={() => action.mutate(async () => api(`/capabilities/${kind}`, { id: editing?.id, revision: editing?.revision, config: kind === 'expert' ? { ...editing?.config_json, name, prompt, model_id: model, skill_ids: skills, connector_ids: connectors, collaborator_ids: collaborators, enabled: true } : { ...JSON.parse(config), name, enabled: true } }))}>保存配置</Button>
    </Card>}
    {versions && <Card title={`${versions.row.config_json.name} · 历史包版本`}><Select aria-label="Skill 版本" style={{ width: '100%' }} placeholder="选择版本" options={versions.packages.map(v => ({ value: v.package_id, label: `${v.version ?? '未标版本号'} · ${v.package_id}` }))} onChange={package_id => action.mutate(async () => { await api(`/skills/${versions.row.id}/select-version`, { package_id, revision: versions.row.revision }); setVersions(undefined); })} /></Card>}
    {rows.data?.map(row => <Card key={row.id} title={<>{row.config_json.name} <Tag>修订 {row.revision}</Tag></>}><Space><Button disabled={!row.config_json.enabled} onClick={() => useCapability(row)}>{kind === 'expert' ? '使用专家' : '用于新对话'}</Button><Switch checked={!!row.config_json.enabled} onChange={enabled => action.mutate(() => api(`/capabilities/${kind}`, { id: row.id, revision: row.revision, config: { ...row.config_json, enabled } }))} />{kind === 'skill' && <><Button onClick={() => action.mutate(async () => { const packages = await api<{ package_id: string; name: string; version?: string }[]>(`/skills/${row.id}/versions`); setVersions({ row, packages }); })}>历史版本</Button><label>上传新版本<input aria-label={`更新 ${row.config_json.name}`} type="file" accept=".zip" onChange={e => { const file = e.target.files?.[0]; if (file) action.mutate(async () => queued('/skills/import', { name: file.name, skill_id: row.id, draft_id: 'skill-update', content_base64: await encodeFile(file) })); }} /></label></>}{kind !== 'skill' && <Button onClick={() => edit(row)}>编辑</Button>}{kind === 'connector' && <Button loading={action.isPending} onClick={() => action.mutate(async () => { const result = await api<{ id: string }>(`/connectors/${row.id}/test`, {}); setProbeId(result.id); })}>测试并发现工具</Button>}</Space></Card>)}
  </Drawer></>;
}
