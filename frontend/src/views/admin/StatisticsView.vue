<script setup>
/**
 * 数据统计页（管理员）。
 *
 * ============================================================================
 *  关于 ECharts 的使用要点（面试可以讲）：
 * ============================================================================
 *  1. **按需引入而不是全量引入**
 *     全量 import * as echarts 会把整个库（约 1MB）打进 bundle。
 *     这里只引入需要的图表类型和组件（饼图、折线图、柱状图 + 提示框等），
 *     体积能减少一大半。
 *
 *  2. **图表实例必须手动销毁**
 *     ECharts 实例持有 canvas 和事件监听，如果不销毁会导致内存泄漏。
 *     所以在 onUnmounted 里要 dispose，并且在数据更新时复用实例
 *     而不是反复 create（反复 create 会累积泄漏）。
 *
 *  3. **窗口 resize 时要调 chart.resize()**
 *     否则浏览器窗口变化后图表还是旧尺寸，显示会错位。
 *     这里用一个统一的 resize 处理器管理所有图表实例。
 *
 *  4. **深色模式适配**
 *     图表颜色不跟随 CSS 变量，所以主题切换时需要重新 setOption
 *     把文字颜色换成深色模式合适的值。
 * ============================================================================
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

// ============================================================================
//  ECharts 导入方式说明（这里踩过一个坑，记录下来）
// ============================================================================
//  最初我用的是「按需导入」（tree-shaking 友好）：
//      import * as echarts from 'echarts/core'
//      import { BarChart, LineChart, PieChart } from 'echarts/charts'
//      import { GridComponent, ... } from 'echarts/components'
//      import { CanvasRenderer } from 'echarts/renderers'
//      echarts.use([...])
//
//  但运行时抛了一个很难查的错误：
//      TypeError: registers.registerChartView is not a function
//
//  排查过程：
//    - echarts 5.6.0 / zrender 5.6.1，只有一个副本，版本匹配 ✅
//    - 在 Node 里直接跑分层导入 + 注册，完全正常 ✅
//    - 生产构建产物里 zrender 和 echarts 在同一个 chunk ✅
//    → 结论：是 dev 模式下 ESM 模块解析导致多个子路径拿到了不同的模块实例，
//      使得 charts 包里的注册函数拿到的 registers 对象不完整。
//
//  最终选择：**全量导入**。
//  权衡：全量 echarts 约 1MB，但 StatisticsView 是路由级懒加载的，
//        只有管理员打开统计页时才下载这一个 chunk（已单独分包），
//        对首屏和其他页面零影响。
//        用 ~200KB 的体积换掉了整类"模块实例不一致"的疑难问题，值得。
//
//  这也是一个实用的工程判断：**当某个优化带来的复杂度远超收益时，退回到简单方案**。
// ============================================================================
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'

import statisticsApi from '@/api/statistics'
import { useTheme } from '@/composables/useTheme'
import PageHeader from '@/components/common/PageHeader.vue'
import StatCard from '@/components/common/StatCard.vue'

// 说明：全量导入的 echarts 已经包含了饼图/折线图/柱状图和所有基础组件，
// 不需要再调用 echarts.use([...]) 手动注册。
// （按需导入才需要注册那一堆组件，见上面注释里说明的原因）

const { effectiveTheme } = useTheme()

// ---------------------------------------------------------------------------
//  数据
// ---------------------------------------------------------------------------
const loading = ref(true)
const userStats = ref({})
const equipmentStats = ref({})
// 设备状态分布是**独立接口**返回的，不能从 equipmentStats 里取。
// 这是个踩过的坑：我第一版从 equipmentStats.value.status_distribution 取数据，
// 但 /statistics/equipment 接口并不返回这个字段（它在 /statistics/equipment/status 里），
// 结果拿到 undefined → 数组为空 → 图表直接 return 不渲染。
const statusStats = ref({})
const bookingStats = ref({})

// 设备明细表的排序状态（服务端排序，见模板里的说明）
const rankingLimit = ref(50) // 取全部设备（本项目共 15 台，留足余量）
const rankingOrderBy = ref('browse_count') // 默认按浏览量排序
const trendDays = ref(7)

// 图表容器 ref
const statusChartRef = ref(null)
const trendChartRef = ref(null)
const rankChartRef = ref(null)

// 图表实例（保存起来以便更新和销毁）
const charts = {
  status: null,
  trend: null,
  rank: null,
}

// ---------------------------------------------------------------------------
//  深色模式下的图表配色
// ---------------------------------------------------------------------------
const chartTheme = computed(() => {
  const isDark = effectiveTheme.value === 'dark'
  return {
    text: isDark ? '#d1d5db' : '#4b5563',
    subText: isDark ? '#9ca3af' : '#6b7280',
    axisLine: isDark ? '#374151' : '#e5e7eb',
    splitLine: isDark ? '#2d3748' : '#f3f4f6',
    tooltipBg: isDark ? '#1f2937' : '#ffffff',
    tooltipBorder: isDark ? '#374151' : '#e5e7eb',
    tooltipText: isDark ? '#e5e7eb' : '#1f2937',
  }
})

// 状态配色（与列表页的状态标签保持一致）
const STATUS_COLORS = {
  available: '#67c23a',
  busy: '#e6a23c',
  maintenance: '#f56c6c',
}

const BOOKING_STATUS_COLORS = {
  pending: '#e6a23c',
  approved: '#67c23a',
  completed: '#409eff',
  rejected: '#f56c6c',
  cancelled: '#909399',
}

// ---------------------------------------------------------------------------
//  图表初始化/更新
// ---------------------------------------------------------------------------

/** 设备状态分布（饼图） */
function renderStatusChart() {
  if (!statusChartRef.value) return
  // 数据来自 /statistics/equipment/status 接口（见上面 statusStats 的注释）
  const data = statusStats.value.status_distribution || []
  if (!data.length) return

  if (!charts.status) {
    charts.status = echarts.init(statusChartRef.value)
  }

  const t = chartTheme.value
  charts.status.setOption({
    tooltip: {
      trigger: 'item',
      backgroundColor: t.tooltipBg,
      borderColor: t.tooltipBorder,
      textStyle: { color: t.tooltipText },
      // {b} 名称 {c} 数值 {d} 百分比
      formatter: '{b}<br/>{c} 台（{d}%）',
    },
    legend: {
      bottom: 0,
      textStyle: { color: t.text },
      icon: 'circle',
    },
    series: [
      {
        type: 'pie',
        // 环形图比实心饼图更现代，中间还能放总数
        radius: ['45%', '68%'],
        center: ['50%', '45%'],
        avoidLabelOverlap: true,
        itemStyle: {
          borderRadius: 6,
          borderColor: 'transparent',
          borderWidth: 2,
        },
        label: {
          show: true,
          formatter: '{b}\n{c} 台',
          color: t.text,
          fontSize: 12,
        },
        labelLine: { length: 10, length2: 10 },
        data: data.map((item) => ({
          name: item.status_name,
          value: item.count,
          itemStyle: { color: STATUS_COLORS[item.status] || '#909399' },
        })),
      },
    ],
  })
}

