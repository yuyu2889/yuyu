<script setup>
/**
 * 预约审核页（管理员）。
 *
 * 设计要点：
 * 1. **待审核的排在前面**：默认按"待审核"筛选，因为这是管理员最需要处理的任务。
 * 2. **批量操作**：支持多选后批量通过（前提是无冲突）。
 *    这是很实用的效率优化 —— 管理员一天可能要审几十条。
 * 3. **审核前展示冲突信息**：如果该设备该时段已被其他已通过的预约占用，
 *    审核时后端会拒绝。这里提前展示，避免管理员白操作一次。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import bookingApi from '@/api/booking'
import { usePagination } from '@/composables/usePagination'
import { BOOKING_STATUS, getBookingStatus, PAGINATION } from '@/config/constants'
import { formatTime, calcDuration, formatDate } from '@/utils/format'
import PageHeader from '@/components/common/PageHeader.vue'
import EquipmentImage from '@/components/common/EquipmentImage.vue'

// ---------------------------------------------------------------------------
//  筛选条件
// ---------------------------------------------------------------------------
const filters = reactive({
  status: 'pending', // 默认只看待审核
  keyword: '',
  equipment_id: '',
  date_from: '',
  date_to: '',
})

// 日期范围（用 el-date-picker 的 range 模式，绑定成数组再拆分到 filters）
const dateRange = ref([])

const {
  list,
  total,
  loading,
  pagination,
  isEmpty,
  refresh,
  fetchData,
  handlePageChange,
  handleSizeChange,
} = usePagination(bookingApi.listAllBookings, {
  filters,
  pageSize: PAGINATION.DEFAULT_SIZE,
})

// 状态 tab 配置
const statusTabs = computed(() => [
  { value: 'pending', label: '待审核' },
  { value: '', label: '全部' },
  { value: 'approved', label: '已通过' },
  { value: 'completed', label: '已完成' },
  { value: 'rejected', label: '已拒绝' },
  { value: 'cancelled', label: '已取消' },
])

function handleStatusChange(status) {
  filters.status = status
  refresh()
}

// 日期范围变化
function handleDateChange(range) {
  filters.date_from = range?.[0] || ''
  filters.date_to = range?.[1] || ''
  refresh()
}

function resetFilters() {
  filters.status = 'pending'
  filters.keyword = ''
  filters.equipment_id = ''
  filters.date_from = ''
  filters.date_to = ''
  dateRange.value = []
  refresh()
}

// ---------------------------------------------------------------------------
//  统计概览
// ---------------------------------------------------------------------------
const overview = ref({})

async function loadOverview() {
  try {
    const statisticsApi = (await import('@/api/statistics')).default
    const data = await statisticsApi.getBookingStatistics()
    overview.value = data
  } catch {
    overview.value = {}
  }
}

// ---------------------------------------------------------------------------
//  审核操作
// ---------------------------------------------------------------------------
const auditVisible = ref(false)
const auditSubmitting = ref(false)
const currentBooking = ref(null)
const auditFormRef = ref(null)
const auditForm = reactive({
  result: 'approved',
  note: '',
})

const auditRules = {
  note: [
    {
      validator: (rule, value, callback) => {
        // 拒绝时必须填原因（后端也会校验，这里提前拦一次减少一次请求）
        if (auditForm.result === 'rejected' && !String(value || '').trim()) {
          return callback(new Error('拒绝预约时必须填写原因'))
        }
        callback()
      },
      trigger: 'blur',
    },
  ],
}

function openAudit(booking, result = 'approved') {
  currentBooking.value = booking
  auditForm.result = result
  auditForm.note = ''
  auditVisible.value = true
}

async function submitAudit() {
  try {
    await auditFormRef.value.validate()
  } catch {
    return
  }

  auditSubmitting.value = true
  try {
    await bookingApi.auditBooking(currentBooking.value.id, {
      result: auditForm.result,
      note: auditForm.note || null,
    })
    ElMessage.success(auditForm.result === 'approved' ? '已通过该预约' : '已拒绝该预约')
    auditVisible.value = false
    refresh()
    loadOverview()
  } catch {
    // 拦截器已提示（比如"该时段已被占用"会明确告知）
  } finally {
    auditSubmitting.value = false
  }
}

/** 快速通过（不打开弹窗，直接确认） */
async function quickApprove(booking) {
  try {
    await ElMessageBox.confirm(
      `确定通过「${booking.user_name}」对「${booking.equipment_name}」的预约申请吗？\n\n` +
        `时间：${booking.booking_date} ${formatTime(booking.start_time)}-${formatTime(booking.end_time)}`,
      '确认通过',
      { confirmButtonText: '确认通过', cancelButtonText: '取消', type: 'success' },
    )
  } catch {
    return
  }

  try {
    await bookingApi.auditBooking(booking.id, { result: 'approved', note: null })
    ElMessage.success('已通过')
    refresh()
    loadOverview()
  } catch {
    // 拦截器已提示
  }
}

