<script setup>
/**
 * 设备详情页 + 预约表单。
 *
 * 设计要点：
 * 1. **冲突预检**：用户选完时间段后，提交前先调用 `/bookings/check-conflict`，
 *    提前告诉用户这个时段能不能用。避免"填完一堆信息才被拒绝"。
 *    同时也会展示当天已被占用的时段，用户一眼能看出哪些时段空着。
 * 2. **时间段用两个 el-time-select**，粒度 30 分钟（和后端校验规则一致）。
 *    比"开始时间 + 时长"的交互更直观。
 * 3. **从列表页带 action=book 参数进来时自动打开预约弹窗**，
 *    省掉用户再点一次的动作。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import equipmentApi from '@/api/equipment'
import bookingApi from '@/api/booking'
import { useAuthStore } from '@/stores/auth'
import { BOOKING_RULES, getEquipmentStatus } from '@/config/constants'
import { formatPrice, formatTime, calcDuration } from '@/utils/format'
import PageHeader from '@/components/common/PageHeader.vue'
import EquipmentImage from '@/components/common/EquipmentImage.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const equipmentId = computed(() => Number(route.params.id))

const loading = ref(true)
const equipment = ref({})
const isCollected = ref(false)
const collectLoading = ref(false)

// 预约弹窗
const bookingVisible = ref(false)
const bookingSubmitting = ref(false)
const checking = ref(false)
const conflictResult = ref(null)
const bookedSlots = ref([])

const bookingFormRef = ref(null)
const bookingForm = reactive({
  booking_date: '',
  start_time: '',
  end_time: '',
  purpose: '',
  notes: '',
})

const bookingRules = {
  booking_date: [{ required: true, message: '请选择预约日期', trigger: 'change' }],
  start_time: [{ required: true, message: '请选择开始时间', trigger: 'change' }],
  end_time: [{ required: true, message: '请选择结束时间', trigger: 'change' }],
  purpose: [
    { required: true, message: '请填写使用目的', trigger: 'blur' },
    { min: 2, max: 500, message: '使用目的长度为 2-500 个字符', trigger: 'blur' },
  ],
}

// ---------------------------------------------------------------------------
//  时间选择器的约束
// ---------------------------------------------------------------------------
//  日期可选范围：今天 ~ 30 天后（与后端规则一致）
const dateDisabled = (date) => {
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const max = new Date()
  max.setDate(max.getDate() + BOOKING_RULES.MAX_ADVANCE_DAYS)
  max.setHours(23, 59, 59, 999)
  return date < today || date > max
}

// 把后端的 "HH:mm" 字符串转成从 08:00 到 22:00、步长 30 分钟的选项
const timeOptions = computed(() => {
  const options = []
  const [startH, startM] = BOOKING_RULES.DAY_START.split(':').map(Number)
  const [endH, endM] = BOOKING_RULES.DAY_END.split(':').map(Number)
  const startMinutes = startH * 60 + startM
  const endMinutes = endH * 60 + endM

  for (let m = startMinutes; m <= endMinutes; m += BOOKING_RULES.STEP_MINUTES) {
    const h = String(Math.floor(m / 60)).padStart(2, '0')
    const mm = String(m % 60).padStart(2, '0')
    options.push(`${h}:${mm}`)
  }
  return options
})

/** 结束时间必须晚于开始时间 → 动态禁用无效选项 */
const endTimeDisabled = (value) => {
  if (!bookingForm.start_time) return false
  return value <= bookingForm.start_time
}

/** 计算当前填写的时长，实时提示给用户 */
const currentDuration = computed(() => {
  if (!bookingForm.start_time || !bookingForm.end_time) return ''
  return calcDuration(bookingForm.start_time, bookingForm.end_time)
})

const durationValid = computed(() => {
  if (!bookingForm.start_time || !bookingForm.end_time) return true
  const toMin = (t) => {
    const [h, m] = t.split(':').map(Number)
    return h * 60 + m
  }
  const diff = toMin(bookingForm.end_time) - toMin(bookingForm.start_time)
  if (diff <= 0) return false
  return diff >= BOOKING_RULES.MIN_DURATION_MINUTES && diff <= BOOKING_RULES.MAX_DURATION_MINUTES
})

// ---------------------------------------------------------------------------
//  数据加载
// ---------------------------------------------------------------------------
async function loadEquipment() {
  loading.value = true
  try {
    const data = await equipmentApi.getEquipmentDetail(equipmentId.value)
    equipment.value = data
    isCollected.value = Boolean(data.is_collected)
  } catch {
    equipment.value = {}
  } finally {
    loading.value = false
  }
}

