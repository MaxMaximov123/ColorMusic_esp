import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { quasar, transformAssetUrls } from '@quasar/vite-plugin'

export default defineConfig({
  plugins: [
    vue({ template: { transformAssetUrls } }),
    quasar({ sassVariables: false })
  ],
  build: {
    outDir: '../server/public',
    emptyOutDir: true,
    target: 'es2020',
    rollupOptions: {
      output: {
        manualChunks: {
          'vue-vendor': ['vue', 'vue-router'],
          'quasar': ['quasar'],
          'chart': ['chart.js', 'vue-chartjs']
        }
      }
    }
  },
  server: {
    proxy: {
      '/ws': { target: 'http://localhost:3000', ws: true },
      '/api': { target: 'http://localhost:3000' }
    }
  }
})
