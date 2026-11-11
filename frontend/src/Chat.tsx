import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Drawer, Input, Select, Typography } from 'antd';
import { useNavigate, useParams } from 'react-router-dom';
import { api, eventUrl, type Message, type Model, type Run } from './api/platform';

import { RunPanel, newChoices, type Choices } from './RunPanel';
const ModelSettings = lazy(() => import('./ModelSettings').then(m => ({ default: m.ModelSettings }))); 

export function Chat() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const draftKey = `agentbuddy-draft-${projectId ?? 'new'}`;
  const [draft, setDraft] = useState('');
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [capabilityBusy, setCapabilityBusy] = useState(false);
  const [choices, setChoices] = useState<Choices>(newChoices);
  const savingSelection = useRef(Promise.resolve());
  const [selectionError, setSelectionError] = useState('');
  function changeChoices(value: Choices) {
    setChoices(value); localStorage.setItem(`${draftKey}-choices`, JSON.stringify(value));
    if (projectId) savingSelection.current = savingSelection.current.catch(() => {}).then(async () => { await api(`/projects/${projectId}/selection`, { selection: value.selection }, 'PATCH'); setSelectionError(''); }).catch(error => setSelectionError(error.message));
  }
  useEffect(() => {
    let active = true;
    const stored = localStorage.getItem(`${draftKey}-choices`);
    try { setChoices(JSON.parse(stored ?? 'null') ?? newChoices()); } catch { setChoices(newChoices()); }
    if (!stored && projectId) void api<{ selection: Partial<Choices['selection']> }>(`/projects/${projectId}/selection`).then(result => { if (active) setChoices({ ...newChoices(), selection: { add_skills: [], remove_skills: [], ...result.selection } }); }).catch(error => { if (active) setSelectionError(error.message); });
    return () => { active = false; };
  }, [draftKey, projectId]);
  const [model, setModel] = useState<string>();
  const [stream, setStream] = useState('');
  const [connection, setConnection] = useState('');
  useEffect(() => { setDraft(localStorage.getItem(draftKey) ?? ''); setStream(''); }, [draftKey]);
  const models = useQuery({ queryKey: ['models'], queryFn: () => api<Model[]>('/models') });
  const office = useQuery({ queryKey: ['office-dependencies'], queryFn: () => api<{ renderer_available: boolean }>('/office/dependencies') });
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => api<{ default_model_id: string | null }>('/settings') });
  const messages = useQuery({ queryKey: ['messages', projectId], enabled: !!projectId, queryFn: () => api<Message[]>(`/projects/${projectId}/messages`) });
  const runs = useQuery({ queryKey: ['runs', projectId], enabled: !!projectId, queryFn: () => api<Run[]>(`/projects/${projectId}/runs`), refetchInterval: q => q.state.data?.some(r => ['queued', 'running', 'waiting_input'].includes(r.status)) ? 2000 : false });
  const latest = runs.data?.[0];
  const activeId = latest && ['queued', 'running', 'waiting_input'].includes(latest.status) ? latest.id : null;
  useEffect(() => {
    setStream('');
    if (!activeId) { setConnection(''); return; }
    let cursor = 0;
    const source = new EventSource(eventUrl(activeId));
    source.onopen = () => setConnection('实时连接已建立');
    source.onerror = () => setConnection('连接中断，正在重新连接；任务继续执行');
    const receive = (event: MessageEvent) => {
      const data = JSON.parse(event.data);
      if (data.id <= cursor) return;
      cursor = data.id;
      if (data.type === 'message.delta') setStream(old => old + data.payload.text);
      if (data.type === 'message.completed') setStream(data.payload.text);
      if (data.type === 'run.completed') {
        source.close();
        void cache.invalidateQueries({ queryKey: ['messages', projectId] });
        void cache.invalidateQueries({ queryKey: ['runs', projectId] });
      }
    };
    for (const event of ['run.status', 'message.delta', 'message.completed', 'run.completed']) source.addEventListener(event, receive as EventListener);
    return () => source.close();
  }, [activeId, projectId, cache]);
  useEffect(() => {
    if (latest && ['succeeded', 'failed', 'interrupted'].includes(latest.status)) {
      void cache.invalidateQueries({ queryKey: ['messages', projectId] });
    }
  }, [latest?.id, latest?.status, projectId, cache]);
  const send = useMutation({ mutationFn: async () => {
    const payload = { ...choices, project_id: projectId ?? null, model_id: model ?? null, message: draft };
    const body = JSON.stringify(payload);
    const pendingKey = `agentbuddy-pending-${draftKey}`;
    let pending: { body: string; key: string } | null = null;
    try { pending = JSON.parse(sessionStorage.getItem(pendingKey) ?? 'null'); } catch { /* replace invalid draft metadata */ }
    if (pending?.body !== body) { pending = { body, key: crypto.randomUUID() }; sessionStorage.setItem(pendingKey, JSON.stringify(pending)); }
    const result = await api<{ project_id: string; run_id: string }>('/runs', payload, 'POST', { 'Idempotency-Key': pending!.key });
    sessionStorage.removeItem(pendingKey);
    return result;
  }, onSuccess: result => {
    setDraft(''); localStorage.removeItem(draftKey);
    const next = { ...choices, upload_ids: [] }; localStorage.setItem(`agentbuddy-draft-${result.project_id}-choices`, JSON.stringify(next)); setChoices(next);
    if (!projectId) localStorage.setItem(`${draftKey}-choices`, JSON.stringify({ ...next, draft_id: crypto.randomUUID() }));
    void cache.invalidateQueries({ queryKey: ['projects'] });
    void cache.invalidateQueries({ queryKey: ['messages', result.project_id] });
    void cache.invalidateQueries({ queryKey: ['runs', result.project_id] });
    navigate(`/projects/${result.project_id}`);
  } });
  const available = models.data?.filter(m => m.available) ?? [];
  const error = send.error || models.error || messages.error || runs.error || settings.error;
  const examples = [
    { icon: '▤', name: '写一份项目周报', type: '文档创作', prompt: '生成一份 Word 项目周报', color: 'blue' },
    { icon: '▥', name: '把数据变成洞察', type: '数据分析', prompt: '生成包含预算求和公式的 Excel 表格', color: 'green' },
    { icon: '▧', name: '做一份清晰的演示', type: '演示制作', prompt: '生成三页中文项目介绍 PPT', color: 'orange' },
    { icon: '⌘', name: '整理资料与项目文件', type: '开发与研究', prompt: '整理上传资料并生成带来源的 Markdown 报告', color: 'purple' },
  ];
  return <div className={`chat-page ${projectId ? 'has-conversation' : 'new-conversation'}`}>
    <div className="chat-topbar"><span>{projectId ? '项目对话' : '新对话'}</span><span className="workspace-pill"><i />个人工作空间</span></div>
    <section className="chat-content">
    {!projectId && <div className="welcome"><h1>今天，想做些什么？</h1><p>说出你的想法，让合适的专家来帮忙。</p></div>}
    {!models.isPending && !available.length && <Alert type="info" title="请先配置可用模型" description={<Button type="link" onClick={() => setModelSettingsOpen(true)}>设置模型连接</Button>} />}
    {office.data && !office.data.renderer_available && <Alert type="warning" title="办公渲染依赖尚未准备" description="在后端安装 LibreOffice 并设置 SOFFICE_PATH；普通文本任务仍可使用。" />}
    {selectionError && <Alert type="error" title="会话能力保存失败" description={selectionError} />}
    {error && <Alert type="error" title="操作未完成，输入已保留" description={error.message} />}
    <div className="messages">{messages.data?.map(m => <Card className={`chat-message ${m.role}`} key={m.id} size="small" title={m.role === 'user' ? '你' : '助手'}><div className="message-text">{m.content}</div></Card>)}
      {activeId && <Card title={latest?.status === 'queued' ? '等待 Worker 执行' : '助手正在回答'}><div className="message-text" aria-live="polite">{stream || latest?.partial_text || '请稍候…'}</div><Typography.Text type="secondary">{connection}</Typography.Text></Card>}
      {latest?.failure_json && <Alert type="error" title="本轮未完成" description={<><p>{latest.failure_json.message}</p><p>再次执行将从最后成功版本开始，不覆盖旧版本。先核对外部操作是否已经完成，再补充继续要求；失败轮次的附件请重新选择。</p><Button disabled={!!activeId} onClick={() => { const previous = [...(messages.data ?? [])].reverse().find(m => m.role === 'user'); const text = `请从最后成功版本继续完成以下任务。先核对现有文件和外部操作结果，避免重复执行。\n${previous?.content ?? ''}`; setDraft(text); localStorage.setItem(draftKey, text); }}>填入继续执行草稿</Button>{latest.partial_text && <pre>{latest.partial_text}</pre>}</>} />}
    </div>
    {!projectId && <div className="task-shortcuts" aria-label="常用任务">{[
      ['写文档', '生成一份 Word 项目周报'], ['分析数据', '分析我上传的数据，整理重点并生成结果表格。'],
      ['做演示', '生成三页中文项目介绍 PPT'], ['写代码', '帮我做一个读书会活动介绍网页，交付 HTML 和 CSS 文件。'],
    ].map(([label, prompt]) => <button key={label} onClick={() => { setDraft(prompt); localStorage.setItem(draftKey, prompt); }}>{label}</button>)}</div>}
    <RunPanel projectId={projectId} runId={latest?.id} active={!!activeId} choices={choices} onChange={changeChoices} onBusy={setCapabilityBusy} actions={<>
      <Select variant="borderless" aria-label="对话模型" placeholder="选择模型" className="model-picker" value={model ?? settings.data?.default_model_id ?? undefined} options={available.map(m => ({ value: m.id, label: m.model_id }))} onChange={setModel} popupRender={menu => <>{menu}<div className="model-menu-footer"><Button type="text" onClick={() => setModelSettingsOpen(true)}>⚙ 模型连接与设置</Button></div></>} />
      <Button type="text" className="model-settings-button" aria-label="模型设置" title="模型连接与设置" onClick={() => setModelSettingsOpen(true)}>⚙</Button>
      <Button className="send-button" aria-label="发送" type="primary" loading={send.isPending} disabled={capabilityBusy || !!activeId || !draft.trim() || !available.length} onClick={() => send.mutate()} title="发送消息">↑</Button>
    </>}>
      <Input.TextArea aria-label="消息" value={draft} onChange={event => { setDraft(event.target.value); localStorage.setItem(draftKey, event.target.value); }} autoSize={{ minRows: 3, maxRows: 9 }} maxLength={32000} placeholder="描述你的任务，或上传文件一起处理…" />
    </RunPanel>
    {!projectId && <div className="inspiration"><h2>也可以从这些任务开始</h2><div className="example-list">{examples.map(item => <button key={item.name} onClick={() => { setDraft(item.prompt); localStorage.setItem(draftKey, item.prompt); }}><span>{item.name}</span><span className="example-type">{item.type}</span></button>)}</div></div>}
    </section>
    <Drawer title="模型连接与设置" open={modelSettingsOpen} onClose={() => setModelSettingsOpen(false)} size="large" className="management-drawer" destroyOnHidden><Suspense fallback={<p>正在加载模型设置…</p>}><ModelSettings /></Suspense></Drawer>
  </div>;
}