/** 预约趋势（折线图） */
function renderTrendChart(trendData) {
  if (!trendChartRef.value) return
  const data = trendData?.daily_bookings || []
  if (!data.length) return

  if (!charts.trend) {
    charts.trend = echarts.init(trendChartRef.value)
  }

  const t = chartTheme.value
  charts.trend.setOption({
    tooltip: {
      trigger: 'axis',
      backgroundColor: t.tooltipBg,
      borderColor: t.tooltipBorder,
      textStyle: { color: t.tooltipText },
    },
    grid: { left: 40, right: 20, top: 30, bottom: 30 },
    xAxis: {
      type: 'category',
      data: data.map((item) => item.label),
      axisLine: { lineStyle: { color: t.axisLine } },
      axisLabel: { color: t.subText },
    },
    yAxis: {
      type: 'value',
      minInterval: 1, // 预约数必须是整数，避免出现 0.5 这种刻度
      axisLine: { show: false },
      axisLabel: { color: t.subText },
      splitLine: { lineStyle: { color: t.splitLine } },
    },
    series: [
      {
        type: 'line',
        data: data.map((item) => item.booking_count),
        smooth: true,
        symbol: 'circle',
        symbolSize: 7,
        lineStyle: { width: 3, color: '#409eff' },
        itemStyle: { color: '#409eff' },
        // 面积渐变填充，视觉上更有层次
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(64, 158, 255, 0.28)' },
            { offset: 1, color: 'rgba(64, 158, 255, 0.02)' },
          ]),
        },
      },
    ],
  })
}