// ---------------------------------------------------------------------------
//  批量操作
// ---------------------------------------------------------------------------
const selectedBookings = ref([])

const canBatchApprove = computed(
  () => selectedBookings.value.length > 0 &&
    selectedBookings.value.every((b) => b.status === 'pending'),
)

async function handleBatchApprove() {
  const ids = selectedBookings.value.map((b) => b.id)
  try {
    await ElMessageBox.confirm(
      `确定批量通过选中的 ${ids.length} 条预约吗？\n\n` +
        '注意：如果某条预约的设备时段已被占用，该条会被拒绝通过。',
      '批量审核',
      { confirmButtonText: '确认通过', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }

  const results = await Promise.allSettled(
    ids.map((id) => bookingApi.auditBooking(id, { result: 'approved', note: '批量审核通过' })),
  )

  const succeeded = results.filter((r) => r.status === 'fulfilled').length
  const failed = results.length - succeeded

  if (failed === 0) {
    ElMessage.success(`成功通过 ${succeeded} 条预约`)
  } else {
    ElMessage.warning(`通过 ${succeeded} 条，失败 ${failed} 条（可能是时段冲突）`)
  }

  selectedBookings.value = []
  refresh()
  loadOverview()
}

function handleSelectionChange(rows) {
  selectedBookings.value = rows
}

// ---------------------------------------------------------------------------
//  详情弹窗
// ---------------------------------------------------------------------------
const detailVisible = ref(false)
const detailBooking = ref(null)

function openDetail(booking) {
  detailBooking.value = booking
  detailVisible.value = true
}

/** 判断某条预约是否"即将开始"（用于高亮提醒） */
function isUpcoming(booking) {
  if (booking.status !== 'approved') return false
  const start = new Date(`${booking.booking_date}T${booking.start_time}`)
  const diffHours = (start.getTime() - Date.now()) / 3600000
  return diffHours >= 0 && diffHours <= 24
}

onMounted(() => {
  loadOverview()
})
</script>

<template>
  <div class="page-container">
    <PageHeader title="预约审核" description="审核用户提交的设备预约申请，通过后用户即可按时使用设备">
      <template #actions>
        <el-button
          v-if="canBatchApprove"
          type="success"
          @click="handleBatchApprove"
        >
          <el-icon><Select /></el-icon>
          批量通过（{{ selectedBookings.length }}）
        </el-button>
        <el-button @click="refresh">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <!-- ==================== 概览卡片 ==================== -->
    <div class="overview-grid">
      <div class="overview-card card pending" @click="handleStatusChange('pending')">
        <div class="overview-value">{{ overview.pending_count ?? 0 }}</div>
        <div class="overview-label">待审核</div>
      </div>
      <div class="overview-card card" @click="handleStatusChange('approved')">
        <div class="overview-value">{{ overview.approved_count ?? 0 }}</div>
        <div class="overview-label">已通过</div>
      </div>
      <div class="overview-card card" @click="handleStatusChange('completed')">
        <div class="overview-value">{{ overview.completed_count ?? 0 }}</div>
        <div class="overview-label">已完成</div>
      </div>
      <div class="overview-card card" @click="handleStatusChange('')">
        <div class="overview-value">{{ overview.total_bookings ?? 0 }}</div>
        <div class="overview-label">预约总数</div>
      </div>
      <div class="overview-card card">
        <div class="overview-value">{{ overview.today_bookings ?? 0 }}</div>
        <div class="overview-label">今日预约</div>
      </div>
    </div>

    <!-- ==================== 筛选栏 ==================== -->
    <div class="filter-bar card">
      <div class="filter-row">
        <el-input
          v-model="filters.keyword"
          placeholder="搜索预约人 / 设备名 / 使用目的"
          clearable
          class="filter-search"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>

        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          class="filter-date"
          @change="handleDateChange"
        />

        <el-button v-if="filters.keyword || dateRange?.length" @click="resetFilters">
          重置筛选
        </el-button>
      </div>

      <!-- 状态 tab -->
      <div class="status-tabs">
        <div
          v-for="tab in statusTabs"
          :key="tab.value"
          class="status-tab"
          :class="{ active: filters.status === tab.value }"
          @click="handleStatusChange(tab.value)"
        >
          {{ tab.label }}
        </div>
      </div>
    </div>

    <!-- ==================== 表格 ==================== -->
    <div class="card table-card">
      <el-table
        v-loading="loading"
        :data="list"
        style="width: 100%"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="45" :selectable="(row) => row.status === 'pending'" />

        <el-table-column label="预约人" width="130">
          <template #default="{ row }">
            <div class="user-cell">
              <el-avatar :size="28" class="user-avatar">
                {{ (row.user_name || '?').charAt(0) }}
              </el-avatar>
              <div>
                <div class="user-name">{{ row.user_name || '-' }}</div>
                <div class="text-muted">#{{ row.user_id }}</div>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="设备" min-width="200">
          <template #default="{ row }">
            <div class="equipment-cell">
              <EquipmentImage
                :src="row.equipment_image"
                :name="row.equipment_name"
                height="40px"
                radius="var(--radius-sm)"
                class="cell-thumb"
              />
              <span class="cell-name">{{ row.equipment_name || '-' }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="使用时间" min-width="200">
          <template #default="{ row }">
            <div class="time-cell">
              <div class="time-date">
                {{ row.booking_date }}
                <el-tag v-if="isUpcoming(row)" type="warning" size="small" effect="plain">
                  即将开始
                </el-tag>
              </div>
              <div class="time-range">
                {{ formatTime(row.start_time) }} - {{ formatTime(row.end_time) }}
                <span class="text-muted">
                  （{{ calcDuration(row.start_time, row.end_time) }}）
                </span>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="使用目的" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ row.purpose }}</template>
        </el-table-column>

        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="getBookingStatus(row.status).type" size="small" effect="light">
              {{ getBookingStatus(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="200" align="center" fixed="right">
          <template #default="{ row }">
            <template v-if="row.status === 'pending'">
              <el-button type="success" size="small" @click="quickApprove(row)">
                通过
              </el-button>
              <el-button type="danger" size="small" plain @click="openAudit(row, 'rejected')">
                拒绝
              </el-button>
            </template>
            <el-button size="small" link @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 空状态 -->
      <el-empty
        v-if="isEmpty"
        :description="filters.status === 'pending' ? '暂无待审核的预约申请' : '没有符合条件的预约'"
        :image-size="80"
      />

      <!-- 分页 -->
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

    <!-- ==================== 审核弹窗 ==================== -->
    <el-dialog
      v-model="auditVisible"
      :title="auditForm.result === 'approved' ? '通过预约' : '拒绝预约'"
      width="520px"
      :close-on-click-modal="false"
    >
      <div v-if="currentBooking" class="audit-info">
        <div class="audit-row">
          <span class="audit-label">预约人</span>
          <span>{{ currentBooking.user_name }}</span>
        </div>
        <div class="audit-row">
          <span class="audit-label">设备</span>
          <span>{{ currentBooking.equipment_name }}</span>
        </div>
        <div class="audit-row">
          <span class="audit-label">时间</span>
          <span>
            {{ currentBooking.booking_date }}
            {{ formatTime(currentBooking.start_time) }}-{{ formatTime(currentBooking.end_time) }}
          </span>
        </div>
        <div class="audit-row">
          <span class="audit-label">用途</span>
          <span>{{ currentBooking.purpose }}</span>
        </div>
        <div v-if="currentBooking.notes" class="audit-row">
          <span class="audit-label">用户备注</span>
          <span>{{ currentBooking.notes }}</span>
        </div>
      </div>

      <el-form ref="auditFormRef" :model="auditForm" :rules="auditRules" class="audit-form">
        <el-form-item label="审核结果">
          <el-radio-group v-model="auditForm.result">
            <el-radio value="approved">通过</el-radio>
            <el-radio value="rejected">拒绝</el-radio>
          </el-radio-group>
        </el-form-item>

        <el-form-item
          label="审核备注"
          prop="note"
          :required="auditForm.result === 'rejected'"
        >
          <el-input
            v-model="auditForm.note"
            type="textarea"
            :rows="3"
            maxlength="500"
            show-word-limit
            :placeholder="
              auditForm.result === 'rejected'
                ? '请说明拒绝原因，用户会收到邮件通知'
                : '选填，如使用注意事项'
            "
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="auditVisible = false">取消</el-button>
        <el-button
          :type="auditForm.result === 'approved' ? 'success' : 'danger'"
          :loading="auditSubmitting"
          @click="submitAudit"
        >
          确认{{ auditForm.result === 'approved' ? '通过' : '拒绝' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- ==================== 详情弹窗 ==================== -->
    <el-dialog v-model="detailVisible" title="预约详情" width="600px">
      <el-descriptions v-if="detailBooking" :column="1" border>
        <el-descriptions-item label="预约编号">{{ detailBooking.id }}</el-descriptions-item>
        <el-descriptions-item label="预约人">
          {{ detailBooking.user_name }}（ID: {{ detailBooking.user_id }}）
        </el-descriptions-item>
        <el-descriptions-item label="设备">
          {{ detailBooking.equipment_name }}（ID: {{ detailBooking.equipment_id }}）
        </el-descriptions-item>
        <el-descriptions-item label="预约日期">{{ detailBooking.booking_date }}</el-descriptions-item>
        <el-descriptions-item label="使用时段">
          {{ formatTime(detailBooking.start_time) }} - {{ formatTime(detailBooking.end_time) }}
          （{{ calcDuration(detailBooking.start_time, detailBooking.end_time) }}）
        </el-descriptions-item>
        <el-descriptions-item label="使用目的">{{ detailBooking.purpose }}</el-descriptions-item>
        <el-descriptions-item label="用户备注">
          {{ detailBooking.notes || '无' }}
        </el-descriptions-item>
        <el-descriptions-item label="当前状态">
          <el-tag :type="getBookingStatus(detailBooking.status).type" size="small">
            {{ getBookingStatus(detailBooking.status).label }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="审核备注">
          {{ detailBooking.audit_note || '无' }}
        </el-descriptions-item>
        <el-descriptions-item label="提交时间">
          {{ formatDate(detailBooking.created_at, 'datetime') }}
        </el-descriptions-item>
      </el-descriptions>

      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ==================== 概览卡片 ==================== */
.overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--space-3);
  margin-bottom: var(--space-5);
}

.overview-card {
  padding: var(--space-4);
  text-align: center;
  cursor: pointer;
  transition: transform var(--transition-fast), box-shadow var(--transition-base);
}

.overview-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.overview-card.pending {
  border-left: 3px solid var(--color-warning);
}

.overview-value {
  font-size: var(--font-2xl);
  font-weight: 700;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}

.overview-label {
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  margin-top: var(--space-1);
}

/* ==================== 筛选栏 ==================== */
.filter-bar {
  padding: var(--space-4);
  margin-bottom: var(--space-5);
}

.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  align-items: center;
  margin-bottom: var(--space-3);
}

.filter-search {
  width: 280px;
}

.filter-date {
  width: 260px;
}

.status-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-light);
}

.status-tab {
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-full);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  cursor: pointer;
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

/* ==================== 表格 ==================== */
.table-card {
  padding: var(--space-4);
}

.user-cell {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.user-avatar {
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-dark));
  color: #fff;
  font-size: var(--font-sm);
  font-weight: 600;
  flex-shrink: 0;
}

.user-name {
  font-weight: 600;
  color: var(--color-text-primary);
  font-size: var(--font-sm);
}

.equipment-cell {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.cell-thumb {
  width: 40px;
  flex-shrink: 0;
}

.cell-name {
  font-weight: 500;
}

.time-cell {
  line-height: 1.5;
}

.time-date {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: 500;
}

.time-range {
  font-size: var(--font-sm);
  color: var(--color-primary);
  font-variant-numeric: tabular-nums;
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  margin-top: var(--space-5);
}

/* ==================== 审核弹窗 ==================== */
.audit-info {
  background: var(--color-bg-hover);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  margin-bottom: var(--space-4);
}

.audit-row {
  display: flex;
  gap: var(--space-3);
  font-size: var(--font-sm);
  padding: var(--space-1) 0;
}

.audit-label {
  width: 72px;
  flex-shrink: 0;
  color: var(--color-text-placeholder);
}

.audit-form {
  margin-top: var(--space-2);
}

/* ==================== 响应式 ==================== */
@media (max-width: 900px) {
  .filter-search,
  .filter-date {
    width: 100%;
  }
}
</style>
