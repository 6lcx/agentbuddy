import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
export default defineConfig({ plugins: [react()], define: { __API_PREFIX__: JSON.stringify('/test/api') }, test: { environment: 'jsdom', include: ['src/**/*.test.ts?(x)'] } });
