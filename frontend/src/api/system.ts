import type { components } from './schema';
export type SystemStatus = components['schemas']['SystemStatus'];
export interface Operation {
  id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  result_json: { storage?: string; journal_mode?: string; foreign_keys?: boolean } | null;
  error_json: { code: string; message: string } | null;
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${__API_PREFIX__}${path}`, init);
  if (!response.ok) {
    if (response.status === 503) throw new Error('数据库尚未初始化或暂不可用，请先执行数据库迁移。');
    throw new Error(`请求失败（HTTP ${response.status}）`);
  }
  return response.json() as Promise<T>;
}
export const getSystemStatus = (signal?: AbortSignal) => request<SystemStatus>('/system/status', { signal });
export const createDiagnostic = () => request<Pick<Operation, 'id' | 'status'>>('/system/diagnostics', { method: 'POST' });
export const getOperation = (id: string, signal?: AbortSignal) => request<Operation>(`/operations/${encodeURIComponent(id)}`, { signal });
