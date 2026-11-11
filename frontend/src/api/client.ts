export interface Health { status: string; service: string }

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch(`${__API_PREFIX__}/health`, { signal });
  if (!response.ok) throw new Error(`服务检查失败（HTTP ${response.status}）`);
  const data: unknown = await response.json();
  if (!data || typeof data !== 'object' || !('status' in data) || !('service' in data)
      || typeof data.status !== 'string' || typeof data.service !== 'string') {
    throw new Error('后端返回了无效的健康状态');
  }
  return { status: data.status, service: data.service };
}
