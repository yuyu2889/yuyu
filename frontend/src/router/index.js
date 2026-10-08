/**
 * 路由配置。
 *
 * 设计要点（面试可以讲）：
 *
 * 1. **路由级懒加载**
 *    每个页面都用 `() => import(...)` 动态导入。
 *    这样 Vite 会把每个页面打成独立的 chunk，首屏只加载当前页面所需的代码。
 *    对于统计页这种带 ECharts（几百 KB）的页面，收益非常明显 ——
 *    用户如果从不点统计页，就永远不会下载 ECharts。
 *
 * 2. **路由守卫做统一鉴权**
 *    把"要不要登录"、"要不要管理员"声明在路由的 meta 里，
 *    守卫统一处理。而不是在每个页面组件里写 if 判断。
 *    这和后端的 require_admin 依赖是同一个思路：**声明式优于命令式**。
 *
 * 3. **权限在前后端都做**
 *    前端守卫只是"体验优化"（避免用户点了菜单才发现没权限），
 *    **真正的安全边界在后端**。因为前端代码用户完全可以改，
 *    绕过守卫直接调接口。所以后端也必须有校验（本项目有）。
 */
import { createRouter, createWebHistory } from 'vue-router'
import { STORAGE_KEYS } from '@/config/constants'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/LoginView.vue'),
    meta: { title: '登录', public: true, layout: 'blank' },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'Dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '工作台', icon: 'HomeFilled' },
      },
      {
        path: 'equipment',
        name: 'EquipmentList',
        component: () => import('@/views/EquipmentListView.vue'),
        meta: { title: '设备浏览', icon: 'Box' },
      },
      {
        path: 'equipment/:id',
        name: 'EquipmentDetail',
        component: () => import('@/views/EquipmentDetailView.vue'),
        meta: { title: '设备详情', hidden: true }, // 不在侧边栏显示
      },
      {
        path: 'bookings',
        name: 'MyBookings',
        component: () => import('@/views/BookingListView.vue'),
        meta: { title: '我的预约', icon: 'Calendar' },
      },
      {
        path: 'collections',
        name: 'MyCollections',
        component: () => import('@/views/CollectionView.vue'),
        meta: { title: '我的收藏', icon: 'Star' },
      },
      {
        path: 'profile',
        name: 'Profile',
        component: () => import('@/views/ProfileView.vue'),
        meta: { title: '个人中心', icon: 'User' },
      },
      {
        path: 'admin/bookings',
        name: 'AdminBookings',
        component: () => import('@/views/admin/BookingAuditView.vue'),
        meta: { title: '预约审核', icon: 'Checked', requiresAdmin: true },
      },
      {
        path: 'admin/equipment',
        name: 'AdminEquipment',
        component: () => import('@/views/admin/EquipmentManageView.vue'),
        meta: { title: '设备管理', icon: 'Tools', requiresAdmin: true },
      },
      {
        path: 'admin/users',
        name: 'AdminUsers',
        component: () => import('@/views/admin/UserManageView.vue'),
        meta: { title: '用户管理', icon: 'UserFilled', requiresAdmin: true },
      },
      {
        path: 'admin/statistics',
        name: 'AdminStatistics',
        component: () => import('@/views/admin/StatisticsView.vue'),
        meta: { title: '数据统计', icon: 'DataAnalysis', requiresAdmin: true },
      },
    ],
  },
  {
    // 404 兜底：必须放在最后
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: '页面不存在', public: true, layout: 'blank' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  // 切换路由时滚动回顶部（否则从长列表点进详情页会停留在中间位置）
  scrollBehavior(to, from, savedPosition) {
    return savedPosition || { top: 0 }
  },
})

// ---------------------------------------------------------------------------
//  全局前置守卫
// ---------------------------------------------------------------------------
router.beforeEach((to) => {
  // 设置页面标题
  document.title = to.meta.title
    ? `${to.meta.title} · 实验室设备预约系统`
    : '实验室设备预约系统'

  const token = localStorage.getItem(STORAGE_KEYS.TOKEN)
  const isPublic = Boolean(to.meta.public)

  // 已登录用户访问登录页 → 跳转到工作台
  if (to.name === 'Login' && token) {
    return { name: 'Dashboard' }
  }

  // 未登录访问受保护页面 → 跳转登录页，并记住原目标
  if (!isPublic && !token) {
    return {
      name: 'Login',
      query: { redirect: to.fullPath },
    }
  }

  // 需要管理员权限的页面，检查本地缓存的用户信息
  //
  // ⚠️ 注意：这里只是"体验优化"，不是安全措施。
  // 因为 localStorage 里的用户信息是用户可以自己改的
  // （打开开发者工具改成 is_admin: true 就能进管理页面的 UI）。
  // 但没关系 —— 他进去之后所有管理接口都会被后端 403 拒绝。
  // **安全边界必须由后端守住**，这是基本原则。
  if (to.meta.requiresAdmin) {
    const userRaw = localStorage.getItem(STORAGE_KEYS.USER)
    let isAdmin = false
    try {
      isAdmin = JSON.parse(userRaw || '{}')?.is_admin === true
    } catch {
      isAdmin = false
    }
    if (!isAdmin) {
      return { name: 'Dashboard' }
    }
  }

  return true
})

export default router
