import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  base: '/odb-viewer/',
  build: {
    rollupOptions: {
      input: {
        main: 'index.html',
        loadBenchmark: 'load-benchmark.html',
      },
    },
  },
})