async function toggleCollection() {
  if (!auth.isLoggedIn.value) {
    ElMessage.warning('请先登录')
    router.push({ name: 'Login', query: { redirect: route.fullPath } })
    return
  }

  collectLoading.value = true
  try {
    if (isCollected.value) {
      await equipmentApi.removeCollection(equipmentId.value)
      isCollected.value = false
      ElMessage.success('已取消收藏')
    } else {
      await equipmentApi.addCollection(equipmentId.value)
      isCollected.value = true
      ElMessage.success('已加入收藏')
    }
  } catch {
    // 拦截器已提示
  } finally {
    collectLoading.value = false
  }
}

// ---------------------------------------------------------------------------
//  冲突预检
// ---------------------------------------------------------------------------
async function checkConflict() {
  // 三个字段都填齐了才有必要检查
  if (!bookingForm.booking_date || !bookingForm.start_time || !bookingForm.end_time) {
    return
  }
  if (!durationValid.value) {
    conflictResult.value = null
    return
  }

  checking.value = true
  try {
    const result = await bookingApi.checkConflict({
      equipment_id: equipmentId.value,
      booking_date: bookingForm.booking_date,
      start_time: `${bookingForm.start_time}:00`,
      end_time: `${bookingForm.end_time}:00`,
    })
    conflictResult.value = result
    bookedSlots.value = result.booked_slots || []
  } catch {
    conflictResult.value = null
  } finally {
    checking.value = false
  }
}

// 表单关键字段变化时自动重新预检
watch(
  () => [bookingForm.booking_date, bookingForm.start_time, bookingForm.end_time],
  () => {
    conflictResult.value = null
    checkConflict()
  },
)

// ---------------------------------------------------------------------------
//  提交预约
// ---------------------------------------------------------------------------
async function submitBooking() {
  try {
    await bookingFormRef.value.validate()
  } catch {
    return
  }

  if (!durationValid.value) {
    ElMessage.warning(
      `单次预约时长需在 ${BOOKING_RULES.MIN_DURATION_MINUTES} 分钟到 ` +
        `${BOOKING_RULES.MAX_DURATION_MINUTES / 60} 小时之间`,
    )
    return
  }

  // 预检提示有冲突时再确认一次（用户可能想强行提交，让后端做最终判定）
  if (conflictResult.value && !conflictResult.value.available) {
    try {
      await ElMessageBox.confirm(
        `${conflictResult.value.reason}。确定要提交吗？（提交后由后端做最终校验）`,
        '时段可能不可用',
        { confirmButtonText: '仍要提交', cancelButtonText: '重新选择', type: 'warning' },
      )
    } catch {
      return
    }
  }

  bookingSubmitting.value = true
  try {
    await bookingApi.createBooking({
      equipment_id: equipmentId.value,
      booking_date: bookingForm.booking_date,
      start_time: `${bookingForm.start_time}:00`,
      end_time: `${bookingForm.end_time}:00`,
      purpose: bookingForm.purpose,
      notes: bookingForm.notes || null,
    })

    ElMessage.success('预约申请已提交，请等待管理员审核')
    bookingVisible.value = false
    resetBookingForm()
    // 刷新设备状态（可能因为预约而改变）
    loadEquipment()
  } catch {
    // 拦截器已提示（冲突、权限等问题都会有明确提示）
  } finally {
    bookingSubmitting.value = false
  }
}

function resetBookingForm() {
  bookingFormRef.value?.resetFields()
  bookingForm.notes = ''
  conflictResult.value = null
  bookedSlots.value = []
}

function openBookingDialog() {
  if (!auth.isLoggedIn.value) {
    ElMessage.warning('请先登录后再预约')
    router.push({ name: 'Login', query: { redirect: route.fullPath } })
    return
  }
  if (equipment.value.status !== 'available') {
    ElMessage.warning('该设备当前不可预约')
    return
  }
  bookingVisible.value = true
}

// ---------------------------------------------------------------------------
//  初始化
// ---------------------------------------------------------------------------
onMounted(async () => {
  await loadEquipment()
  // 从列表页带 action=book 参数进来时，自动打开预约弹窗
  if (route.query.action === 'book') {
    openBookingDialog()
  }
})
</script>

