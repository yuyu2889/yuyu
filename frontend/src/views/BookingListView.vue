<script setup>
/**
 * 我的预约页。
 *
 * 设计要点：
 * 1. **按状态分组用 el-tabs**，而不是一个下拉筛选 ——
 *    状态是用户最关心的维度，做成 tab 一眼能看到各状态的数量。
 * 2. **取消预约要二次确认并填原因**，避免误操作。
 * 3. 支持从 URL 查询参数初始化状态（工作台点"待审核"卡片跳过来）。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import bookingApi from '@/api/booking'
import { usePagination } from '@/composables/usePagination'
import { BOOKING_STATUS, getBookingStatus, PAGINATION } from '@/config/constants'
import { formatTime, calcDuration, formatDate } from '@/utils/format'
import PageHeader from '@/components/common/PageHeader.vue'
import EquipmentImage from '@/components/common/EquipmentImage.vue'

const route = useRoute()
const router = useRouter()

// 当前状态 tab（空字符串表示全部）
const activeStatus = ref(route.query.status || '')

const filters = reactive({
  status: activeStatus.value,
})

// 各状态的预约数量（用于 tab 上的角标）
const statusCounts = ref({})

const {
  list,
  total,
  loading,
  pagination,
  isEmpty,
  refresh,
  handlePageChange,
  handleSizeChange,
} = usePagination(bookingApi.listMyBookings, {
  filters,
  pageSize: PAGINATION.DEFAULT_SIZE,
})

// 状态 tab 配置
const statusTabs = computed(() => [
  { value: '', label: '全部', count: total.value },
  ...Object.entries(BOOKING_STATUS).map(([value, config]) => ({
    value,
    label: config.label,
    count: statusCounts.value[value] ?? 0,
    type: config.type,
  })),
])

function handleTabChange(value) {
  activeStatus.value = value
  filters.status = value
  // 同步到 URL，这样刷新页面后还能保持筛选状态，也方便分享链接
  router.replace({ query: value ? { status: value } : {} })
  refresh()
}

// ---------------------------------------------------------------------------
//  加载各状态数量
// ---------------------------------------------------------------------------
async function loadStatusCounts() {
  try {
    // 并行请求各状态的数量（只取 total，page_size 设为 1 减少数据传输）
    const statuses = Object.keys(BOOKING_STATUS)
    const results = await Promise.allSettled(
      statuses.map((status) =>
        bookingApi.listMyBookings({ page: 1, page_size: 1, status }),
      ),
    )
    const counts = {}
    results.forEach((result, index) => {
      if (result.status === 'fulfilled') {
        counts[statuses[index]] = result.value?.total || 0
      }
    })
    statusCounts.value = counts
  } catch {
    statusCounts.value = {}
  }
}

// ---------------------------------------------------------------------------
//  取消预约
// ---------------------------------------------------------------------------
const cancellingId = ref(null)

async function handleCancel(booking) {
  let reason = ''
  try {
    const { value } = await ElMessageBox.prompt(
      `确定要取消「${booking.equipment_name}」在 ${booking.booking_date} ` +
        `${formatTime(booking.start_time)}-${formatTime(booking.end_time)} 的预约吗？`,
      '取消预约',
      {
        confirmButtonText: '确认取消',
        cancelButtonText: '再想想',
        type: 'warning',
        inputPlaceholder: '请填写取消原因（选填）',
        inputValue: '',
      },
    )
    reason = value || ''
  } catch {
    return // 用户放弃
  }

  cancellingId.value = booking.id
  try {
    await bookingApi.cancelBooking(booking.id, reason)
    ElMessage.success('预约已取消')
    refresh()
    loadStatusCounts()
  } catch {
    // 拦截器已提示
  } finally {
    cancellingId.value = null
  }
}

function goToEquipment(equipmentId) {
  router.push(`/equipment/${equipmentId}`)
}

// 路由查询参数变化时同步 tab（比如从工作台再次点进来）
watch(
  () => route.query.status,
  (newStatus) => {
    if ((newStatus || '') !== activeStatus.value) {
      activeStatus.value = newStatus || ''
      filters.status = activeStatus.value
      refresh()
    }
  },
)

onMounted(() => {
  loadStatusCounts()
})
</script>

<template>
  <div class="page-container">
    <PageHeader title="我的预约" description="查看预约进度、取消未完成的预约">
      <template #actions>
        <el-button type="primary" @click="router.push('/equipment')">
          <el-icon><Plus /></el-icon>预约新设备
        </el-button>
      </template>
    </PageHeader>

    <!-- ==================== 状态 Tab ==================== -->
    <div class="status-tabs card">
      <div
        v-for="tab in statusTabs"
        :key="tab.value"
        class="status-tab"
        :class="{ active: activeStatus === tab.value }"
        @click="handleTabChange(tab.value)"
      >
        <span class="tab-label">{{ tab.label }}</span>
        <span v-if="tab.count > 0" class="tab-count">{{ tab.count }}</span>
      </div>
    </div>

    <!-- ==================== 加载态 ==================== -->
    <div v-if="loading" class="card" style="padding: var(--space-6)">
      <el-skeleton :rows="5" animated />
    </div>

    <!-- ==================== 空状态 ==================== -->
    <div v-else-if="isEmpty" class="card empty-wrapper">
      <el-empty :description="activeStatus ? '该状态下暂无预约' : '您还没有预约记录'">
        <el-button type="primary" @click="router.push('/equipment')">
          去预约设备
        </el-button>
      </el-empty>
    </div>

    <!-- ==================== 预约列表 ==================== -->
    <div v-else class="booking-grid">
      <div
        v-for="booking in list"
        :key="booking.id"
        class="booking-card card animate-in"
      >
        <!-- 顶部：状态条 -->
        <div
          class="card-stripe"
          :style="{ background: getBookingStatus(booking.status).color }"
        />

        <div class="card-header">
          <div class="header-left" @click="goToEquipment(booking.equipment_id)">
            <EquipmentImage
              :src="booking.equipment_image"
              :name="booking.equipment_name"
              height="52px"
              radius="var(--radius-sm)"
              class="card-thumb"
            />
            <div>
              <div class="equipment-name">{{ booking.equipment_name || '未知设备' }}</div>
              <div class="booking-id">预约编号 #{{ booking.id }}</div>
            </div>
          </div>
          <el-tag :type="getBookingStatus(booking.status).type" effect="light">
            {{ getBookingStatus(booking.status).label }}
          </el-tag>
        </div>

        <div class="card-body">
          <div class="info-row">
            <span class="info-label"><el-icon><Calendar /></el-icon>使用时间</span>
            <span class="info-value">
              {{ booking.booking_date }}
              <span class="time-range">
                {{ formatTime(booking.start_time) }} - {{ formatTime(booking.end_time) }}
              </span>
              <span class="duration">
                （{{ calcDuration(booking.start_time, booking.end_time) }}）
              </span>
            </span>
          </div>

          <div class="info-row">
            <span class="info-label"><el-icon><Document /></el-icon>使用目的</span>
            <span class="info-value purpose">{{ booking.purpose }}</span>
          </div>

          <div v-if="booking.notes" class="info-row">
            <span class="info-label"><el-icon><ChatLineSquare /></el-icon>我的备注</span>
            <span class="info-value">{{ booking.notes }}</span>
          </div>

          <div v-if="booking.audit_note" class="info-row">
            <span class="info-label"><el-icon><Comment /></el-icon>审核备注</span>
            <span
              class="info-value audit-note"
              :class="{ rejected: booking.status === 'rejected' }"
            >
              {{ booking.audit_note }}
            </span>
          </div>

          <div class="info-row">
            <span class="info-label"><el-icon><Clock /></el-icon>提交时间</span>
            <span class="info-value text-muted">{{ formatDate(booking.created_at, 'datetime') }}</span>
          </div>
        </div>

        <div class="card-footer">
          <el-button size="small" @click="goToEquipment(booking.equipment_id)">
            查看设备
          </el-button>
          <!-- can_cancel 是后端算好的，前端不用自己判断状态 -->
          <el-button
            v-if="booking.can_cancel"
            type="danger"
            size="small"
            plain
            :loading="cancellingId === booking.id"
            @click="handleCancel(booking)"
          >
            取消预约
          </el-button>
        </div>
      </div>
    </div>

    <!-- ==================== 分页 ==================== -->
    <div v-if="total > 0" class="pagination-wrapper">
      <el-pagination
        :current-page="pagination.page"
        :page-size="pagination.page_size"
        :page-sizes="PAGINATION.SIZE_OPTIONS"
        :total="total"
        layout="total, sizes, prev, pager, next"
        background
        @current-change="handlePageChange"
        @size-change="handleSizeChange"
      />
    </div>
  </div>
</template>

<style scoped>
/* ==================== 状态 Tab ==================== */
.status-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  padding: var(--space-3);
  margin-bottom: var(--space-5);
}

