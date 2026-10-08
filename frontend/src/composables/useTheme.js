/**
 * 主题管理（组合式函数）。
 *
 * 实现原理：通过切换 <html data-theme="dark"> 属性来换肤，
 * 所有颜色都来自 CSS 变量（见 styles/tokens.css），
 * 所以切换主题**不需要重新渲染任何组件**，浏览器直接就重绘了。
 *
 * 这是设计令牌方案最直接的收益：换肤成本接近零。
 */
import { computed, ref } from 'vue'
import { STORAGE_KEYS } from '@/config/constants'

// 主题模式：light / dark / auto
const currentMode = ref(localStorage.getItem(STORAGE_KEYS.THEME) || 'light')

// 系统是否偏好深色（用于 auto 模式）
const systemPrefersDark =
  typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null

/** 实际生效的主题（auto 模式下由系统偏好决定） */
const effectiveTheme = computed(() => {
  if (currentMode.value === 'auto') {
    return systemPrefersDark?.matches ? 'dark' : 'light'
  }
  return currentMode.value
})

/** 把主题应用到 DOM */
function applyTheme() {
  const theme = effectiveTheme.value
  document.documentElement.setAttribute('data-theme', theme)

  // Element Plus 的深色模式需要单独的 class
  if (theme === 'dark') {
    document.documentElement.classList.add('dark')
  } else {
    document.documentElement.classList.remove('dark')
  }
}

/** 切换主题 */
function setTheme(mode) {
  currentMode.value = mode
  localStorage.setItem(STORAGE_KEYS.THEME, mode)
  applyTheme()
}

/** 在浅色/深色之间切换（忽略 auto） */
function toggleTheme() {
  setTheme(effectiveTheme.value === 'dark' ? 'light' : 'dark')
}

/** 初始化：在应用启动时调用一次 */
function initTheme() {
  applyTheme()

  // 监听系统主题变化（auto 模式下需要）
  systemPrefersDark?.addEventListener?.('change', () => {
    if (currentMode.value === 'auto') {
      applyTheme()
    }
  })
}

export function useTheme() {
  return {
    currentMode,
    effectiveTheme,
    setTheme,
    toggleTheme,
    initTheme,
  }
}
