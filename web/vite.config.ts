import { defineConfig } from 'vitest/config';
import path from 'node:path';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'deck',
        short_name: 'deck',
        description: 'A self-hosted AI agent',
        display: 'standalone',
        start_url: '/',
        background_color: '#f7f7f8',
        theme_color: '#f7f7f8',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' },
        ],
      },
      workbox: {
        // The API is never cached. Navigations to it go to the network.
        navigateFallbackDenylist: [/^\/api\//, /^\/(experience|prototype)\.html/],
        globPatterns: ['**/*.{js,css,html,svg,png}'],
      },
    }),
  ],
  build: { rollupOptions: { input: { app: path.resolve(import.meta.dirname, 'index.html'), prototype: path.resolve(import.meta.dirname, 'prototype.html') } } },
  resolve: { alias: { '@': path.resolve(import.meta.dirname, 'src') } },
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
