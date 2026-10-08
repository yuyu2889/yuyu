import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

// Vite 配置
// 设计要点：
// 1. 用 unplugin 做 Element Plus 的按需自动导入 —— 不需要手写 import，
//    但只有真正用到的组件才会被打包（避免把整个组件库塞进 bundle）
// 2. 配置 @ 路径别名，避免出现 ../../../ 这种难以阅读的相对路径
// 3. 开发服务器加 dev proxy：把 /api 转发到后端，
//    这样前端代码里写相对路径即可，不用硬编码后端地址
//    （生产环境用 Nginx 做同样的转发）
export default defineConfig({
  plugins: [
    vue(),
    AutoImport({
      resolvers: [ElementPlusResolver()],
      imports: ['vue', 'vue-router'],
      dts: false,
    }),
    Components({
      resolvers: [ElementPlusResolver()],
      dts: false,
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // 监听 127.0.0.1，避免意外暴露到局域网
    host: '127.0.0.1',
    proxy: {
      // 把 /api 和 /static 转发到后端，前端代码里就可以写相对路径
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/static': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    // 生产构建时关闭 sourcemap（体积小、也不暴露源码结构）
    sourcemap: false,
    // 大 chunk 警告阈值调高一点（ECharts 本身就比较大）
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        // 手动分包：把体积大且不常变的库单独打成一个 chunk，
        // 这样业务代码更新时，用户不需要重新下载这些库
        // （利用浏览器缓存，显著提升二次访问速度）
        manualChunks: {
          'vue-vendor': ['vue', 'vue-router'],
          'element-plus': ['element-plus', '@element-plus/icons-vue'],
          echarts: ['echarts'],
        },
      },
    },
  },
})
