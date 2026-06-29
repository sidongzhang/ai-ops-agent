import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

function matchAntdGroup(id) {
  const groups = {
    'vendor-antd-form': [
      '/auto-complete/',
      '/checkbox/',
      '/form/',
      '/input/',
      '/input-number/',
      '/mentions/',
      '/radio/',
      '/select/',
      '/switch/',
    ],
    'vendor-antd-data': [
      '/avatar/',
      '/badge/',
      '/breadcrumb/',
      '/calendar/',
      '/collapse/',
      '/descriptions/',
      '/divider/',
      '/image/',
      '/list/',
      '/pagination/',
      '/rate/',
      '/statistic/',
      '/table/',
    ],
  }

  for (const [groupName, markers] of Object.entries(groups)) {
    if (markers.some((marker) => id.includes(marker))) {
      return groupName
    }
  }

  return 'vendor-antd-core'
}

export default defineConfig({
  plugins: [vue()],
  server: { port: 5173 },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined

          if (id.includes('@ant-design/icons-vue')) {
            return 'vendor-antd-icons'
          }
          if (id.includes('ant-design-vue')) {
            return matchAntdGroup(id)
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
