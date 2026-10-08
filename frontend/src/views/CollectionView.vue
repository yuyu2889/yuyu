<script setup>
/**
 * 我的收藏页。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import equipmentApi from '@/api/equipment'
import { usePagination } from '@/composables/usePagination'
import { getEquipmentStatus, PAGINATION } from '@/config/constants'
import { formatPrice } from '@/utils/format'
import PageHeader from '@/components/common/PageHeader.vue'
import EquipmentImage from '@/components/common/EquipmentImage.vue'

const router = useRouter()

const {
  list,
  total,
  loading,
  pagination,
  isEmpty,
  refresh,
  handlePageChange,
  handleSizeChange,
} = usePagination(equipmentApi.listCollections, {
  pageSize: PAGINATION.DEFAULT_SIZE,
})

const removingId = ref(null)
const clearing = ref(false)

/** 可预约的设备数量（用于给用户提示） */
const availableCount = computed(
  () => list.value.filter((item) => item.status === 'available').length,
)

async function handleRemove(item) {
  removingId.value = item.id
  try {
    await equipmentApi.removeCollection(item.id)
    ElMessage.success('已取消收藏')
    refresh()
  } catch {
    // 拦截器已提示
  } finally {
    removingId.value = null
  }
}

async function handleClear() {
  try {
    await ElMessageBox.confirm(
      `确定要清空全部 ${total.value} 条收藏吗？此操作不可恢复。`,
      '清空收藏',
      { confirmButtonText: '确认清空', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }

  clearing.value = true
  try {
    const result = await equipmentApi.clearCollections()
    ElMessage.success(`已清空 ${result?.deleted ?? 0} 条收藏`)
    refresh()
  } catch {
    // 拦截器已提示
  } finally {
    clearing.value = false
  }
}

onMounted(() => {
  refresh()
})
</script>

<template>
  <div class="page-container">
    <PageHeader title="我的收藏" description="收藏的设备会出现在这里，方便快速找到常用设备">
      <template #actions>
        <el-button
          v-if="total > 0"
          type="danger"
          plain
          :loading="clearing"
          @click="handleClear"
        >
          <el-icon><Delete /></el-icon>清空收藏
        </el-button>
        <el-button type="primary" @click="router.push('/equipment')">
          <el-icon><Plus /></el-icon>浏览设备
        </el-button>
      </template>
    </PageHeader>

    <!-- 统计提示 -->
    <el-alert
      v-if="total > 0"
      type="info"
      :closable="false"
      show-icon
      class="summary-alert"
    >
      <template #title>
        共收藏 {{ total }} 台设备，其中
        <strong class="highlight">{{ availableCount }}</strong>
        台当前可用
      </template>
    </el-alert>

    <!-- 加载态 -->
    <div v-if="loading" class="card" style="padding: var(--space-6)">
      <el-skeleton :rows="4" animated />
    </div>

    <!-- 空状态 -->
    <div v-else-if="isEmpty" class="card empty-wrapper">
      <el-empty description="还没有收藏任何设备">
        <el-button type="primary" @click="router.push('/equipment')">
          去浏览设备
        </el-button>
      </el-empty>
    </div>

    <!-- 收藏列表 -->
    <div v-else class="collection-grid">
      <div
        v-for="item in list"
        :key="item.collection_id"
        class="collection-card card animate-in"
      >
        <div class="card-image" @click="router.push(`/equipment/${item.id}`)">
          <EquipmentImage :src="item.image" :name="item.name" height="150px" radius="0" />
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
          <h3 class="card-title" @click="router.push(`/equipment/${item.id}`)">
            {{ item.name }}
          </h3>
          <div class="card-model">{{ item.model }}</div>

          <div class="card-meta">
            <el-tag size="small" effect="plain" type="info">
              {{ item.category_name || '未分类' }}
            </el-tag>
            <span v-if="item.lab_name" class="meta-item">
              <el-icon><Location /></el-icon>{{ item.lab_name }}
            </span>
          </div>

          <div class="card-price">{{ formatPrice(item.price) }}</div>
          <div class="collected-time">
            收藏于 {{ item.collected_at?.slice(0, 10) || '-' }}
          </div>
        </div>

        <div class="card-footer">
          <el-button
            v-if="item.status === 'available'"
            type="primary"
            size="small"
            @click="router.push({ path: `/equipment/${item.id}`, query: { action: 'book' } })"
          >
            立即预约
          </el-button>
          <el-button v-else size="small" disabled>当前不可预约</el-button>

          <el-button
            type="danger"
            size="small"
            plain
            :loading="removingId === item.id"
            @click="handleRemove(item)"
          >
            取消收藏
          </el-button>
        </div>
      </div>
    </div>

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
</template>

<style scoped>
.summary-alert {
  margin-bottom: var(--space-5);
}

.highlight {
  color: var(--color-success);
  font-size: var(--font-md);
}

.empty-wrapper {
  padding: var(--space-10);
}

.collection-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: var(--space-4);
}

.collection-card {
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: transform var(--transition-fast), box-shadow var(--transition-base);
}

.collection-card:hover {
  transform: translateY(-3px);
  box-shadow: var(--shadow-lg);
}

.card-image {
  position: relative;
  cursor: pointer;
}

.status-badge {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2);
}

.card-body {
  padding: var(--space-4);
  flex: 1;
}

.card-title {
  font-size: var(--font-md);
  font-weight: 600;
  margin-bottom: var(--space-1);
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: color var(--transition-fast);
}

.card-title:hover {
  color: var(--color-primary);
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

.card-price {
  font-size: var(--font-md);
  font-weight: 700;
  color: var(--color-primary);
  font-variant-numeric: tabular-nums;
}

.collected-time {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
  margin-top: 2px;
}

.card-footer {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--color-border-light);
  background: var(--color-bg-hover);
}

.card-footer .el-button {
  flex: 1;
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  margin-top: var(--space-6);
}
</style>
