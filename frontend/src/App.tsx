import { lazy, Suspense } from 'react';
import { Alert, Button, Card, Space, Tag, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import { getHealth } from './api/client';
import { api, type Project } from './api/platform';
import { SystemStatus } from './SystemStatus';
const ModelSettings = lazy(() => import('./ModelSettings').then(module => ({ default: module.ModelSettings })));
const Capabilities = lazy(() => import('./Capabilities').then(module => ({ default: module.Capabilities })));
const Chat = lazy(() => import('./Chat').then(module => ({ default: module.Chat })));

function Status() {
  const health = useQuery({ queryKey: ['health'], queryFn: ({ signal }) => getHealth(signal) });
  return <Card title="服务连接">
    {health.isPending && <p role="status">正在连接后端…</p>}
    {health.isError && <Alert type="error" showIcon title="后端暂不可用" description={health.error.message} />}
    {health.isSuccess && <Space><Tag color="success">后端已连接</Tag><span>{health.data.service}</span></Space>}
    <div className="actions"><Button onClick={() => void health.refetch()} loading={health.isFetching}>重新检查</Button></div>
    <Typography.Paragraph type="secondary">连接检查仅代表 API 可访问。可使用通用助手、指定专家或自动协作完成任务；执行结果与文件版本请在对话中查看。</Typography.Paragraph>
    <SystemStatus />
  </Card>;
}

export function App() {
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => api<Project[]>('/projects') });
  const location = useLocation();
  const chat = location.pathname === '/' || location.pathname === '/chat' || location.pathname.startsWith('/projects/');
  return <div className="app-shell">
    <aside className="app-sidebar">
      <Link className="brand" to="/chat"><span className="brand-mark">✦</span><span>AgentBuddy</span></Link>
      <Link className="new-chat-button" to="/chat"><span>＋</span> 新建对话</Link>
      <nav className="primary-nav"><NavLink aria-label="对话" to="/chat" className={chat ? 'active' : ''}><span>▤</span>对话</NavLink><NavLink aria-label="专家与技能" to="/capabilities"><span>♧</span>专家·技能·连接器</NavLink></nav>
      <div className="history-heading">最近对话<span>{projects.data?.length ?? 0}</span></div>
      <div className="sidebar-history">{projects.data?.map(p => <NavLink key={p.id} to={`/projects/${p.id}`} title={p.title}><span>◌</span><span>{p.title}</span></NavLink>)}{!projects.data?.length && <p className="history-empty">从一个新想法开始吧</p>}</div>
      <div className="sidebar-bottom"><NavLink aria-label="服务状态" to="/status"><span>◉</span>服务状态</NavLink><div className="workspace-profile"><span className="profile-avatar">A</span><div><strong>个人工作空间</strong></div><span className="online-dot" /></div></div>
    </aside>
    <main className={chat ? 'app-main chat-main' : 'app-main management-main'}><Suspense fallback={<p className="loading-page">正在加载工作台…</p>}><Routes>
      <Route path="/" element={<Chat />} /><Route path="/chat" element={<Chat />} /><Route path="/projects/:projectId" element={<Chat />} />
      <Route path="/status" element={<><Typography.Title level={2}>服务状态</Typography.Title><Status /></>} />
      <Route path="/capabilities" element={<Capabilities />} /><Route path="/settings/models" element={<ModelSettings />} />
      <Route path="*" element={<><Typography.Title level={2}>页面不存在</Typography.Title><Link to="/chat">返回工作台</Link></>} />
    </Routes></Suspense></main>
  </div>;
}
