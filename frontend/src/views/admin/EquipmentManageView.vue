<script setup>
/**
 * 设备管理页（管理员）。
 *
 * 包含：设备列表 + 新增/编辑弹窗 + 图片上传。
 *
 * 图片上传的设计（这是本项目的重点改进）：
 * 1. 前端只做"大小和格式的初步校验"给用户即时反馈
 * 2. 真正的安全校验在后端（读文件头判断真实类型）
 * 3. 上传成功后后端返回完整 URL，前端直接用，不做任何路径拼接
 * 4. 换图时后端会自动删除旧文件，不会残留垃圾
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import equipmentApi from '@/api/equipment'
import { usePagination } from '@/composables/usePagination'
import { EQUIPMENT_STATUS, getEquipmentStatus, PAGINATION } from '@/config/constants'
import { formatPrice } from '@/utils/format'
import PageHeader from '@/components/common/PageHeader.vue'
import EquipmentImage from '@/components/common/EquipmentImage.vue'

// ---------------------------------------------------------------------------
//  列表
// ---------------------------------------------------------------------------
const filters = reactive({
  keyword: '',
  category_id: '',
  lab_id: '',
  status: '',
})

const {
  list,
  total,
  loading,
  pagination,
  isEmpty,
  refresh,
  handlePageChange,
  handleSizeChange,
} = usePagination(equipmentApi.listEquipments, {
  filters,
  pageSize: PAGINATION.DEFAULT_SIZE,
})

const categories = ref([])
const laboratories = ref([])

// ---------------------------------------------------------------------------
//  新增 / 编辑弹窗
// ---------------------------------------------------------------------------
const dialogVisible = ref(false)
const dialogMode = ref('create') // create | edit
const submitting = ref(false)
const formRef = ref(null)

const form = reactive({
  id: null,
  name: '',
  model: '',
  serial_number: '',
  category_id: null,
  lab_id: null,
  status: 'available',
  purchase_date: '',
  price: null,
  description: '',
  image: null,
})

const rules = {
  name: [{ required: true, message: '请输入设备名称', trigger: 'blur' }],
  model: [{ required: true, message: '请输入设备型号', trigger: 'blur' }],
  serial_number: [{ required: true, message: '请输入序列号', trigger: 'blur' }],
  category_id: [{ required: true, message: '请选择设备分类', trigger: 'change' }],
  lab_id: [{ required: true, message: '请选择所在实验室', trigger: 'change' }],
}

function resetForm() {
  form.id = null
  form.name = ''
  form.model = ''
  form.serial_number = ''
  form.category_id = null
  form.lab_id = null
  form.status = 'available'
  form.purchase_date = ''
  form.price = null
  form.description = ''
  form.image = null
  formRef.value?.clearValidate()
}

function openCreate() {
  dialogMode.value = 'create'
  resetForm()
  dialogVisible.value = true
}

function openEdit(row) {
  dialogMode.value = 'edit'
  resetForm()
  Object.assign(form, {
    id: row.id,
    name: row.name,
    model: row.model,
    serial_number: row.serial_number,
    category_id: row.category_id,
    lab_id: row.lab_id,
    status: row.status,
    purchase_date: row.purchase_date || '',
    price: row.price,
    description: row.description || '',
    image: row.image,
  })
  dialogVisible.value = true
}

async function submitForm() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }

  submitting.value = true
  try {
    const payload = {
      name: form.name,
      model: form.model,
      serial_number: form.serial_number,
      category_id: form.category_id,
      lab_id: form.lab_id,
      status: form.status,
      purchase_date: form.purchase_date || null,
      price: form.price,
      description: form.description || null,
    }

    if (dialogMode.value === 'create') {
      const created = await equipmentApi.createEquipment(payload)
      ElMessage.success('设备创建成功')
      dialogVisible.value = false
      refresh()
      // 新增后如果选了图片，提示用户去编辑里上传（因为上传接口需要设备 ID）
      if (pendingImageFile.value && created?.id) {
        await uploadImage(created.id, pendingImageFile.value)
        pendingImageFile.value = null
      }
    } else {
      await equipmentApi.updateEquipment(form.id, payload)
      ElMessage.success('设备信息已更新')
      dialogVisible.value = false
      refresh()
    }
  } catch {
    // 拦截器已提示
  } finally {
    submitting.value = false
  }
}

// ---------------------------------------------------------------------------
//  图片上传
// ---------------------------------------------------------------------------
const uploadRef = ref(null)
const uploading = ref(false)
// 新增设备时图片还没法上传（没有设备ID），先暂存文件，创建成功后再传
const pendingImageFile = ref(null)
// 本地预览用的临时 URL
const previewUrl = ref('')

const MAX_SIZE_MB = 5
const ALLOWED_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']

/** 选择文件前的校验（给用户即时反馈，真正的安全校验在后端） */
function beforeUpload(file) {
  if (!ALLOWED_TYPES.includes(file.type)) {
    ElMessage.error('只支持 JPG / PNG / WebP / GIF 格式的图片')
    return false
  }
  if (file.size > MAX_SIZE_MB * 1024 * 1024) {
    ElMessage.error(`图片大小不能超过 ${MAX_SIZE_MB}MB（当前 ${(file.size / 1024 / 1024).toFixed(1)}MB）`)
    return false
  }

  // 本地预览：用 URL.createObjectURL 生成临时地址，不用等服务器返回
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = URL.createObjectURL(file)
  return true
}

