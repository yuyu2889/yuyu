/**
 * 应用入口。
 */
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

// 样式
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import '@/styles/tokens.css'

import App from './App.vue'
import router from './router'
import { useTheme } from '@/composables/useTheme'

// ---------------------------------------------------------------------------
//  主题初始化
// ---------------------------------------------------------------------------
//  必须在挂载前执行，否则会出现"先渲染浅色再闪一下变深色"的问题（FOUC）。
//  这是深色模式实现的常见坑。
useTheme().initTheme()

const app = createApp(App)

// ---------------------------------------------------------------------------
//  注册 Element Plus 图标
// ---------------------------------------------------------------------------
//  逐个注册全局图标组件，这样模板里可以直接写 <el-icon><HomeFilled /></el-icon>
//  也可以用动态组件 <component :is="'HomeFilled'" />（侧边栏菜单就是这么用的）
for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component)
}

app.use(router)
app.use(ElementPlus, {
  // 中文语言包（分页、日期选择器等组件的文案）
  locale: zhCn,
  // 全局默认尺寸
  size: 'default',
  // 弹窗的层级基准（避免被自定义的 fixed 元素遮挡）
  zIndex: 2000,
})

// ---------------------------------------------------------------------------
//  全局错误处理
// ---------------------------------------------------------------------------
//  为什么需要这个？
//  如果某个错误发生在组件树之外（比如路由守卫、事件回调、异步任务里），
//  Vue 的 onErrorCaptured 错误边界是捕获不到的 ——
//  那种情况下用户看到的往往是一片空白，且没有任何提示。
//
//  这里做两件事：
//    1. 在控制台打印完整堆栈（开发时排查用）
//    2. 在页面顶部显示一个可关闭的错误条（让问题可见，而不是静默白屏）
//  这个"让错误可见"的思路，是解决白屏类问题最有效的手段。
// ---------------------------------------------------------------------------

/** 在页面顶部显示一个错误提示条 */
function showFatalError(title, message) {
  // 避免重复插入
  if (document.getElementById('fatal-error-bar')) {
    return
  }

  const bar = document.createElement('div')
  bar.id = 'fatal-error-bar'
  bar.style.cssText = `
    position: fixed; top: 0; left: 0; right: 0; z-index: 99999;
    background: #fef0f0; color: #f56c6c; border-bottom: 1px solid #fbc4c4;
    padding: 10px 16px; font-size: 13px; line-height: 1.6;
    font-family: -apple-system, 'Microsoft YaHei', sans-serif;
    box-shadow: 0 2px 8px rgba(0,0,0,.08);
  `
  bar.innerHTML = `
    <strong style="margin-right:8px;">⚠ ${title}</strong>
    <span style="color:#c45656;">${message}</span>
    <span style="float:right;cursor:pointer;font-weight:bold;padding:0 6px;"
          onclick="this.parentNode.remove()">✕</span>
  `
  document.body.appendChild(bar)
}

app.config.errorHandler = (err, instance, info) => {
  console.error('[全局错误] ' + info, err)
  showFatalError('页面出现错误', (err?.message || String(err)) + '（位置：' + info + '，详情见 F12 控制台）')
}

// 捕获未处理的 Promise 异常（比如没有 try/catch 的 async 函数）
window.addEventListener('unhandledrejection', (event) => {
  console.error('[未处理的 Promise 异常]', event.reason)
  const msg = event.reason?.message || String(event.reason)
  // 接口 401/403 这类错误拦截器已经提示过了，不重复显示
  if (msg && !msg.includes('登录') && !msg.includes('权限')) {
    showFatalError('请求或异步任务出错', msg)
  }
})

app.mount('#app')
