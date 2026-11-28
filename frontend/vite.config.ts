import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

const reactRuntime = fileURLToPath(new URL('./node_modules/react/index.js', import.meta.url));
const reactJsxRuntime = fileURLToPath(new URL('./node_modules/react/jsx-runtime.js', import.meta.url));

export default defineConfig({
  resolve: { alias: [{ find: /^react\/jsx-runtime$/, replacement: reactJsxRuntime }, { find: /^react$/, replacement: reactRuntime }] },
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    fs: { allow: ['..', '../backend/app/dify_plugins/ui'] },
    proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: false } },
  },
  build: { outDir: 'dist', emptyOutDir: true },
});
