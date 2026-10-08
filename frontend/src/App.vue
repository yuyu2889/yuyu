<script setup>
/**
 * 应用根组件。
 *
 * 职责非常轻：只负责"启动时恢复登录态"和"渲染路由出口"。
 * 布局（侧边栏/顶栏）交给 layouts/MainLayout.vue，
 * 这样登录页可以用不同的布局（见路由的 meta.layout）。
 */
import { onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

onMounted(async () => {
  // 移除 index.html 里的首屏加载动画。
  // 注意：这个元素在 Vue 的挂载点内部，所以 Vue 渲染时通常已经把它替换掉了；
  // 这里再查一次是为了兜底（某些情况下 Vue 可能保留原有 DOM 节点）。
  const loading = document.getElementById('app-loading')
  if (loading) {
    loading.remove()
  }

  // 应用启动时尝试恢复登录态：
  // 如果 localStorage 里有 Token，用它换一次最新的用户信息。
  // 为什么不在登录时就把用户信息存下来直接用？
  // 因为用户信息可能被改过（比如管理员改了你的角色），
  // 每次启动都校验一次更准确。
  await auth.fetchCurrentUser()
})
</script>

<template>
  <router-view v-slot="{ Component }">
    <!-- 路由切换时的淡入过渡，让页面切换不那么生硬 -->
    <transition name="fade" mode="out-in">
      <component :is="Component" />
    </transition>
  </router-view>
</template>
