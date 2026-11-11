import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Switch, Table, Tag, Typography } from 'antd';
import { api, type Model, type Provider } from './api/platform';

const defaults: Record<string, string> = { qwen_dashscope: 'https://dashscope.aliyuncs.com/api/v1', deepseek: 'https://api.deepseek.com', claude: 'https://api.anthropic.com/v1' };
export function ModelSettings() {
  const client = useQueryClient();
  const providers = useQuery({ queryKey: ['providers'], queryFn: () => api<Provider[]>('/providers') });
  const models = useQuery({ queryKey: ['models'], queryFn: () => api<Model[]>('/models'), refetchInterval: 3000 });
  const settings = useQuery({ queryKey: ['settings'], queryFn: () => api<{ revision: number; default_model_id: string | null }>('/settings') });
  const [providerForm] = Form.useForm();
  const [modelForm] = Form.useForm();
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null);
  const [editingModel, setEditingModel] = useState<Model | null>(null);
  const [operationId, setOperationId] = useState<string | null>(null);
  const operation = useQuery({ queryKey: ['model-probe', operationId], enabled: !!operationId,
    queryFn: async () => {
      const response = await fetch(`${__API_PREFIX__}/operations/${operationId}`);
      if (!response.ok) throw new Error('无法读取模型探测结果');
      return response.json() as Promise<{ status: string; result_json: { passed?: boolean; error?: { message: string } } | null; error_json: { message: string } | null }>;
    }, refetchInterval: q => ['queued', 'running'].includes(q.state.data?.status ?? 'queued') ? 1000 : false });
  const saveProvider = useMutation({ mutationFn: (values: Record<string, unknown>) => api('/providers' + (editingProvider ? `/${editingProvider.id}` : ''), {
    ...values, api_key: values.api_key || null, config_json: { ...(values.workspace_id ? { workspace_id: values.workspace_id } : {}) }, workspace_id: undefined,
    ...(editingProvider ? { revision: editingProvider.revision } : {}),
  }, editingProvider ? 'PATCH' : 'POST'), onSuccess: async () => { providerForm.resetFields(); setEditingProvider(null); await client.cancelQueries({ queryKey: ['providers'] }); await client.invalidateQueries({ queryKey: ['providers'] }); await client.invalidateQueries({ queryKey: ['models'] }); } });
  const saveModel = useMutation({ mutationFn: (values: Record<string, unknown>) => api('/models' + (editingModel ? `/${editingModel.id}` : ''), { ...values, ...(editingModel ? { revision: editingModel.revision } : {}) }, editingModel ? 'PATCH' : 'POST'), onSuccess: () => { modelForm.resetFields(); setEditingModel(null); void client.invalidateQueries({ queryKey: ['models'] }); } });
  const testModel = useMutation({ mutationFn: (id: string) => api<{ id: string }>(`/models/${id}/test`, {}), onSuccess: r => setOperationId(r.id) });
  const setDefault = useMutation({ mutationFn: (id: string) => api('/settings', { default_model_id: id, revision: settings.data?.revision ?? 0 }, 'PATCH'), onSuccess: () => void client.invalidateQueries({ queryKey: ['settings'] }) });
  const error = providers.error || models.error || settings.error || saveProvider.error || saveModel.error || testModel.error || setDefault.error || operation.error;
  return <div className="settings-page"><Typography.Title level={2}>模型设置</Typography.Title>
    <Typography.Paragraph>平台统一保存连接配置。保存后需运行文本与工具探测，通过后才能用于对话。密钥不会回显。</Typography.Paragraph>
    {error && <Alert type="error" title="操作未完成" description={error.message} />}
    <Card title={editingProvider ? '编辑提供方' : '添加提供方'}>
      <Form form={providerForm} layout="vertical" initialValues={{ provider_type: 'qwen_dashscope', base_url: defaults.qwen_dashscope, enabled: true }} onFinish={saveProvider.mutate}>
        <Form.Item name="name" label="连接名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="provider_type" label="提供方" rules={[{ required: true }]}><Select options={[{ value: 'qwen_dashscope', label: 'Qwen · DashScope 原生' }, { value: 'deepseek', label: 'DeepSeek 原生' }, { value: 'claude', label: 'Claude Messages' }]} onChange={v => providerForm.setFieldValue('base_url', defaults[v])} /></Form.Item>
        <Form.Item name="base_url" label="API 基地址" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="workspace_id" label="Qwen 工作空间 ID（可选）"><Input /></Form.Item>
        <Form.Item name="api_key" label={editingProvider ? '新密钥（留空保留）' : 'API 密钥'} rules={editingProvider ? [] : [{ required: true }]}><Input.Password autoComplete="new-password" /></Form.Item>
        <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        <Space><Button htmlType="submit" type="primary" loading={saveProvider.isPending}>保存连接</Button><Button onClick={() => { setEditingProvider(null); providerForm.resetFields(); }}>取消编辑</Button></Space>
      </Form>
      <Table rowKey="id" dataSource={providers.data} pagination={false} columns={[{ title: '连接', dataIndex: 'name' }, { title: '提供方', dataIndex: 'provider_type' }, { title: '密钥', render: () => '已配置' }, { title: '操作', render: (_, row: Provider) => <Button onClick={() => { setEditingProvider(row); providerForm.setFieldsValue({ name: row.name, provider_type: row.provider_type, base_url: row.base_url, enabled: row.enabled, workspace_id: row.config_json.workspace_id ?? '', api_key: '' }); }}>编辑连接</Button> }]} />
    </Card>
    <Card title={editingModel ? '编辑模型' : '添加可用模型'}>
      <Form form={modelForm} layout="vertical" initialValues={{ enabled: true, context_window: 32768, max_output_tokens: 2048 }} onFinish={saveModel.mutate}>
        <Form.Item name="provider_id" label="所属连接" rules={[{ required: true }]}><Select options={providers.data?.map(p => ({ label: p.name, value: p.id }))} /></Form.Item>
        <Form.Item name="model_id" label="模型名称" rules={[{ required: true }]}><Input placeholder="填写服务提供方的实际模型 ID" /></Form.Item>
        <Form.Item name="context_window" label="上下文上限" rules={[{ required: true }]}><InputNumber min={4096} max={2000000} /></Form.Item>
        <Form.Item name="max_output_tokens" label="输出上限" rules={[{ required: true }]}><InputNumber min={64} max={32768} /></Form.Item>
        <Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item>
        <Space><Button htmlType="submit" type="primary" loading={saveModel.isPending}>保存模型</Button><Button onClick={() => { setEditingModel(null); modelForm.resetFields(); }}>取消修改</Button></Space>
      </Form>
      <Table rowKey="id" dataSource={models.data} pagination={false} columns={[{ title: '模型', dataIndex: 'model_id' }, { title: '连接', dataIndex: 'provider_name' }, { title: '状态', render: (_, row: Model) => <Tag color={row.available ? 'success' : 'default'}>{row.available ? '探测通过' : row.test_status}</Tag> }, { title: '操作', render: (_, row: Model) => <Space wrap><Button onClick={() => { setEditingModel(row); modelForm.setFieldsValue({ provider_id: row.provider_id, model_id: row.model_id, context_window: row.context_window, max_output_tokens: row.max_output_tokens, enabled: row.enabled }); }}>编辑模型</Button><Button onClick={() => testModel.mutate(row.id)} disabled={testModel.isPending}>运行探测</Button><Button disabled={!row.available || settings.data?.default_model_id === row.id} onClick={() => setDefault.mutate(row.id)}>{settings.data?.default_model_id === row.id ? '默认模型' : '设为默认'}</Button></Space> }]} />
      {operationId && <Alert type={operation.data?.result_json?.passed ? 'success' : operation.data?.result_json?.passed === false || operation.data?.status === 'failed' ? 'error' : 'info'} title={operation.data?.result_json?.passed ? '文本与工具探测通过' : `探测状态：${operation.data?.status ?? '等待结果'}`} description={operation.data?.result_json?.error?.message ?? operation.data?.error_json?.message ?? '探测由独立 Worker 执行；排队时请检查 Worker 是否启动。'} />}
    </Card>
  </div>;
}
