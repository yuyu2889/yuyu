<script setup>
/**
 * 设备列表页。
 *
 * 设计要点：
 * 1. **筛选条件用响应式对象**，配合 usePagination 的 watch 自动重新查询。
 *    不需要手写"改筛选条件 → 重新查"的逻辑。
 * 2. **视图模式切换**（卡片 / 列表），满足不同浏览习惯。
 * 3. **分页组件的 total 绑定后端返回的总数**，而不是当前页长度。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import equipmentApi from '@/api/equipment'
import { usePagination } from '@/composables/usePagination'
import { formatPrice } from '@/utils/format'
import { getEquipmentStatus, PAGINATION } from '@/config/constants'
import PageHeader from '@/components/common/PageHeader.vue'
import EquipmentImage from '@/components/common/EquipmentImage.vue'

const router = useRouter()

// ---------------------------------------------------------------------------
//  筛选条件
// ---------------------------------------------------------------------------
const filters = reactive({
  keyword: '',
  category_id: '',
  lab_id: '',
  status: '',
  order_by: 'id',
})

// 分类和实验室选项（用于下拉筛选）
const categories = ref([])
const laboratories = ref([])

// 视图模式：card（卡片）/ list（列表）
const viewMode = ref('card')

// ---------------------------------------------------------------------------
//  分页数据
// ---------------------------------------------------------------------------
const {
  list,
  total,
  loading,
  pagination,
  isEmpty,
  fetchData,
  refresh,
  handlePageChange,
  handleSizeChange,
} = usePagination(equipmentApi.listEquipments, {
  filters,
  pageSize: 12,
})

const hasActiveFilter = computed(
  () => Boolean(filters.keyword || filters.category_id || filters.lab_id || filters.status),
)

function resetFilters() {
  filters.keyword = ''
  filters.category_id = ''
  filters.lab_id = ''
  filters.status = ''
  filters.order_by = 'id'
  refresh()
}

function goToDetail(id) {
  router.push(`/equipment/${id}`)
}

/** 快捷预约：直接跳详情页并带上"打开预约弹窗"的标记 */
function goToBook(id) {
  router.push({ path: `/equipment/${id}`, query: { action: 'book' } })
}

onMounted(async () => {
  // 并行加载筛选选项（分类和实验室）
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
    <PageHeader title="设备浏览" description="查看实验室全部设备，选择空闲设备进行预约">
      <template #actions>
        <!-- 视图切换 -->
        <el-radio-group v-model="viewMode" size="default">
          <el-radio-button value="card">
            <el-icon><Grid /></el-icon>
          </el-radio-button>
          <el-radio-button value="list">
            <el-icon><List /></el-icon>
          </el-radio-button>
        </el-radio-group>
      </template>
    </PageHeader>

    <!-- ==================== 筛选栏 ==================== -->
    <div class="filter-bar card">
      <div class="filter-row">
        <el-input
          v-model="filters.keyword"
          placeholder="搜索设备名称 / 型号 / 序列号"
          clearable
          class="filter-search"
        >
          <template #prefix>
            <el-icon><Search /></el-icon>
          </template>
        </el-input>

        <el-select v-model="filters.category_id" placeholder="全部分类" clearable class="filter-item">
          <el-option
            v-for="cat in categories"
            :key="cat.id"
            :label="`${cat.name}（${cat.equipment_count}）`"
            :value="cat.id"
          />
        </el-select>

        <el-select v-model="filters.lab_id" placeholder="全部实验室" clearable class="filter-item">
          <el-option
            v-for="lab in laboratories"
            :key="lab.id"
            :label="lab.name"
            :value="lab.id"
          />
        </el-select>

        <el-select v-model="filters.status" placeholder="全部状态" clearable class="filter-item">
          <el-option label="可用" value="available" />
          <el-option label="使用中" value="busy" />
          <el-option label="维护中" value="maintenance" />
        </el-select>

        <el-select v-model="filters.order_by" class="filter-item">
          <el-option label="默认排序" value="id" />
          <el-option label="最多浏览" value="browse_count" />
          <el-option label="最多预约" value="booking_count" />
          <el-option label="名称排序" value="name" />
        </el-select>

        <el-button v-if="hasActiveFilter" @click="resetFilters">
          <el-icon><Refresh /></el-icon>重置
        </el-button>
      </div>

      <div v-if="!loading" class="filter-summary">
        共找到 <strong>{{ total }}</strong> 台设备
        <span v-if="hasActiveFilter" class="text-muted">（已筛选）</span>
      </div>
    </div>

    <!-- ==================== 加载态 ==================== -->
    <div v-if="loading" class="skeleton-grid">
      <div v-for="i in 8" :key="i" class="skeleton-card card">
        <el-skeleton animated>
          <template #template>
            <el-skeleton-item variant="image" style="width: 100%; height: 150px" />
            <div style="padding: 16px">
              <el-skeleton-item variant="h3" style="width: 60%" />
              <el-skeleton-item variant="text" style="margin-top: 8px" />
              <el-skeleton-item variant="text" style="width: 40%; margin-top: 8px" />
            </div>
          </template>
        </el-skeleton>
      </div>
    </div>

    <!-- ==================== 空状态 ==================== -->
    <div v-else-if="isEmpty" class="empty-wrapper card">
      <el-empty description="没有找到符合条件的设备">
        <el-button v-if="hasActiveFilter" type="primary" @click="resetFilters">
          清除筛选条件
        </el-button>
      </el-empty>
    </div>

    <!-- ==================== 卡片视图 ==================== -->
    <div v-else-if="viewMode === 'card'" class="card-grid">
      <div
        v-for="item in list"
        :key="item.id"
        class="equipment-card card animate-in"
        @click="goToDetail(item.id)"
      >
        <div class="card-image">
          <EquipmentImage :src="item.image" :name="item.name" height="160px" radius="0" />
          <el-tag
            class="status-badge"
            :type="getEquipmentStatus(item.status).type"
            effect="dark"
            size="small"
          >
            {{ getEquipmentStatus(item.status).label }}
          </el-tag>
        </div>

        <div class="card-body">
          <h3 class="card-title">{{ item.name }}</h3>
          <div class="card-model">{{ item.model }}</div>

          <div class="card-meta">
            <el-tag size="small" effect="plain" type="info">
              {{ item.category_name || '未分类' }}
            </el-tag>
            <span v-if="item.lab_name" class="meta-item">
              <el-icon><Location /></el-icon>{{ item.lab_name }}
            </span>
          </div>

          <div class="card-footer">
            <div class="card-stats">
              <span title="浏览量"><el-icon><View /></el-icon>{{ item.browse_count || 0 }}</span>
              <span title="预约次数"><el-icon><Calendar /></el-icon>{{ item.booking_count || 0 }}</span>
              <span title="收藏数"><el-icon><Star /></el-icon>{{ item.collection_count || 0 }}</span>
            </div>
            <el-button
              v-if="item.status === 'available'"
              type="primary"
              size="small"
              @click.stop="goToBook(item.id)"
            >
              预约
            </el-button>
            <el-button v-else size="small" disabled>不可预约</el-button>
          </div>
        </div>
      </div>
    </div>

    <!-- ==================== 列表视图 ==================== -->
    <div v-else class="list-view card">
      <el-table :data="list" style="width: 100%" @row-click="(row) => goToDetail(row.id)">
        <el-table-column label="设备" min-width="260">
          <template #default="{ row }">
            <div class="table-equipment">
              <EquipmentImage
                :src="row.image"
                :name="row.name"
                height="48px"
                radius="var(--radius-sm)"
                class="table-thumb"
              />
              <div>
                <div class="table-name">{{ row.name }}</div>
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

        <el-table-column label="价格" width="120" align="right">
          <template #default="{ row }">{{ formatPrice(row.price) }}</template>
        </el-table-column>

        <el-table-column label="统计" width="150">
          <template #default="{ row }">
            <span class="table-stats">
              <el-icon><View /></el-icon>{{ row.browse_count || 0 }}
              <el-icon><Calendar /></el-icon>{{ row.booking_count || 0 }}
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

        <el-table-column label="操作" width="100" align="center">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'available'"
              type="primary"
              link
              @click.stop="goToBook(row.id)"
            >
              预约
            </el-button>
            <span v-else class="text-muted">不可用</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- ==================== 分页 ==================== -->
    <div v-if="total > 0" class="pagination-wrapper">
      <el-pagination
        :current-page="pagination.page"
        :page-size="pagination.page_size"
        :page-sizes="PAGINATION.SIZE_OPTIONS"
        :total="total"
        layout="total, sizes, prev, pager, next, jumper"
        background
        @current-change="handlePageChange"
        @size-change="handleSizeChange"
      />
    </div>
  </div>
</template>

<style scoped>
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
}

.filter-search {
  width: 260px;
  flex-shrink: 0;
}

.filter-item {
  width: 160px;
  flex-shrink: 0;
}

.filter-summary {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-light);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}

