import type { components } from './schema';
export type Provider = components['schemas']['ProviderView'];
export type Model = components['schemas']['ModelView'];
export type Project = components['schemas']['ProjectView'];
export type Message = components['schemas']['MessageView'];
export type Run = components['schemas']['RunView'];
export async function api<T>(path: string, body?: unknown, method?: string, headers?: Record<string, string>): Promise<T> {
  const response = await fetch(`${__API_PREFIX__}${path}`, {
    method: method ?? (body === undefined ? 'GET' : 'POST'),
    headers: { 'Content-Type': 'application/json', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error?.message ?? value.detail ?? `请求失败 (${response.status})`);
  return value.data as T;
}
export const eventUrl = (id: string) => `${__API_PREFIX__}/runs/${encodeURIComponent(id)}/events`;

export async function queued<T>(path: string, body: unknown): Promise<T> {
  const operation = await api<{ id: string }>(path, body);
  for (let attempt = 0; attempt < 120; attempt++) {
    const response = await fetch(`${__API_PREFIX__}/operations/${operation.id}`);
    const result = await response.json();
    if (!response.ok) throw new Error('无法读取后台任务状态');
    if (result.status === 'succeeded') return result.result_json as T;
    if (result.status === 'failed') throw new Error(result.error_json?.message ?? '后台处理失败');
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error(`后台任务仍未完成，请检查 Worker。任务：${operation.id}`);
}