/** 设备排行（横向柱状图） */
function renderRankChart() {
  if (!rankChartRef.value) return
  // 取前 8 个做图表，太多会挤在一起
  const ranking = (equipmentStats.value.equipment_ranking || []).slice(0, 8)
  if (!ranking.length) return

  if (!charts.rank) {
    charts.rank = echarts.init(rankChartRef.value)
  }

  const t = chartTheme.value
  // 横向柱状图的 y 轴是从下往上的，所以要反转数据顺序
  const reversed = [...ranking].reverse()

  charts.rank.setOption({
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: t.tooltipBg,
      borderColor: t.tooltipBorder,
      textStyle: { color: t.tooltipText },
    },
    grid: { left: 110, right: 40, top: 20, bottom: 20 },
    xAxis: {
      type: 'value',
      axisLine: { show: false },
      axisLabel: { color: t.subText },
      splitLine: { lineStyle: { color: t.splitLine } },
    },
    yAxis: {
      type: 'category',
      data: reversed.map((item) => item.equipment_name),
      axisLine: { lineStyle: { color: t.axisLine } },
      axisLabel: { color: t.text, fontSize: 12 },
    },
    series: [
      {
        type: 'bar',
        data: reversed.map((item) => item.browse_count),
        barWidth: '55%',
        itemStyle: {
          borderRadius: [0, 4, 4, 0],
          color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
            { offset: 0, color: '#66b1ff' },
            { offset: 1, color: '#409eff' },
          ]),
        },
        label: {
          show: true,
          position: 'right',
          color: t.subText,
          fontSize: 12,
        },
      },
    ],
  })
}

// ---------------------------------------------------------------------------
//  数据加载
// ---------------------------------------------------------------------------
async function loadAll() {
  loading.value = true
  try {
    // 注意：设备状态分布是**单独的接口**（/statistics/equipment/status），
    // 它不在 getEquipmentStatistics 的返回里 —— 这是踩过的坑。
    const [users, equipments, statuses, bookings, trend] = await Promise.allSettled([
      statisticsApi.getUserStatistics(),
      statisticsApi.getEquipmentStatistics({ limit: rankingLimit.value, order_by: rankingOrderBy.value }),
      statisticsApi.getEquipmentStatusDistribution(),
      statisticsApi.getBookingStatistics(),
      statisticsApi.getBookingTrend(trendDays.value),
    ])

    if (users.status === 'fulfilled') userStats.value = users.value || {}
    if (equipments.status === 'fulfilled') equipmentStats.value = equipments.value || {}
    if (statuses.status === 'fulfilled') statusStats.value = statuses.value || {}
    if (bookings.status === 'fulfilled') bookingStats.value = bookings.value || {}
    // 趋势数据缓存下来（主题切换重绘时需要），避免再请求一次接口
    if (trend.status === 'fulfilled') trendCache.value = trend.value

    // 诊断：哪个接口失败了就在控制台明确指出（而不是静默显示空白图表）
    for (const [name, result] of [
      ['用户统计', users],
      ['设备统计', equipments],
      ['设备状态分布', statuses],
      ['预约统计', bookings],
      ['预约趋势', trend],
    ]) {
      if (result.status === 'rejected') {
        console.error(`[StatisticsView] ${name}接口请求失败:`, result.reason)
      }
    }

    // 等 DOM 更新后再渲染图表（容器必须有尺寸，否则 ECharts 初始化会异常）
    await nextTick()
    renderAllCharts()
  } finally {
    loading.value = false
  }
}