/** 自定义上传：不走 el-upload 的默认请求，而是用我们自己的 axios 实例 */
async function handleUpload(options) {
  const file = options.file

  // 新增模式下设备还没有 ID，先暂存
  if (dialogMode.value === 'create' || !form.id) {
    pendingImageFile.value = file
    ElMessage.info('图片已选择，将在设备创建完成后自动上传')
    options.onSuccess?.({})
    return
  }

  await uploadImage(form.id, file, options)
}

async function uploadImage(equipmentId, file, options) {
  uploading.value = true
  try {
    const result = await equipmentApi.uploadEquipmentImage(equipmentId, file)
    // 后端返回的 url 已经可以直接使用
    form.image = result.url
    ElMessage.success('图片上传成功')
    options?.onSuccess?.(result)
    refresh()
  } catch {
    // 拦截器已提示（格式不符/过大等错误后端会明确说明）
    options?.onError?.(new Error('upload failed'))
    if (previewUrl.value) {
      URL.revokeObjectURL(previewUrl.value)
      previewUrl.value = ''
    }
  } finally {
    uploading.value = false
  }
}

async function handleRemoveImage() {
  if (!form.id || !form.image) {
    // 还没保存的设备，直接清掉预览
    form.image = null
    pendingImageFile.value = null
    if (previewUrl.value) {
      URL.revokeObjectURL(previewUrl.value)
      previewUrl.value = ''
    }
    return
  }

  try {
    await ElMessageBox.confirm('确定要删除该设备的图片吗？', '删除图片', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }

  try {
    await equipmentApi.deleteEquipmentImage(form.id)
    form.image = null
    ElMessage.success('图片已删除')
    refresh()
  } catch {
    // 拦截器已提示
  }
}

/** 当前显示的图片：优先用本地预览 */
const displayImage = computed(() => previewUrl.value || form.image)

// ---------------------------------------------------------------------------
//  删除设备
// ---------------------------------------------------------------------------
async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确定要删除设备「${row.name}」吗？\n\n` +
        '注意：如果该设备还有未完成的预约，删除会被拒绝。',
      '删除设备',
      { confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }

  try {
    await equipmentApi.deleteEquipment(row.id)
    ElMessage.success('设备已删除')
    refresh()
  } catch {
    // 拦截器已提示（有预约时会说明具体数量）
  }
}

// ---------------------------------------------------------------------------
//  初始化
// ---------------------------------------------------------------------------
onMounted(async () => {
  const [cats, labs] = await Promise.allSettled([
    equipmentApi.listCategories(),
    equipmentApi.listLaboratories(),
  ])
  if (cats.status === 'fulfilled') categories.value = cats.value || []
  if (labs.status === 'fulfilled') laboratories.value = labs.value || []
})
</script>

<template>
  <div class="page-container">
    <PageHeader title="设备管理" description="维护实验室设备信息，支持图片上传与状态管理">
      <template #actions>
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>新增设备
        </el-button>
        <el-button @click="refresh">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <!-- ==================== 筛选栏 ==================== -->
    <div class="filter-bar card">
      <el-input
        v-model="filters.keyword"
        placeholder="搜索设备名称 / 型号 / 序列号"
        clearable
        class="filter-search"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>

      <el-select v-model="filters.category_id" placeholder="全部分类" clearable class="filter-item">
        <el-option v-for="c in categories" :key="c.id" :label="c.name" :value="c.id" />
      </el-select>

      <el-select v-model="filters.lab_id" placeholder="全部实验室" clearable class="filter-item">
        <el-option v-for="l in laboratories" :key="l.id" :label="l.name" :value="l.id" />
      </el-select>

      <el-select v-model="filters.status" placeholder="全部状态" clearable class="filter-item">
        <el-option
          v-for="(config, value) in EQUIPMENT_STATUS"
          :key="value"
          :label="config.label"
          :value="value"
        />
      </el-select>
    </div>

    <!-- ==================== 表格 ==================== -->
    <div class="card table-card">
      <el-table v-loading="loading" :data="list" style="width: 100%">
        <el-table-column label="设备" min-width="220">
          <template #default="{ row }">
            <div class="equipment-cell">
              <EquipmentImage
                :src="row.image"
                :name="row.name"
                height="48px"
                radius="var(--radius-sm)"
                class="cell-thumb"
              />
              <div>
                <div class="cell-name">{{ row.name }}</div>
                <div class="text-muted">{{ row.model }}</div>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="serial_number" label="序列号" width="130" />

        <el-table-column label="分类" width="130">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" type="info">
              {{ row.category_name || '-' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="实验室" min-width="140">
          <template #default="{ row }">{{ row.lab_name || '-' }}</template>
        </el-table-column>

        <el-table-column label="价格" width="130" align="right">
          <template #default="{ row }">{{ formatPrice(row.price) }}</template>
        </el-table-column>

        <el-table-column label="统计" width="140">
          <template #default="{ row }">
            <span class="stats-cell">
              <el-tooltip content="浏览量"><span><el-icon><View /></el-icon>{{ row.browse_count || 0 }}</span></el-tooltip>
              <el-tooltip content="累计预约次数"><span><el-icon><Calendar /></el-icon>{{ row.booking_count || 0 }}</span></el-tooltip>
              <el-tooltip content="当前有效预约"><span><el-icon><Clock /></el-icon>{{ row.active_booking_count || 0 }}</span></el-tooltip>
            </span>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="getEquipmentStatus(row.status).type" size="small" effect="light">
              {{ getEquipmentStatus(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="150" align="center" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" size="small" link @click="openEdit(row)">编辑</el-button>
            <el-button type="danger" size="small" link @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="isEmpty" description="暂无设备数据" :image-size="80" />

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

    <!-- ==================== 新增/编辑弹窗 ==================== -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogMode === 'create' ? '新增设备' : '编辑设备'"
      width="720px"
      :close-on-click-modal="false"
      @closed="resetForm"
    >
      <div class="dialog-layout">
        <!-- 左侧：图片 -->
        <div class="image-section">
          <div class="image-preview">
            <EquipmentImage
              :src="displayImage"
              :name="form.name || '新设备'"
              height="180px"
            />
          </div>

          <el-upload
            ref="uploadRef"
            :show-file-list="false"
            :before-upload="beforeUpload"
            :http-request="handleUpload"
            accept="image/jpeg,image/png,image/webp,image/gif"
            class="upload-area"
          >
            <el-button :loading="uploading" style="width: 100%">
              <el-icon><Upload /></el-icon>
              {{ uploading ? '上传中…' : '选择图片' }}
            </el-button>
          </el-upload>

          <el-button
            v-if="displayImage"
            type="danger"
            plain
            size="small"
            style="width: 100%; margin-top: var(--space-2)"
            @click="handleRemoveImage"
          >
            移除图片
          </el-button>

          <div class="upload-tip">
            支持 JPG / PNG / WebP / GIF<br />
            最大 {{ MAX_SIZE_MB }}MB
            <br />
            <span class="text-muted">
              后端会读取文件头校验真实类型，<br />改扩展名无法绕过
            </span>
          </div>
        </div>

        <!-- 右侧：表单 -->
        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-width="90px"
          class="dialog-form"
        >
          <el-form-item label="设备名称" prop="name">
            <el-input v-model="form.name" placeholder="如：数字示波器" />
          </el-form-item>

          <el-form-item label="设备型号" prop="model">
            <el-input v-model="form.model" placeholder="如：Keysight DSOX1204G" />
          </el-form-item>

          <el-form-item label="序列号" prop="serial_number">
            <el-input v-model="form.serial_number" placeholder="唯一标识，如 EQ2026001" />
          </el-form-item>

          <el-form-item label="设备分类" prop="category_id">
            <el-select v-model="form.category_id" placeholder="请选择" style="width: 100%">
              <el-option v-for="c in categories" :key="c.id" :label="c.name" :value="c.id" />
            </el-select>
          </el-form-item>

          <el-form-item label="所在实验室" prop="lab_id">
            <el-select v-model="form.lab_id" placeholder="请选择" style="width: 100%">
              <el-option
                v-for="l in laboratories"
                :key="l.id"
                :label="`${l.name}（${l.location}）`"
                :value="l.id"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="设备状态">
            <el-select v-model="form.status" style="width: 100%">
              <el-option label="可用" value="available" />
              <el-option label="维护中" value="maintenance" />
            </el-select>
            <div class="form-tip">
              「使用中」由系统根据预约自动流转，不能手动设置
            </div>
          </el-form-item>

          <el-form-item label="采购日期">
            <el-date-picker
              v-model="form.purchase_date"
              type="date"
              placeholder="选择日期"
              value-format="YYYY-MM-DD"
              style="width: 100%"
            />
          </el-form-item>

          <el-form-item label="设备价值">
            <el-input-number
              v-model="form.price"
              :min="0"
              :max="99999999"
              :precision="2"
              :step="100"
              controls-position="right"
              style="width: 100%"
            />
          </el-form-item>

          <el-form-item label="设备描述">
            <el-input
              v-model="form.description"
              type="textarea"
              :rows="3"
              maxlength="2000"
              show-word-limit
              placeholder="设备技术参数、使用注意事项等"
            />
          </el-form-item>
        </el-form>
      </div>

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitForm">
          {{ dialogMode === 'create' ? '创建设备' : '保存修改' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ==================== 筛选栏 ==================== */
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding: var(--space-4);
  margin-bottom: var(--space-5);
}

.filter-search {
  width: 260px;
}

.filter-item {
  width: 160px;
}

/* ==================== 表格 ==================== */
.table-card {
  padding: var(--space-4);
}

.equipment-cell {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.cell-thumb {
  width: 48px;
  flex-shrink: 0;
}

.cell-name {
  font-weight: 600;
  color: var(--color-text-primary);
}

.stats-cell {
  display: flex;
  gap: var(--space-3);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}

.stats-cell span {
  display: flex;
  align-items: center;
  gap: 2px;
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  margin-top: var(--space-5);
}

/* ==================== 弹窗 ==================== */
.dialog-layout {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: var(--space-5);
}

.image-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.image-preview {
  border-radius: var(--radius-md);
  overflow: hidden;
  border: 1px solid var(--color-border-light);
}

.upload-area {
  width: 100%;
}

.upload-tip {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
  line-height: 1.7;
  text-align: center;
}

.dialog-form {
  max-height: 60vh;
  overflow-y: auto;
  padding-right: var(--space-2);
}

.form-tip {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
  line-height: 1.6;
}

/* ==================== 响应式 ==================== */
@media (max-width: 900px) {
  .filter-search,
  .filter-item {
    width: 100%;
  }

  .dialog-layout {
    grid-template-columns: 1fr;
  }
}
</style>
