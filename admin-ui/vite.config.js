import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

const backend = 'http://localhost:8080';

export default defineConfig({
  root: '.',
  plugins: [tailwindcss()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/auth': backend,
      '/analytics': backend,
      '/prompts': backend,
      '/knowledge': backend,
      '/training': backend,
      '/operators': backend,
      '/system': backend,
      '/admin-users': backend,
      '/export': backend,
      '/admin': backend,
      '/health': backend,
      '/metrics': backend,
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
      },
    },
  },
});
