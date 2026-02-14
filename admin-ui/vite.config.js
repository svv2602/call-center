import { defineConfig } from 'vite';

export default defineConfig({
  root: '.',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/auth': 'http://localhost:8080',
      '/analytics': 'http://localhost:8080',
      '/prompts': 'http://localhost:8080',
      '/knowledge': 'http://localhost:8080',
      '/operators': 'http://localhost:8080',
      '/admin': 'http://localhost:8080',
      '/health': 'http://localhost:8080',
      '/metrics': 'http://localhost:8080',
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
      },
    },
  },
});
