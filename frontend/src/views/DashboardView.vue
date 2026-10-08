<script setup>
/**
 * 工作台（首页）。
 *
 * 设计要点：
 * 1. **只发一个请求**：所有统计数字由后端 `/statistics/dashboard` 聚合返回。
 *    原项目首页要发 5 个请求（用户信息、待审核数、收藏数、周预约数、可用设备数），
 *    这里用聚合接口把首屏网络往返从 5 次降到 2 次（统计 + 最近预约）。
 *
 * 2. **按角色显示不同内容**：普通用户看到的是"我的"数据；
 *    管理员额外看到全平台待审核数和用户总数。
 *    后端已经做了权限隔离（普通用户拿到的全局字段是 0）。
 *
 * 3. **加载态用骨架屏**，避免"0 → 真实值"的跳动。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import statisticsApi from '@/api/statistics'
import bookingApi from '@/api/booking'
import { formatDate, formatTime, formatRelativeTime } from '@/utils/format'
import { getBookingStatus } from '@/config/constants'
import StatCard from '@/components/common/StatCard.vue'
import EquipmentImage from '@/components/common/EquipmentImage.vue'

const router = useRouter()
const auth = useAuthStore()

const loading = ref(true)
const stats = ref({})
const recentBookings = ref([])
const recentLoading = ref(true)
const popularEquipment = ref([])

// ---------------------------------------------------------------------------
//  问候语与日期（根据当前时间动态显示）
// ---------------------------------------------------------------------------
const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了'
  if (hour < 12) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

const todayText = computed(() => {
  const d = new Date()
  const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 ${weekdays[d.getDay()]}`
})

// ---------------------------------------------------------------------------
//  统计卡片配置
// ---------------------------------------------------------------------------
//  用 computed 而不是直接在模板里写一堆 div，配置化后增删卡片很容易。
const statCards = computed(() => {
  const s = stats.value
  const cards = [
    {
      key: 'pending',
      label: auth.isAdmin.value ? '全平台待审核' : '我的待审核预约',
      value: auth.isAdmin.value ? (s.all_pending_bookings ?? 0) : (s.my_pending_bookings ?? 0),
      icon: 'Clock',
      color: 'linear-gradient(135deg, #e6a23c, #d48806)',
      trend: auth.isAdmin.value ? '需要尽快处理' : '等待管理员审核',
      clickable: true,
      to: auth.isAdmin.value ? '/admin/bookings' : '/bookings?status=pending',
    },
    {
      key: 'bookings',
      label: '我的预约总数',
      value: s.my_total_bookings ?? 0,
      icon: 'Calendar',
      color: 'linear-gradient(135deg, #409eff, #337ecc)',
      trend: '含全部状态',
      clickable: true,
      to: '/bookings',
    },
    {
      key: 'collections',
      label: '我的收藏',
      value: s.my_collections ?? 0,
      icon: 'Star',
      color: 'linear-gradient(135deg, #f56c6c, #d94a4a)',
      trend: '收藏的设备',
      clickable: true,
      to: '/collections',
    },
    {
      key: 'available',
      label: '当前可用设备',
      value: s.available_equipment ?? 0,
      icon: 'Box',
      color: 'linear-gradient(135deg, #67c23a, #4e9a2f)',
      trend: `共 ${s.total_equipment ?? 0} 台设备`,
      clickable: true,
      to: '/equipment',
    },
    {
      key: 'weekly',
      label: '近 7 天完成',
      value: s.weekly_completed ?? 0,
      icon: 'CircleCheck',
      color: 'linear-gradient(135deg, #909399, #6b7280)',
      trend: auth.isAdmin.value ? '全平台' : '我的使用记录',
      clickable: false,
    },
  ]

  // 管理员额外显示用户总数
  if (auth.isAdmin.value) {
    cards.push({
      key: 'users',
      label: '系统用户数',
      value: s.total_users ?? 0,
      icon: 'UserFilled',
      color: 'linear-gradient(135deg, #8b5cf6, #6d28d9)',
      trend: '启用状态的用户',
      clickable: true,
      to: '/admin/users',
    })
  }

  return cards
})

// ---------------------------------------------------------------------------
//  快捷入口
// ---------------------------------------------------------------------------
const quickActions = computed(() => {
  const actions = [
    { label: '浏览设备', desc: '查看全部设备并预约', icon: 'Search', color: '#409eff', to: '/equipment' },
    { label: '我的预约', desc: '查看预约状态与进度', icon: 'Calendar', color: '#67c23a', to: '/bookings' },
    { label: '我的收藏', desc: '快速找到常用设备', icon: 'Star', color: '#e6a23c', to: '/collections' },
    { label: '个人中心', desc: '修改资料与密码', icon: 'User', color: '#909399', to: '/profile' },
  ]

  if (auth.isAdmin.value) {
    actions.push(
      { label: '预约审核', desc: '处理待审核申请', icon: 'Checked', color: '#f56c6c', to: '/admin/bookings' },
      { label: '数据统计', desc: '查看运营数据', icon: 'DataAnalysis', color: '#8b5cf6', to: '/admin/statistics' },
    )
  }

  return actions
})

// ---------------------------------------------------------------------------
//  数据加载
// ---------------------------------------------------------------------------
async function loadStats() {
  loading.value = true
  try {
    stats.value = await statisticsApi.getDashboard()
  } catch {
    stats.value = {}
  } finally {
    loading.value = false
  }
}

async function loadRecentBookings() {
  recentLoading.value = true
  try {
    // 管理员看全平台，普通用户看自己的
    const fetcher = auth.isAdmin.value
      ? bookingApi.listAllBookings
      : bookingApi.listMyBookings
    const data = await fetcher({ page: 1, page_size: 5 })
    recentBookings.value = data?.list || []
  } catch {
    recentBookings.value = []
  } finally {
    recentLoading.value = false
  }
}

async function loadPopularEquipment() {
  try {
    // 普通用户没有统计接口权限，改用设备列表按浏览量排序
    const data = await (await import('@/api/equipment')).default.listEquipments({
      page: 1,
      page_size: 4,
      order_by: 'browse_count',
    })
    popularEquipment.value = data?.list || []
  } catch {
    popularEquipment.value = []
  }
}

function goTo(path) {
  if (path) router.push(path)
}

onMounted(() => {
  loadStats()
  loadRecentBookings()
  loadPopularEquipment()
})
</script>

<template>
  <div class="page-container">
    <!-- ==================== 欢迎栏 ==================== -->
    <div class="welcome card">
      <div class="welcome-left">
        <el-avatar :size="52" class="welcome-avatar">
          {{ auth.displayName.value.charAt(0) }}
        </el-avatar>
        <div>
          <h2 class="welcome-title">
            {{ greeting }}，{{ auth.displayName.value }}
          </h2>
          <p class="welcome-sub">
            <el-tag v-if="auth.isAdmin.value" type="danger" size="small" effect="dark">
              管理员
            </el-tag>
            <el-tag v-else type="primary" size="small" effect="plain">普通用户</el-tag>
            <span class="welcome-date">{{ todayText }}</span>
          </p>
        </div>
      </div>
    </div>

    <!-- ==================== 统计卡片 ==================== -->
    <div class="stats-grid">
      <StatCard
        v-for="card in statCards"
        :key="card.key"
        :label="card.label"
        :value="card.value"
        :icon="card.icon"
        :color="card.color"
        :trend="card.trend"
        :clickable="card.clickable"
        :loading="loading"
        @click="goTo(card.to)"
      />
    </div>

    <!-- ==================== 快捷入口 ==================== -->
    <div class="section card">
      <div class="section-header">
        <h3 class="section-title">
          <el-icon><Menu /></el-icon>
          快捷入口
        </h3>
      </div>
      <div class="actions-grid">
        <div
          v-for="action in quickActions"
          :key="action.label"
          class="action-item"
          @click="goTo(action.to)"
        >
          <div class="action-icon" :style="{ background: action.color }">
            <el-icon :size="20" color="#fff"><component :is="action.icon" /></el-icon>
          </div>
          <div class="action-text">
            <div class="action-label">{{ action.label }}</div>
            <div class="action-desc">{{ action.desc }}</div>
          </div>
          <el-icon class="action-arrow"><ArrowRight /></el-icon>
        </div>
      </div>
    </div>

    <div class="two-column">
      <!-- ==================== 最近预约 ==================== -->
      <div class="section card">
        <div class="section-header">
          <h3 class="section-title">
            <el-icon><Calendar /></el-icon>
            最近预约
          </h3>
          <el-button link type="primary" @click="goTo('/bookings')">
            查看全部<el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>

        <el-skeleton v-if="recentLoading" :rows="4" animated />

        <el-empty v-else-if="recentBookings.length === 0" description="暂无预约记录" :image-size="70">
          <el-button type="primary" @click="goTo('/equipment')">去预约设备</el-button>
        </el-empty>

        <div v-else class="booking-list">
          <div
            v-for="booking in recentBookings"
            :key="booking.id"
            class="booking-item"
            @click="goTo('/bookings')"
          >
            <div class="booking-time">
              <div class="booking-date">{{ formatDate(booking.booking_date, 'short') }}</div>
              <div class="booking-hour">
                {{ formatTime(booking.start_time) }}
              </div>
            </div>
            <div class="booking-info">
              <div class="booking-name">{{ booking.equipment_name || '未知设备' }}</div>
              <div class="booking-purpose">{{ booking.purpose }}</div>
            </div>
            <el-tag :type="getBookingStatus(booking.status).type" size="small" effect="light">
              {{ getBookingStatus(booking.status).label }}
            </el-tag>
          </div>
        </div>
      </div>

      <!-- ==================== 热门设备 ==================== -->
      <div class="section card">
        <div class="section-header">
          <h3 class="section-title">
            <el-icon><TrendCharts /></el-icon>
            热门设备
          </h3>
          <el-button link type="primary" @click="goTo('/equipment')">
            查看全部<el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>

        <el-empty
          v-if="popularEquipment.length === 0"
          description="暂无设备数据"
          :image-size="70"
        />

        <div v-else class="equipment-list">
          <div
            v-for="item in popularEquipment"
            :key="item.id"
            class="equipment-item"
            @click="goTo(`/equipment/${item.id}`)"
          >
            <EquipmentImage
              :src="item.image"
              :name="item.name"
              height="56px"
              radius="var(--radius-sm)"
              class="equipment-thumb"
            />
            <div class="equipment-info">
              <div class="equipment-name">{{ item.name }}</div>
              <div class="equipment-meta">
                <el-tag size="small" effect="plain" type="info">
                  {{ item.category_name || '未分类' }}
                </el-tag>
                <span class="text-muted">
                  <el-icon><View /></el-icon> {{ item.browse_count || 0 }}
                </span>
              </div>
            </div>
            <el-tag
              :type="item.status === 'available' ? 'success' : 'warning'"
              size="small"
            >
              {{ item.status === 'available' ? '可用' : '占用中' }}
            </el-tag>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ==================== 欢迎栏 ==================== */
