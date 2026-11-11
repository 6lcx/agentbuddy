import { afterEach, expect, it, vi } from 'vitest';
import { getHealth } from './client';
afterEach(() => vi.unstubAllGlobals());
it('uses the configured prefix and returns health', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok', service: 'agentbuddy-api' })));
  vi.stubGlobal('fetch', fetcher);
  expect(await getHealth()).toEqual({ status: 'ok', service: 'agentbuddy-api' });
  expect(fetcher).toHaveBeenCalledWith('/test/api/health', { signal: undefined });
});
it('reports HTTP failures', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 503 })));
  await expect(getHealth()).rejects.toThrow('503');
});
it('rejects unexpected response content', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}')));
  await expect(getHealth()).rejects.toThrow('无效');
});
