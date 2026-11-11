import { useEffect, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Input, Space } from 'antd';
import { api } from './api/platform';
import { SessionControls } from './SessionControls';

export type Choices = { draft_id: string; upload_ids: string[]; seconds_limit: number; calls_limit: number; selection: { mode?: 'general' | 'expert' | 'team'; expert_id?: string; add_skills: string[]; remove_skills: string[]; add_connectors?: string[]; remove_connectors?: string[] } };
export const newChoices = (): Choices => ({ draft_id: crypto.randomUUID(), upload_ids: [], seconds_limit: 1800, calls_limit: 120, selection: { add_skills: [], remove_skills: [] } });
type Interaction = { id: string; digest: string; status: string; payload_json: { kind?: string; question?: string; [key: string]: unknown } };
type Asset = { id: string; kind: string; created_at: number; diff?: { added: string[]; removed: string[]; modified: string[] }; manifest_json: { path: string; size: number }[] };

const supportFile = (path: string) => path.startsWith('.agentbuddy/') || path.endsWith('.agentbuddy.json');
function AssetFiles({ asset, support = false }: { asset: Asset; support?: boolean }) {
  return <ul>{asset.manifest_json.filter(p => supportFile(p.path) === support).map(p => <li key={p.path}><a href={`${__API_PREFIX__}/files/${asset.id}/content?path=${encodeURIComponent(p.path)}`} target="_blank" rel="noreferrer">{p.path}</a>（{p.size} 字节）</li>)}</ul>;
}

export function RunPanel({ projectId, runId, choices, onChange, onBusy, active, children, actions }: { projectId?: string; runId?: string; choices: Choices; onChange: (value: Choices) => void; onBusy: (busy: boolean) => void; active: boolean; children: ReactNode; actions: ReactNode }) {
  const cache = useQueryClient();
  const [answerText, setAnswerText] = useState('');
  const control = useQuery({ queryKey: ['control', runId, active], enabled: !!runId, queryFn: () => api<{ calls_used: number; calls_limit: number; interactions: Interaction[] }>(`/runs/${runId}/control`), refetchInterval: active ? 1500 : false });
  const activity = useQuery({ queryKey: ['activity', runId, active], enabled: !!runId, queryFn: () => api<{ id: number; type: string; payload_json: { name?: string; status?: string; skill_id?: string; summary?: string; log_url?: string; kind?: string; required?: string[]; deleted?: string[] } }[]>(`/runs/${runId}/activity`), refetchInterval: active ? 2000 : false });
  const tasks = useQuery({ queryKey: ['tasks', runId, active], enabled: !!runId, queryFn: () => api<{ id: string; task_key: string; status: string; plan_json: { instruction: string; depends_on: string[] }; result_json?: { reason?: string; summary?: string } }[]>(`/runs/${runId}/tasks`), refetchInterval: active ? 1500 : false });
  const partial = useQuery({ queryKey: ['partial-files', runId, active], enabled: !!runId, queryFn: () => api<{ status: string; files: { path: string }[] }>(`/runs/${runId}/files`) });
  const files = useQuery({ queryKey: ['files', projectId, active], enabled: !!projectId, queryFn: () => api<Asset[]>(`/projects/${projectId}/files`), refetchInterval: active ? 3000 : false });
  const action = useMutation({ mutationFn: (operation: () => Promise<unknown>) => operation(), onSuccess: () => { void cache.invalidateQueries({ queryKey: ['control'] }); void cache.invalidateQueries({ queryKey: ['runs'] }); } });
  const [uploadBusy, setUploadBusy] = useState(false);
  useEffect(() => onBusy(action.isPending || uploadBusy), [action.isPending, uploadBusy, onBusy]);
  const delivery = activity.data?.find(event => event.type === 'delivery.planned')?.payload_json;
  return <div className="run-workspace">
    {action.error && <Alert type="error" title="操作未完成" description={action.error.message} />}
    {runId && <><p>已用调用：{control.data?.calls_used} / {control.data?.calls_limit}</p>{active && <Button danger onClick={() => action.mutate(() => api(`/runs/${runId}/cancel`, {}))}>取消本轮任务</Button>}</>}
    {delivery?.kind === 'files' && <p>本轮交付目标：{delivery.required?.join('、') || '无新增文件'}{delivery.deleted?.length ? `；删除：${delivery.deleted.join('、')}` : ''}</p>}
    {control.data?.interactions.filter(i => active && i.status === 'pending').map(i => <Card title="确认具体操作后执行" key={i.id}><pre>{JSON.stringify(i.payload_json, null, 2)}</pre>{i.payload_json.kind === 'clarification' && <Input.TextArea aria-label="补充需求" value={answerText} onChange={e => setAnswerText(e.target.value)} />}<Space>{[true, false].map(approved => <Button key={String(approved)} onClick={() => action.mutate(() => api(`/runs/${runId}/interactions/${i.id}`, { digest: i.digest, approved, text: i.payload_json.kind === 'clarification' ? answerText : undefined }))}>{approved ? '确认执行' : '拒绝执行'}</Button>)}</Space></Card>)}
    {!!tasks.data?.length && <Card title="协作子任务">{tasks.data.map(task => <details key={task.id}><summary>{task.task_key} · {task.status}</summary><p>{task.plan_json.instruction}</p><p>依赖：{task.plan_json.depends_on.join(', ') || '无'}</p><pre>{task.result_json?.reason ?? task.result_json?.summary}</pre></details>)}</Card>}
    {activity.data && <details><summary>本轮执行记录</summary><ul>{activity.data.map(event => <li key={event.id}>{event.type} {event.payload_json.name ?? event.payload_json.status ?? event.payload_json.skill_id}{event.payload_json.summary && <pre>{event.payload_json.summary}</pre>}{event.payload_json.log_url && <a href={`${__API_PREFIX__}${event.payload_json.log_url}`}>完整日志</a>}</li>)}</ul></details>}
    {partial.data && ['failed', 'interrupted'].includes(partial.data.status) && partial.data.files.length > 0 && <Card title="本轮部分产物（未作为成功版本）"><a href={`${__API_PREFIX__}/runs/${runId}/files/download`}>下载部分产物</a><ul>{partial.data.files.map(f => <li key={f.path}>{f.path}</li>)}</ul></Card>}
    {files.data?.map(f => <Card key={f.id} size="small" title={f.kind === 'version' ? `成功版本 · ${new Date(f.created_at * 1000).toLocaleString()}` : '原始输入'}><a href={`${__API_PREFIX__}/files/${f.id}/download`}>下载 ZIP</a>{f.kind === 'version' && <p>自动检查通过；请打开预览，核对来源、日期、金额及跨文件内容后再使用。</p>}{f.diff && <p>新增 {f.diff.added.filter(path => !supportFile(path)).length} · 修改 {f.diff.modified.filter(path => !supportFile(path)).length} · 删除 {f.diff.removed.filter(path => !supportFile(path)).length} <a href={`${__API_PREFIX__}/files/${f.id}/validation`} target="_blank" rel="noreferrer">查看验证报告</a></p>}<AssetFiles asset={f} />{f.manifest_json.some(p => supportFile(p.path)) && <details><summary>预览和验证文件</summary><AssetFiles asset={f} support /></details>}</Card>)}
    <SessionControls choices={choices} onChange={onChange} onBusy={setUploadBusy} actions={actions} active={active}>{children}</SessionControls>
  </div>;
}