/**
 * 统一渲染全部三个图表，并捕获异常。
 *
 * 为什么要 try/catch？
 * 因为图表渲染发生在 onMounted 的异步回调里，
 * 如果抛异常，Vue 不会把它显示到界面上，只会打一条控制台错误 ——
 * 用户看到的是"页面能打开但图表区一片空白"，完全不知道为什么。
 * 捕获后把错误写进 chartError，界面上就能看到原因了。
 */
function renderAllCharts() {
  chartError.value = ''

  // 先检查数据是否到位 —— 这样"图表空白"能立刻看出是数据问题还是渲染问题。
  // 上一版没有这个检查，导致"接口没被调用 → 数据为空 → 图表静默不渲染"，
  // 表现就是图表区一片空白且没有任何提示，很难排查。
  const missing = []
  if (!statusStats.value.status_distribution?.length) missing.push('设备状态分布')
  if (!equipmentStats.value.equipment_ranking?.length) missing.push('设备排行')
  if (!trendCache.value?.daily_bookings?.length) missing.push('预约趋势')
  if (missing.length) {
    chartError.value = `以下图表缺少数据：${missing.join('、')}。可能是对应接口请求失败，请按 F12 查看控制台。`
  }

  const tasks = [
    { name: '设备状态分布', fn: renderStatusChart },
    { name: '设备排行', fn: renderRankChart },
    { name: '预约趋势', fn: () => trendCache.value && renderTrendChart(trendCache.value) },
  ]
  for (const task of tasks) {
    try {
      task.fn()
    } catch (err) {
      console.error(`[StatisticsView] ${task.name} 图表渲染失败:`, err)
      chartError.value = `${task.name}图表渲染失败：${err.message}`
    }
  }
}

async function handleTrendDaysChange(days) {
  trendDays.value = days
  try {
    const trend = await statisticsApi.getBookingTrend(days)
    trendCache.value = trend
    renderTrendChart(trend)
  } catch {
    // 拦截器已提示
  }
}

/**
 * 设备明细表排序（服务端排序）。
 *
 * el-table 的 sort-change 事件会传 { column, prop, order }：
 *   - prop：列上声明的字段名（这就是为什么 prop 必须写）
 *   - order：'ascending' | 'descending' | null（null 表示取消排序）
 *
 * 注意 order_by 只支持 browse_count / booking_count / collect_count
 * （后端用正则约束了，传别的值会返回 422），所以这里只处理这三个。
 */
const ALLOWED_ORDER_BY = ['browse_count', 'booking_count', 'collect_count']

async function handleSortChange({ prop, order }) {
  if (!prop || !ALLOWED_ORDER_BY.includes(prop)) return

  rankingOrderBy.value = prop

  try {
    const data = await statisticsApi.getEquipmentStatistics({
      limit: rankingLimit.value,
      order_by: prop,
    })
    equipmentStats.value = data || {}
  } catch {
    // 拦截器已提示
  }

  // 说明：后端的 order_by 只支持"降序取 Top N"，升序在排行榜语义下意义不大
  // （"浏览量最少的设备"不是常见的运营需求）。
  // 如果用户点了升序，这里给一个提示，避免他以为排序坏了。
  if (order === 'ascending') {
    ElMessage.info('排行榜按降序展示：这里显示的是数值最高的前若干台设备')
  }
}

// ---------------------------------------------------------------------------
//  窗口 resize 处理
// ---------------------------------------------------------------------------
//  所有图表共用一个 resize 处理器，避免注册多个监听器
function handleResize() {
  Object.values(charts).forEach((chart) => chart?.resize())
}

// 缓存趋势数据，供主题切换时重新渲染用
// 说明：themeCache 必须声明在 watch 之前使用的位置能访问到，
// 所以这里放在 watch 定义之前更清晰（原来放在了之后，容易误读）。
const trendCache = ref(null)

// 图表初始化失败时的提示（避免静默白屏）
const chartError = ref('')