.filter-summary strong {
  color: var(--color-primary);
  font-size: var(--font-md);
}

/* ==================== 骨架屏 ==================== */
.skeleton-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: var(--space-4);
}

.skeleton-card {
  overflow: hidden;
}

/* ==================== 空状态 ==================== */
.empty-wrapper {
  padding: var(--space-10);
}

/* ==================== 卡片视图 ==================== */
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: var(--space-4);
}

.equipment-card {
  overflow: hidden;
  cursor: pointer;
  transition: transform var(--transition-fast), box-shadow var(--transition-base);
  display: flex;
  flex-direction: column;
}

.equipment-card:hover {
  transform: translateY(-3px);
  box-shadow: var(--shadow-lg);
}

.card-image {
  position: relative;
}

.status-badge {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  /* 图片背景可能很亮，给个阴影保证徽章清晰可见 */
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2);
}

.card-body {
  padding: var(--space-4);
  flex: 1;
  display: flex;
  flex-direction: column;
}

.card-title {
  font-size: var(--font-md);
  font-weight: 600;
  margin-bottom: var(--space-1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-model {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
  margin-bottom: var(--space-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
  font-size: var(--font-xs);
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 2px;
  color: var(--color-text-secondary);
}

.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-top: auto;
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-light);
}

.card-stats {
  display: flex;
  gap: var(--space-3);
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
}

.card-stats span {
  display: flex;
  align-items: center;
  gap: 2px;
}

/* ==================== 列表视图 ==================== */
.list-view {
  overflow: hidden;
}

.table-equipment {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.table-thumb {
  width: 48px;
  flex-shrink: 0;
}

.table-name {
  font-weight: 600;
  color: var(--color-text-primary);
}

.table-stats {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}

.table-stats .el-icon {
  margin-right: 2px;
}

/* el-table 的行可以点击 */
:deep(.el-table__row) {
  cursor: pointer;
}

/* ==================== 分页 ==================== */
.pagination-wrapper {
  display: flex;
  justify-content: center;
  margin-top: var(--space-6);
}

/* ==================== 响应式 ==================== */
@media (max-width: 900px) {
  .filter-search {
    width: 100%;
  }

  .filter-item {
    width: calc(50% - var(--space-2));
  }

  .card-grid,
  .skeleton-grid {
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  }
}
</style>
