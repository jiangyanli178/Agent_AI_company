import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// /studio/ 和 /studio 目录请求会被 Vite 的 SPA history fallback 抢先返回 Vue 壳，
// 这里重写成显式文件路径，保证 dev 下两者都能打开量化工作室页面。
const studioIndexRewrite = {
  name: 'studio-index-rewrite',
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      if (req.url === '/studio/' || req.url === '/studio') req.url = '/studio/index.html'
      next()
    })
  },
}

export default defineConfig({
  plugins: [vue(), studioIndexRewrite],
  server: {
    port: 5173,
    // 端口被占就直接报错，不要静默改用 5174。
    // 悄悄换端口会让人以为"起好了"，实际多一个看不见的进程，
    // 而且两次启动的窗口看起来都正常，排查起来非常费劲。
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1200,
  },
})
