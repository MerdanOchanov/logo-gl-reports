import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Сборка — обычный (не module) скрипт: на Windows Python отдаёт .js как text/plain,
 * и браузер отказывается исполнять type="module". Классический скрипт этого не проверяет.
 */
const classicScript = (): Plugin => ({
  name: 'classic-script',
  apply: 'build',
  enforce: 'post',
  transformIndexHtml(html) {
    return html.replace(/<script type="module" crossorigin src=/g, '<script defer src=')
  },
})

export default defineConfig({
  plugins: [react(), classicScript()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8790' },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1500,
    rollupOptions: { output: { format: 'iife', inlineDynamicImports: true } },
  },
})
