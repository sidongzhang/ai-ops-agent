import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/auth': 'http://127.0.0.1:8000',
      '/systems': 'http://127.0.0.1:8000',
      '/collectors': 'http://127.0.0.1:8000',
      '/collector': 'http://127.0.0.1:8000',
      '/workflows': 'http://127.0.0.1:8000',
      '/messages': 'http://127.0.0.1:8000',
      '/audit': 'http://127.0.0.1:8000',
      '/analytics': 'http://127.0.0.1:8000',
      '/openapi': 'http://127.0.0.1:8000',
      '/incidents': 'http://127.0.0.1:8000',
      '/healthz': 'http://127.0.0.1:8000',
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
      '/feishu': 'http://127.0.0.1:8000',
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined

          if (id.includes('@ant-design/icons-vue') || id.includes('ant-design-vue')) {
            return 'vendor-antd'
          }
          if (id.includes('vue-router')) {
            return 'vendor-router'
          }
          if (id.includes('pinia')) {
            return 'vendor-state'
          }
          if (id.includes('axios')) {
            return 'vendor-api'
          }
          if (id.includes('marked')) {
            return 'vendor-markdown'
          }
          if (id.includes('/node_modules/vue/') || id.includes('/node_modules/@vue/')) {
            return 'vendor-vue'
          }

          return 'vendor-misc'
        },
      },
    },
  },
})
