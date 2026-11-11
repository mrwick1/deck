import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig({
  plugins: [svelte()],
  server: {
    proxy: {
      // Dev only: stands in for the Authelia proxy.
      '/api': {
        target: 'http://127.0.0.1:8790',
        headers: { 'Remote-User': 'dev' },
      },
    },
  },
  test: { include: ['src/**/*.test.ts'] },
});
