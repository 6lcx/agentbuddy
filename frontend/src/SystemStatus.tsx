import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Alert, Button, Divider, Space, Tag, Typography } from 'antd';
import { createDiagnostic, getOperation, getSystemStatus } from './api/system';

const workerLabels = {
  not_started: '未启动', running: '运行中', stale: '心跳已过期', stopped: '已停止', lost: '异常退出',
};
const operationLabels = { queued: '排队中', running: '执行中', succeeded: '检查通过', failed: '检查失败' };

export function SystemStatus() {
  const [operationId, setOperationId] = useState<string | null>(null);
  const status = useQuery({
    queryKey: ['system-status'], queryFn: ({ signal }) => getSystemStatus(signal), refetchInterval: 2000,
  });
  const create = useMutation({ mutationFn: createDiagnostic, onSuccess: result => setOperationId(result.id) });
  const operation = useQuery({
    queryKey: ['operation', operationId],
    queryFn: ({ signal }) => getOperation(operationId!, signal),
    enabled: operationId !== null,
    refetchInterval: query => ['queued', 'running'].includes(query.state.data?.status ?? '') ? 500 : false,
  });
  const busy = operationId !== null && (operation.isPending || ['queued', 'running'].includes(operation.data?.status ?? ''));
  return <section aria-label="后台执行状态">
    <Divider />
    <Typography.Title level={4}>后台执行状态</Typography.Title>
    {status.isPending && <p>正在读取数据库与 Worker 状态…</p>}
    {status.isError && <Alert type="warning" title="后台尚未就绪" description={status.error.message} />}
    {status.data && <>
      <Space wrap><Tag color="success">SQLite 已就绪</Tag><Tag color={status.data.worker.status === 'running' ? 'success' : 'default'}>Worker：{workerLabels[status.data.worker.status]}</Tag></Space>
      <Typography.Paragraph>磁盘可用：{(status.data.storage.free_bytes / 1024 ** 3).toFixed(1)} GiB · Office 渲染器：{status.data.office_renderer_available ? '已找到' : '未配置'}</Typography.Paragraph>
      {status.data.worker.heartbeat_at && <Typography.Paragraph type="secondary">最后心跳：{new Date(status.data.worker.heartbeat_at * 1000).toLocaleString()}</Typography.Paragraph>}
      {status.data.worker.status !== 'running' && <Typography.Paragraph>请启动独立 Worker，后台检查与后续任务才能执行。</Typography.Paragraph>}
    </>}
    <div className="actions"><Button onClick={() => create.mutate()} loading={create.isPending} disabled={status.data?.worker.status !== 'running' || busy}>运行存储检查</Button></div>
    {create.isError && <Alert type="error" title="检查提交失败" description={create.error.message} />}
    {operation.isError && <Alert type="error" title="检查状态读取失败" description={operation.error.message} action={<Button onClick={() => void operation.refetch()}>重试</Button>} />}
    {operation.data && <div aria-live="polite">
      <Tag color={operation.data.status === 'succeeded' ? 'success' : operation.data.status === 'failed' ? 'error' : 'processing'}>{operationLabels[operation.data.status]}</Tag>
      {operation.data.result_json && <p>临时文件读写正常 · SQLite {operation.data.result_json.journal_mode?.toUpperCase()} · 外键检查{operation.data.result_json.foreign_keys ? '已启用' : '未启用'}</p>}
      {operation.data.error_json && <p>{operation.data.error_json.message}</p>}
    </div>}
  </section>;
}
