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

app.mount('#app')
