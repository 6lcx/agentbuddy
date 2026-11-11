import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

function prefix(value: string): string {
  const normalized = value.replace(/\/+$/, '');
  if (!/^(\/[A-Za-z0-9_-]+)+$/.test(normalized)) {
    throw new Error(`Invalid path prefix: ${value}`);
  }
  return normalized;
}

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env };
  const appBase = prefix(env.APP_BASE_PATH ?? '/app');
  const apiPrefix = prefix(env.API_PREFIX ?? '/api/v1');
  if (apiPrefix === appBase || apiPrefix.startsWith(`${appBase}/`) || appBase.startsWith(`${apiPrefix}/`)) {
    throw new Error('Page and API prefixes must not overlap');
  }
  return {
    plugins: [react()],
    base: `${appBase}/`,
    define: { __API_PREFIX__: JSON.stringify(apiPrefix) },
    server: {
      port: 5173,
      strictPort: true,
      proxy: { [apiPrefix]: { target: env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000' } },
    },
  };
});