.welcome {
  padding: var(--space-5) var(--space-6);
  margin-bottom: var(--space-5);
}

.welcome-left {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}

.welcome-avatar {
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-dark));
  color: #fff;
  font-size: var(--font-xl);
  font-weight: 700;
  flex-shrink: 0;
}

.welcome-title {
  font-size: var(--font-xl);
  font-weight: 700;
  margin-bottom: var(--space-1);
}

.welcome-sub {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
}

.welcome-date {
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}

/* ==================== 统计卡片网格 ==================== */
.stats-grid {
  display: grid;
  /* auto-fit + minmax 实现自适应列数：
     宽屏一行放 3-4 个，窄屏自动降为 1-2 个，不用写媒体查询 */
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}

/* ==================== 区块通用 ==================== */
.section {
  padding: var(--space-5);
  margin-bottom: var(--space-5);
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-4);
}

.section-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-md);
  font-weight: 600;
}

/* ==================== 快捷入口 ==================== */
.actions-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--space-3);
}

.action-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: border-color var(--transition-fast), background var(--transition-fast),
    transform var(--transition-fast);
}

.action-item:hover {
  border-color: var(--color-primary);
  background: var(--color-primary-pale);
  transform: translateY(-1px);
}

.action-icon {
  width: 40px;
  height: 40px;
  flex-shrink: 0;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
}