// 主题切换时重新渲染（因为图表颜色不跟随 CSS 变量）
//
// 注意这里的一个坑（第一版写错了）：
// 原来写成 renderTrendChart({ daily_bookings: bookingStats.value.trend_data || ... })
// 但 bookingStats 里根本没有 trend_data 字段，所以会传一个空数组进去，
// renderTrendChart 内部 `if (!data.length) return` 直接返回 ——
// 结果是主题切换后折线图不重绘、其他两个图表也因为这个异步回调抛错而没执行。
// 正确做法是把「完整的趋势响应」缓存下来，原样传回去。
watch(effectiveTheme, async () => {
  await nextTick()
  try {
    renderStatusChart()
    renderRankChart()
    if (trendCache.value) {
      renderTrendChart(trendCache.value)
    }
  } catch (err) {
    console.error('[StatisticsView] 主题切换重绘图表失败:', err)
  }
})

onMounted(async () => {
  // loadAll 里已经把趋势数据一起请求并缓存了，
  // 所以这里不需要再单独请求一次（第一版重复请求了，浪费一次往返）
  await loadAll()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  // 关键：销毁图表实例，避免内存泄漏
  Object.keys(charts).forEach((key) => {
    charts[key]?.dispose()
    charts[key] = null
  })
})
</script>

