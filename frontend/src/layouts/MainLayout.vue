<script setup>
/**
 * 主布局：侧边栏 + 顶栏 + 内容区。
 *
 * 设计要点（面试可以讲）：
 *
 * 1. **菜单由路由表生成，而不是手写**
 *    原项目的侧边栏菜单是手写的一堆 <el-menu-item>，
 *    每加一个页面就要改一次菜单，而且容易和路由不一致。
 *    V2 从 router 的 routes 里自动生成菜单：
 *    - 只会多不会少（路由加了菜单自动就有）
 *    - meta.hidden 控制是否隐藏（比如详情页不出现在菜单里）
 *    - meta.requiresAdmin 控制只有管理员才能看到
 *
 * 2. **响应式折叠**
 *    窄屏自动折叠侧边栏，状态存 localStorage（下次进来保持）。
 *
 * 3. **面包屑从路由 meta 推导**
 *    不用每个页面手动写面包屑。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { useTheme } from '@/composables/useTheme'
import { STORAGE_KEYS } from '@/config/constants'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { effectiveTheme, toggleTheme } = useTheme()

// ---------------------------------------------------------------------------
//  侧边栏折叠状态
// ---------------------------------------------------------------------------
const collapsed = ref(localStorage.getItem(STORAGE_KEYS.SIDEBAR_COLLAPSED) === 'true')

function toggleCollapse() {
  collapsed.value = !collapsed.value
  localStorage.setItem(STORAGE_KEYS.SIDEBAR_COLLAPSED, String(collapsed.value))
}

// ---------------------------------------------------------------------------
//  从路由表生成菜单
// ---------------------------------------------------------------------------
const menuItems = computed(() => {
  // 找到 '/' 这条父路由，取它的 children
  const rootRoute = router.options.routes.find((r) => r.path === '/')
  const children = rootRoute?.children || []

  const items = children
    .filter((child) => {
      // 隐藏 meta.hidden 的路由（如详情页）
      if (child.meta?.hidden) return false
      // 需要管理员权限的，非管理员不显示
      if (child.meta?.requiresAdmin && !auth.isAdmin.value) return false
      return true
    })
    .map((child) => ({
      // 拼出完整路径（子路由的 path 是相对的）
      path: '/' + child.path.replace(/^\//, ''),
      name: child.name,
      title: child.meta?.title || child.name,
      icon: child.meta?.icon || 'Menu',
      group: child.meta?.requiresAdmin ? '管理' : '主要功能',
    }))

  return items
})

/** 按分组整理菜单（普通功能 / 管理功能分组展示） */
const menuGroups = computed(() => {
  const groups = {}
  for (const item of menuItems.value) {
    if (!groups[item.group]) groups[item.group] = []
    groups[item.group].push(item)
  }
  return groups
})

/** 当前激活的菜单项（用路径前缀匹配，这样详情页也能高亮对应的列表菜单） */
const activeMenu = computed(() => {
  const path = route.path
  // 精确匹配优先
  const exact = menuItems.value.find((item) => item.path === path)
  if (exact) return exact.path
  // 前缀匹配（比如 /equipment/3 应该高亮 /equipment）
  const prefix = menuItems.value
    .filter((item) => path.startsWith(item.path) && item.path !== '/dashboard')
    .sort((a, b) => b.path.length - a.path.length)[0]
  return prefix?.path || path
})

/** 面包屑 */
const breadcrumbs = computed(() => {
  const crumbs = [{ title: '首页', path: '/dashboard' }]
  if (route.path !== '/dashboard' && route.meta?.title) {
    crumbs.push({ title: route.meta.title, path: route.path })
  }
  return crumbs
})

// ---------------------------------------------------------------------------
//  用户操作
// ---------------------------------------------------------------------------
const userMenuVisible = ref(false)

async function handleLogout() {
  try {
    await ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      confirmButtonText: '退出',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return // 用户取消
  }
  await auth.logout()
  router.push({ name: 'Login' })
}

function handleCommand(command) {
  if (command === 'logout') {
    handleLogout()
  } else if (command === 'profile') {
    router.push({ name: 'Profile' })
  }
}

// ---------------------------------------------------------------------------
//  响应式：窄屏自动折叠
// ---------------------------------------------------------------------------
function handleResize() {
  if (window.innerWidth < 992 && !collapsed.value) {
    collapsed.value = true
  }
}

onMounted(() => {
  handleResize()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
})
</script>