.action-text {
  flex: 1;
  min-width: 0;
}

.action-label {
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text-primary);
}

.action-desc {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.action-arrow {
  color: var(--color-text-placeholder);
  flex-shrink: 0;
}

/* ==================== 两栏布局 ==================== */
.two-column {
  display: grid;
  grid-template-columns: 1.3fr 1fr;
  gap: var(--space-5);
}

/* ==================== 最近预约列表 ==================== */
.booking-list {
  display: flex;
  flex-direction: column;
}

.booking-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-2);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.booking-item:hover {
  background: var(--color-bg-hover);
}

.booking-item + .booking-item {
  border-top: 1px solid var(--color-border-light);
}

.booking-time {
  width: 52px;
  flex-shrink: 0;
  text-align: center;
}

.booking-date {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
}

.booking-hour {
  font-size: var(--font-base);
  font-weight: 700;
  color: var(--color-primary);
  font-variant-numeric: tabular-nums;
}

.booking-info {
  flex: 1;
  min-width: 0;
}

.booking-name {
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.booking-purpose {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ==================== 热门设备列表 ==================== */
.equipment-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.equipment-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.equipment-item:hover {
  background: var(--color-bg-hover);
}

.equipment-thumb {
  width: 56px;
  flex-shrink: 0;
}

.equipment-info {
  flex: 1;
  min-width: 0;
}

.equipment-name {
  font-size: var(--font-base);
  font-weight: 600;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.equipment-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: 2px;
  font-size: var(--font-xs);
}

.equipment-meta .text-muted {
  display: flex;
  align-items: center;
  gap: 2px;
}

/* ==================== 响应式 ==================== */
@media (max-width: 1100px) {
  .two-column {
    grid-template-columns: 1fr;
  }
}
</style>