.status-tab {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-full);
  cursor: pointer;
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  transition: background var(--transition-fast), color var(--transition-fast);
}

.status-tab:hover {
  background: var(--color-bg-hover);
}

.status-tab.active {
  background: var(--color-primary);
  color: #fff;
  font-weight: 600;
}

.tab-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 5px;
  border-radius: var(--radius-full);
  background: rgba(0, 0, 0, 0.08);
  font-size: var(--font-xs);
  font-variant-numeric: tabular-nums;
}

.status-tab.active .tab-count {
  background: rgba(255, 255, 255, 0.25);
}

/* ==================== 空状态 ==================== */
.empty-wrapper {
  padding: var(--space-10);
}

/* ==================== 预约卡片 ==================== */
.booking-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
  gap: var(--space-4);
}

.booking-card {
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* 顶部的状态色条 */
.card-stripe {
  height: 3px;
  width: 100%;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4);
  border-bottom: 1px solid var(--color-border-light);
}

.header-left {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  cursor: pointer;
  min-width: 0;
}

.card-thumb {
  width: 52px;
  flex-shrink: 0;
}

.equipment-name {
  font-size: var(--font-md);
  font-weight: 600;
  color: var(--color-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.booking-id {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
}

.card-body {
  padding: var(--space-4);
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.info-row {
  display: flex;
  gap: var(--space-3);
  font-size: var(--font-sm);
}

.info-label {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 76px;
  flex-shrink: 0;
  color: var(--color-text-placeholder);
}

.info-value {
  flex: 1;
  min-width: 0;
  color: var(--color-text-regular);
  word-break: break-word;
}

.time-range {
  color: var(--color-primary);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.duration {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
}

.purpose {
  white-space: pre-wrap;
}

.audit-note {
  padding: var(--space-2);
  background: var(--color-success-pale);
  border-radius: var(--radius-sm);
  color: var(--color-success);
}

.audit-note.rejected {
  background: var(--color-danger-pale);
  color: var(--color-danger);
}

.card-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border-light);
  background: var(--color-bg-hover);
}

/* ==================== 分页 ==================== */
.pagination-wrapper {
  display: flex;
  justify-content: center;
  margin-top: var(--space-6);
}

/* ==================== 响应式 ==================== */
@media (max-width: 900px) {
  .booking-grid {
    grid-template-columns: 1fr;
  }
}
</style>