<template>
  <div class="layout" :class="{ 'is-collapsed': collapsed }">
    <!-- ==================== 侧边栏 ==================== -->
    <aside class="sidebar">
      <div class="logo">
        <div class="logo-icon">
          <el-icon :size="22"><School /></el-icon>
        </div>
        <transition name="fade">
          <div v-show="!collapsed" class="logo-text">
            <div class="logo-title">实验室预约</div>
            <div class="logo-sub">设备管理系统</div>
          </div>
        </transition>
      </div>

      <nav class="menu">
        <template v-for="(items, group) in menuGroups" :key="group">
          <div v-show="!collapsed" class="menu-group-title">{{ group }}</div>
          <router-link
            v-for="item in items"
            :key="item.path"
            :to="item.path"
            class="menu-item"
            :class="{ active: activeMenu === item.path }"
          >
            <el-icon :size="18"><component :is="item.icon" /></el-icon>
            <transition name="fade">
              <span v-show="!collapsed" class="menu-label">{{ item.title }}</span>
            </transition>
            <!-- 折叠时用 tooltip 显示文字 -->
            <el-tooltip
              v-if="collapsed"
              :content="item.title"
              placement="right"
              :show-after="200"
            />
          </router-link>
        </template>
      </nav>

      <div class="sidebar-footer">
        <div class="collapse-btn" @click="toggleCollapse">
          <el-icon :size="18">
            <component :is="collapsed ? 'Expand' : 'Fold'" />
          </el-icon>
          <span v-show="!collapsed">收起菜单</span>
        </div>
      </div>
    </aside>

    <!-- ==================== 主区域 ==================== -->
    <div class="main">
      <header class="header">
        <!-- 面包屑 -->
        <div class="header-left">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item
              v-for="crumb in breadcrumbs"
              :key="crumb.path"
              :to="crumb.path === route.path ? undefined : crumb.path"
            >
              {{ crumb.title }}
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <!-- 右侧操作区 -->
        <div class="header-right">
          <!-- 主题切换 -->
          <el-tooltip :content="effectiveTheme === 'dark' ? '切换到浅色模式' : '切换到深色模式'">
            <button class="icon-btn" @click="toggleTheme">
              <el-icon :size="18">
                <component :is="effectiveTheme === 'dark' ? 'Sunny' : 'Moon'" />
              </el-icon>
            </button>
          </el-tooltip>

          <!-- 用户信息 -->
          <el-dropdown trigger="click" @command="handleCommand">
            <div class="user-info">
              <el-avatar :size="32" class="user-avatar">
                {{ auth.displayName.value.charAt(0) }}
              </el-avatar>
              <div class="user-meta">
                <div class="user-name">{{ auth.displayName.value }}</div>
                <div class="user-role">
                  <el-tag v-if="auth.isAdmin.value" type="danger" size="small" effect="plain">
                    管理员
                  </el-tag>
                  <span v-else class="text-muted">普通用户</span>
                </div>
              </div>
              <el-icon :size="14"><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><User /></el-icon>个人中心
                </el-dropdown-item>
                <el-dropdown-item command="logout" divided>
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <!-- 内容区 -->
      <main class="content">
        <!-- 错误边界：页面组件崩溃时显示错误信息，而不是整页白屏 -->
        <ErrorBoundary>
          <router-view v-slot="{ Component }">
            <transition name="fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </ErrorBoundary>
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--color-bg-page);
}

/* ==================== 侧边栏 ==================== */
.sidebar {
  width: var(--sidebar-width);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--color-bg-card);
  border-right: 1px solid var(--color-border-light);
  transition: width var(--transition-base);
  overflow: hidden;
}

.is-collapsed .sidebar {
  width: var(--sidebar-width-collapsed);
}

.logo {
  height: var(--header-height);
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 0 var(--space-4);
  border-bottom: 1px solid var(--color-border-light);
  flex-shrink: 0;
}

.logo-icon {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-dark));
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}

.logo-text {
  overflow: hidden;
  white-space: nowrap;
}

.logo-title {
  font-size: var(--font-md);
  font-weight: 700;
  color: var(--color-text-primary);
  line-height: 1.2;
}

.logo-sub {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
}

.menu {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-3) var(--space-2);
}

.menu-group-title {
  padding: var(--space-3) var(--space-3) var(--space-2);
  font-size: var(--font-xs);
  font-weight: 600;
  color: var(--color-text-placeholder);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: 42px;
  padding: 0 var(--space-3);
  margin-bottom: var(--space-1);
  border-radius: var(--radius-md);
  color: var(--color-text-regular);
  font-size: var(--font-base);
  white-space: nowrap;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.menu-item:hover {
  background: var(--color-bg-hover);
  color: var(--color-primary);
}

.menu-item.active {
  background: var(--color-primary-pale);
  color: var(--color-primary);
  font-weight: 600;
}

.menu-label {
  overflow: hidden;
}

.sidebar-footer {
  padding: var(--space-2);
  border-top: 1px solid var(--color-border-light);
  flex-shrink: 0;
}

.collapse-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  height: 38px;
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
  font-size: var(--font-sm);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.collapse-btn:hover {
  background: var(--color-bg-hover);
}

/* ==================== 主区域 ==================== */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.header {
  height: var(--header-height);
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--space-6);
  background: var(--color-bg-card);
  border-bottom: 1px solid var(--color-border-light);
}

.header-left {
  min-width: 0;
  overflow: hidden;
}

.header-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-shrink: 0;
}

.icon-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.icon-btn:hover {
  background: var(--color-bg-hover);
  color: var(--color-primary);
}

.user-info {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.user-info:hover {
  background: var(--color-bg-hover);
}

.user-avatar {
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-dark));
  color: #fff;
  font-weight: 600;
  flex-shrink: 0;
}

.user-meta {
  line-height: 1.3;
  text-align: left;
}

.user-name {
  font-size: var(--font-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.user-role {
  font-size: var(--font-xs);
}

.content {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
}

/* ==================== 响应式 ==================== */
@media (max-width: 768px) {
  .header {
    padding: 0 var(--space-4);
  }

  /* 窄屏隐藏面包屑和非关键信息，保证顶栏不换行 */
  .header-left {
    display: none;
  }

  .user-meta {
    display: none;
  }
}
</style>