<template>
  <div class="page-container">
    <!-- ==================== 加载骨架 ==================== -->
    <el-skeleton v-if="loading" :rows="8" animated />

    <template v-else-if="equipment.id">
      <PageHeader :title="equipment.name" :description="equipment.model">
        <template #actions>
          <el-button @click="router.back()">
            <el-icon><Back /></el-icon>返回
          </el-button>
          <el-button
            :type="isCollected ? 'warning' : 'default'"
            :loading="collectLoading"
            @click="toggleCollection"
          >
            <el-icon>
              <component :is="isCollected ? 'StarFilled' : 'Star'" />
            </el-icon>
            {{ isCollected ? '已收藏' : '收藏' }}
          </el-button>
          <el-button
            type="primary"
            :disabled="equipment.status !== 'available'"
            @click="openBookingDialog"
          >
            <el-icon><Calendar /></el-icon>
            {{ equipment.status === 'available' ? '立即预约' : '当前不可预约' }}
          </el-button>
        </template>
      </PageHeader>

      <div class="detail-layout">
        <!-- ==================== 左侧：图片与统计 ==================== -->
        <div class="detail-left">
          <div class="image-card card">
            <EquipmentImage
              :src="equipment.image"
              :name="equipment.name"
              height="320px"
              radius="0"
            />
            <div class="image-status">
              <el-tag
                :type="getEquipmentStatus(equipment.status).type"
                effect="dark"
                size="large"
              >
                {{ getEquipmentStatus(equipment.status).label }}
              </el-tag>
            </div>
          </div>

          <div class="stats-card card">
            <div class="stat-row">
              <div class="stat-block">
                <el-icon :size="20" color="var(--color-primary)"><View /></el-icon>
                <div class="stat-num">{{ equipment.browse_count || 0 }}</div>
                <div class="stat-text">浏览量</div>
              </div>
              <div class="stat-block">
                <el-icon :size="20" color="var(--color-success)"><Calendar /></el-icon>
                <div class="stat-num">{{ equipment.booking_count || 0 }}</div>
                <div class="stat-text">累计预约</div>
              </div>
            </div>
          </div>
        </div>

        <!-- ==================== 右侧：详细信息 ==================== -->
        <div class="detail-right card">
          <h3 class="section-title">
            <el-icon><InfoFilled /></el-icon>设备信息
          </h3>

          <el-descriptions :column="1" border class="info-descriptions">
            <el-descriptions-item label="设备名称">{{ equipment.name }}</el-descriptions-item>
            <el-descriptions-item label="设备型号">{{ equipment.model }}</el-descriptions-item>
            <el-descriptions-item label="序列号">
              <span class="mono">{{ equipment.serial_number }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="设备分类">
              <el-tag size="small" effect="plain">{{ equipment.category_name || '未分类' }}</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="所在实验室">
              <span v-if="equipment.lab_name">
                {{ equipment.lab_name }}
                <span v-if="equipment.lab_location" class="text-muted">
                  （{{ equipment.lab_location }}）
                </span>
              </span>
              <span v-else class="text-muted">未指定</span>
            </el-descriptions-item>
            <el-descriptions-item label="采购日期">
              {{ equipment.purchase_date || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="设备价值">
              {{ formatPrice(equipment.price) }}
            </el-descriptions-item>
          </el-descriptions>

          <h3 class="section-title" style="margin-top: var(--space-6)">
            <el-icon><Document /></el-icon>设备说明
          </h3>
          <p class="description-text">
            {{ equipment.description || '暂无设备说明' }}
          </p>
        </div>
      </div>
    </template>

    <el-empty v-else description="设备不存在或已被删除">
      <el-button type="primary" @click="router.push('/equipment')">返回设备列表</el-button>
    </el-empty>

    <!-- ==================== 预约弹窗 ==================== -->
    <el-dialog
      v-model="bookingVisible"
      title="预约设备"
      width="560px"
      :close-on-click-modal="false"
      @closed="resetBookingForm"
    >
      <div class="dialog-equipment">
        <EquipmentImage
          :src="equipment.image"
          :name="equipment.name"
          height="48px"
          radius="var(--radius-sm)"
          class="dialog-thumb"
        />
        <div>
          <div class="dialog-name">{{ equipment.name }}</div>
          <div class="text-muted">{{ equipment.model }}</div>
        </div>
      </div>

      <el-form
        ref="bookingFormRef"
        :model="bookingForm"
        :rules="bookingRules"
        label-width="90px"
        class="booking-form"
      >
        <el-form-item label="预约日期" prop="booking_date">
          <el-date-picker
            v-model="bookingForm.booking_date"
            type="date"
            placeholder="选择日期"
            value-format="YYYY-MM-DD"
            :disabled-date="dateDisabled"
            style="width: 100%"
          />
        </el-form-item>

        <el-form-item label="使用时段" required>
          <div class="time-range">
            <el-select
              v-model="bookingForm.start_time"
              placeholder="开始时间"
              class="time-select"
            >
              <el-option
                v-for="t in timeOptions"
                :key="`start-${t}`"
                :label="t"
                :value="t"
                :disabled="endTimeDisabled(t)"
              />
            </el-select>
            <span class="time-separator">至</span>
            <el-select
              v-model="bookingForm.end_time"
              placeholder="结束时间"
              class="time-select"
            >
              <el-option
                v-for="t in timeOptions"
                :key="`end-${t}`"
                :label="t"
                :value="t"
                :disabled="t <= bookingForm.start_time"
              />
            </el-select>
          </div>
          <div v-if="currentDuration" class="time-hint" :class="{ invalid: !durationValid }">
            时长：{{ currentDuration }}
            <template v-if="!durationValid">
              （需在 {{ BOOKING_RULES.MIN_DURATION_MINUTES }} 分钟 -
              {{ BOOKING_RULES.MAX_DURATION_MINUTES / 60 }} 小时之间）
            </template>
          </div>
        </el-form-item>

        <!-- 冲突预检结果 -->
        <el-form-item v-if="checking" label="可用性">
          <span class="text-muted">
            <el-icon class="is-loading"><Loading /></el-icon>正在检查时段可用性…
          </span>
        </el-form-item>

        <el-form-item v-else-if="conflictResult" label="可用性">
          <el-alert
            v-if="conflictResult.available"
            type="success"
            :closable="false"
            show-icon
            title="该时段可以预约"
          />
          <el-alert
            v-else
            type="error"
            :closable="false"
            show-icon
            :title="conflictResult.reason || '该时段不可用'"
          >
            <template v-if="conflictResult.conflicts?.length" #default>
              冲突时段：
              <span v-for="(c, i) in conflictResult.conflicts" :key="i">
                {{ formatTime(c.start_time) }}-{{ formatTime(c.end_time)
                }}<span v-if="i < conflictResult.conflicts.length - 1">、</span>
              </span>
            </template>
          </el-alert>
        </el-form-item>

        <!-- 当天已占用时段 -->
        <el-form-item v-if="bookedSlots.length" label="已占用">
          <div class="booked-slots">
            <el-tag
              v-for="(slot, i) in bookedSlots"
              :key="i"
              size="small"
              type="info"
              effect="plain"
            >
              {{ formatTime(slot.start_time) }}-{{ formatTime(slot.end_time) }}
            </el-tag>
          </div>
        </el-form-item>

        <el-form-item label="使用目的" prop="purpose">
          <el-input
            v-model="bookingForm.purpose"
            type="textarea"
            :rows="3"
            maxlength="500"
            show-word-limit
            placeholder="请简要说明使用该设备要做什么实验或任务"
          />
        </el-form-item>

        <el-form-item label="备注">
          <el-input
            v-model="bookingForm.notes"
            type="textarea"
            :rows="2"
            maxlength="500"
            show-word-limit
            placeholder="选填，如有特殊需求可在此说明"
          />
        </el-form-item>
      </el-form>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="提交后需等待管理员审核，审核结果会发送到您的邮箱"
        class="dialog-tip"
      />

      <template #footer>
        <el-button @click="bookingVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="bookingSubmitting"
          :disabled="!durationValid"
          @click="submitBooking"
        >
          提交预约申请
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ==================== 详情布局 ==================== */
.detail-layout {
  display: grid;
  grid-template-columns: 420px 1fr;
  gap: var(--space-5);
  align-items: start;
}

.detail-left {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.image-card {
  position: relative;
  overflow: hidden;
}

.image-status {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
}

.stats-card {
  padding: var(--space-4);
}

.stat-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
}

.stat-block {
  text-align: center;
}

.stat-num {
  font-size: var(--font-xl);
  font-weight: 700;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
  margin-top: var(--space-1);
}

.stat-text {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
}

/* ==================== 右侧信息 ==================== */
.detail-right {
  padding: var(--space-6);
}

.section-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-md);
  font-weight: 600;
  margin-bottom: var(--space-4);
}

.info-descriptions {
  margin-bottom: var(--space-2);
}

.mono {
  font-family: Consolas, Monaco, monospace;
  font-size: var(--font-sm);
}

.description-text {
  font-size: var(--font-base);
  line-height: 1.8;
  color: var(--color-text-regular);
  margin: 0;
  white-space: pre-wrap;
}

/* ==================== 预约弹窗 ==================== */
.dialog-equipment {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  background: var(--color-bg-hover);
  border-radius: var(--radius-md);
  margin-bottom: var(--space-4);
}

.dialog-thumb {
  width: 48px;
  flex-shrink: 0;
}

.dialog-name {
  font-weight: 600;
  color: var(--color-text-primary);
}

.booking-form {
  margin-top: var(--space-2);
}

.time-range {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
}

.time-select {
  flex: 1;
}

.time-separator {
  color: var(--color-text-placeholder);
  flex-shrink: 0;
}

.time-hint {
  font-size: var(--font-xs);
  color: var(--color-text-secondary);
  margin-top: var(--space-1);
}

.time-hint.invalid {
  color: var(--color-danger);
}

.booked-slots {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}

.dialog-tip {
  margin-top: var(--space-2);
}

/* ==================== 响应式 ==================== */
@media (max-width: 1100px) {
  .detail-layout {
    grid-template-columns: 1fr;
  }
}
</style>