<template>
  <div class="page-container">
    <PageHeader title="数据统计" description="系统运营数据概览与可视化分析">
      <template #actions>
        <el-button @click="loadAll">
          <el-icon><Refresh /></el-icon>刷新数据
        </el-button>
      </template>
    </PageHeader>

    <!-- 图表渲染失败时的提示
         为什么要加这个？因为图表渲染在异步回调里，抛异常时 Vue 不会显示到界面上，
         用户只看到"图表区域一片空白"，完全不知道为什么。
         加上这个提示后，出问题能直接看到原因。 -->
    <el-alert
      v-if="chartError"
      type="error"
      :closable="false"
      show-icon
      :title="chartError"
      description="图表渲染失败，但页面其他数据仍然可用。请按 F12 打开控制台查看详细错误信息。"
      class="chart-error"
    />

    <!-- ==================== 顶部指标卡片 ==================== -->
    <div class="stats-grid">
      <StatCard
        label="用户总数"
        :value="userStats.total_users ?? 0"
        icon="UserFilled"
        color="linear-gradient(135deg, #8b5cf6, #6d28d9)"
        :trend="`近7天新增 ${userStats.new_users_7d ?? 0}`"
        :loading="loading"
      />
      <StatCard
        label="设备总数"
        :value="equipmentStats.total_equipment_count ?? 0"
        icon="Box"
        color="linear-gradient(135deg, #409eff, #337ecc)"
        :trend="`可用 ${statusStats.status_distribution?.find((s) => s.status === 'available')?.count ?? 0} 台`"
        :loading="loading"
      />
      <StatCard
        label="预约总数"
        :value="bookingStats.total_bookings ?? 0"
        icon="Calendar"
        color="linear-gradient(135deg, #67c23a, #4e9a2f)"
        :trend="`待审核 ${bookingStats.pending_count ?? 0} 条`"
        :loading="loading"
      />
      <StatCard
        label="累计浏览量"
        :value="equipmentStats.total_browse_count ?? 0"
        icon="View"
        color="linear-gradient(135deg, #e6a23c, #d48806)"
        trend="设备被查看总次数"
        :loading="loading"
      />
      <StatCard
        label="累计预约次数"
        :value="equipmentStats.total_booking_count ?? 0"
        icon="CircleCheck"
        color="linear-gradient(135deg, #f56c6c, #d94a4a)"
        trend="已完成的预约"
        :loading="loading"
      />
      <StatCard
        label="收藏总数"
        :value="equipmentStats.total_collect_count ?? 0"
        icon="Star"
        color="linear-gradient(135deg, #14b8a6, #0d9488)"
        trend="用户收藏记录"
        :loading="loading"
      />
    </div>

    <!-- ==================== 图表区第一行 ==================== -->
    <div class="chart-row">
      <!-- 设备状态分布 -->
      <div class="chart-card card">
        <div class="chart-header">
          <h3 class="chart-title">
            <el-icon><PieChart /></el-icon>设备状态分布
          </h3>
          <span class="chart-sub">共 {{ equipmentStats.total_equipment_count ?? 0 }} 台设备</span>
        </div>
        <div ref="statusChartRef" v-loading="loading" class="chart-body" />

        <!-- 数据明细表（图表之外再给一份精确数字） -->
        <div class="status-legend">
          <div
            v-for="item in statusStats.status_distribution || []"
            :key="item.status"
            class="legend-item"
          >
            <span class="legend-dot" :style="{ background: STATUS_COLORS[item.status] }" />
            <span class="legend-name">{{ item.status_name }}</span>
            <span class="legend-value">{{ item.count }} 台</span>
            <span class="legend-percent">{{ item.percentage }}%</span>
          </div>
        </div>
      </div>

      <!-- 预约趋势 -->
      <div class="chart-card card">
        <div class="chart-header">
          <h3 class="chart-title">
            <el-icon><TrendCharts /></el-icon>预约趋势
          </h3>
          <el-radio-group
            :model-value="trendDays"
            size="small"
            @update:model-value="handleTrendDaysChange"
          >
            <el-radio-button :value="7">7天</el-radio-button>
            <el-radio-button :value="14">14天</el-radio-button>
            <el-radio-button :value="30">30天</el-radio-button>
          </el-radio-group>
        </div>
        <div ref="trendChartRef" v-loading="loading" class="chart-body" />
        <div class="chart-footer">
          <el-tooltip content="按预约的提交日期分组统计，反映每天的预约需求变化">
            <span class="text-muted">
              <el-icon><InfoFilled /></el-icon>
              口径：按预约提交日期统计
            </span>
          </el-tooltip>
        </div>
      </div>
    </div>

    <!-- ==================== 图表区第二行 ==================== -->
    <div class="chart-row">
      <!-- 设备排行 -->
      <div class="chart-card card">
        <div class="chart-header">
          <h3 class="chart-title">
            <el-icon><Histogram /></el-icon>设备浏览量排行（Top 8）
          </h3>
        </div>
        <div ref="rankChartRef" v-loading="loading" class="chart-body chart-body-tall" />
      </div>

      <!-- 预约状态分布 -->
      <div class="chart-card card">
        <div class="chart-header">
          <h3 class="chart-title">
            <el-icon><DataAnalysis /></el-icon>预约状态分布
          </h3>
        </div>
        <div class="booking-status-list">
          <div
            v-for="item in bookingStats.status_distribution || []"
            :key="item.status"
            class="booking-status-item"
          >
            <div class="status-info">
              <span
                class="status-dot"
                :style="{ background: BOOKING_STATUS_COLORS[item.status] }"
              />
              <span class="status-name">{{ item.status_name }}</span>
            </div>
            <div class="status-bar-wrapper">
              <div
                class="status-bar"
                :style="{
                  width: `${item.percentage}%`,
                  background: BOOKING_STATUS_COLORS[item.status],
                }"
              />
            </div>
            <div class="status-numbers">
              <span class="status-count">{{ item.count }}</span>
              <span class="status-percent">{{ item.percentage }}%</span>
            </div>
          </div>
        </div>
        <div class="chart-footer">
          <span class="text-muted">今日预约：{{ bookingStats.today_bookings ?? 0 }} 条</span>
        </div>
      </div>
    </div>

    <!-- ==================== 设备明细表 ==================== -->
    <div class="card table-card">
      <div class="chart-header">
        <h3 class="chart-title">
          <el-icon><List /></el-icon>设备数据明细
        </h3>
        <span class="chart-sub">
          点击「浏览量 / 预约次数 / 收藏数」表头可排序（在**全库范围**内排序后取前
          {{ rankingLimit }} 名，非仅当前页）
        </span>
      </div>

      <!-- 关于排序的设计说明：
           这里用的是**服务端排序**（sortable="custom" + @sort-change），
           而不是 el-table 默认的客户端排序（sortable）。
           原因：这是「排行榜」，语义上应该是"全部设备里浏览量最高的 N 台"。
           如果只做客户端排序，就变成"已取回的 N 台里再排一次"，
           当设备总数超过 N 时结果就是错的。
           后端 /statistics/equipment 已支持 order_by 参数，
           所以这里点击表头会重新请求接口。 -->
      <el-table
        v-loading="loading"
        :data="equipmentStats.equipment_ranking || []"
        :default-sort="{ prop: 'browse_count', order: 'descending' }"
        style="width: 100%"
        @sort-change="handleSortChange"
      >
        <el-table-column
          type="index"
          label="排名"
          width="70"
          align="center"
          :index="(i) => i + 1"
        />
        <el-table-column prop="equipment_name" label="设备名称" min-width="160" />
        <el-table-column label="分类" width="140">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" type="info">
              {{ row.category_name || '-' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="lab_name" label="实验室" min-width="140" />

        <!-- 说明：这些列用了 #default 插槽自定义渲染，但 prop 依然必须写，
             否则 Element Plus 的排序事件拿不到字段名（sort-change 的 prop 会是 null）。 -->
        <el-table-column
          prop="browse_count"
          label="浏览量"
          width="100"
          align="right"
          sortable="custom"
          :sort-orders="['descending', 'ascending']"
        >
          <template #default="{ row }">{{ row.browse_count }}</template>
        </el-table-column>
        <el-table-column
          prop="booking_count"
          label="预约次数"
          width="110"
          align="right"
          sortable="custom"
          :sort-orders="['descending', 'ascending']"
        >
          <template #default="{ row }">{{ row.booking_count }}</template>
        </el-table-column>
        <el-table-column
          prop="collect_count"
          label="收藏数"
          width="90"
          align="right"
          sortable="custom"
          :sort-orders="['descending', 'ascending']"
        >
          <template #default="{ row }">{{ row.collect_count }}</template>
        </el-table-column>
        <el-table-column prop="active_booking_count" label="当前有效预约" width="130" align="right">
          <template #default="{ row }">
            <el-tag v-if="row.active_booking_count > 0" type="warning" size="small">
              {{ row.active_booking_count }}
            </el-tag>
            <span v-else class="text-muted">0</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<style scoped>
.chart-error {
  margin-bottom: var(--space-5);
}

/* ==================== 指标卡片 ==================== */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}

/* ==================== 图表布局 ==================== */
.chart-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-5);
  margin-bottom: var(--space-5);
}

