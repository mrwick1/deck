import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig({
  plugins: [
    svelte(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'deck',
        short_name: 'deck',
        description: 'A self-hosted AI agent',
        display: 'standalone',
        start_url: '/',
        background_color: '#121214',
        theme_color: '#4f46e5',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' },
        ],
      },
      workbox: {
        // The API is never cached. Navigations to it go to the network.
        navigateFallbackDenylist: [/^\/api\//],
        globPatterns: ['**/*.{js,css,html,svg,png}'],
      },
    }),
  ],
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