.chart-card {
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
}

.chart-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
  flex-wrap: wrap;
}

.chart-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-md);
  font-weight: 600;
}

.chart-sub {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
}

.chart-body {
  /* 图表必须有明确高度，否则 ECharts 初始化时拿不到尺寸会渲染异常 */
  height: 280px;
  width: 100%;
}

.chart-body-tall {
  height: 340px;
}

.chart-footer {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-light);
  font-size: var(--font-xs);
}

.chart-footer .text-muted {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* ==================== 状态图例明细 ==================== */
.status-legend {
  margin-top: var(--space-4);
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border-light);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.legend-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-sm);
}

.legend-dot,
.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-name {
  flex: 1;
  color: var(--color-text-secondary);
}

.legend-value {
  font-weight: 600;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}

.legend-percent {
  width: 54px;
  text-align: right;
  color: var(--color-text-placeholder);
  font-variant-numeric: tabular-nums;
}

/* ==================== 预约状态条形图 ==================== */
.booking-status-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-2) 0;
}

.booking-status-item {
  display: grid;
  grid-template-columns: 90px 1fr 90px;
  align-items: center;
  gap: var(--space-3);
}

.status-info {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}

.status-bar-wrapper {
  height: 10px;
  background: var(--color-border-light);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.status-bar {
  height: 100%;
  border-radius: var(--radius-full);
  /* 宽度变化时有个动画，数据加载完不会显得突兀 */
  transition: width 0.6s ease;
  min-width: 2px;
}

.status-numbers {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  justify-content: flex-end;
}

.status-count {
  font-size: var(--font-md);
  font-weight: 700;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}

.status-percent {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
  width: 44px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

/* ==================== 明细表 ==================== */
.table-card {
  padding: var(--space-5);
}

/* ==================== 响应式 ==================== */
@media (max-width: 1100px) {
  .chart-row {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .booking-status-item {
    grid-template-columns: 70px 1fr 70px;
  }
}
</style>
