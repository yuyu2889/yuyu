<script setup>
/**
 * 用户管理页（管理员）。
 *
 * 设计要点：
 * 1. **防呆设计**：当前登录的管理员自己那一行，禁用所有危险操作按钮
 *    （禁用/删除/降级），并且给出提示。
 *    后端也做了同样的校验（双保险），这里禁用按钮是为了避免无意义的请求。
 * 2. **操作后局部刷新**：只重新拉取列表，不整页刷新。
 * 3. 删除是不可逆的物理删除，所以用输入确认的方式增强操作成本。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import userApi from '@/api/user'
import { useAuthStore } from '@/stores/auth'
import { usePagination } from '@/composables/usePagination'
import { ROLE_OPTIONS, USER_STATUS, getUserStatus, PAGINATION } from '@/config/constants'
import { formatDate } from '@/utils/format'
import PageHeader from '@/components/common/PageHeader.vue'

const auth = useAuthStore()

// ---------------------------------------------------------------------------
//  列表
// ---------------------------------------------------------------------------
const filters = reactive({
  keyword: '',
  status: '',
  role: '',
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
} = usePagination(userApi.listUsers, {
  filters,
  pageSize: PAGINATION.DEFAULT_SIZE,
})

/** 判断某一行是否是当前登录用户自己 */
function isSelf(row) {
  return row.id === auth.state.user?.id
}

// ---------------------------------------------------------------------------
//  启用 / 禁用
// ---------------------------------------------------------------------------
async function toggleStatus(row) {
  const isDisabling = row.status === 'active'
  const actionText = isDisabling ? '禁用' : '启用'

  try {
    await ElMessageBox.confirm(
      isDisabling
        ? `确定要禁用用户「${row.real_name}」吗？\n\n禁用后该用户当前所有登录状态会立即失效，无法再访问系统。`
        : `确定要启用用户「${row.real_name}」吗？`,
      `${actionText}用户`,
      {
        confirmButtonText: `确认${actionText}`,
        cancelButtonText: '取消',
        type: isDisabling ? 'warning' : 'info',
      },
    )
  } catch {
    return
  }

  try {
    await userApi.changeUserStatus(row.id, {
      status: isDisabling ? 'disabled' : 'active',
    })
    ElMessage.success(`已${actionText}用户「${row.real_name}」`)
    refresh()
  } catch {
    // 拦截器已提示
  }
}

// ---------------------------------------------------------------------------
//  授予 / 撤销管理员
// ---------------------------------------------------------------------------
async function toggleAdmin(row) {
  const isGranting = !row.is_admin
  const actionText = isGranting ? '授予管理员权限' : '撤销管理员权限'

  try {
    await ElMessageBox.confirm(
      isGranting
        ? `确定要授予「${row.real_name}」管理员权限吗？\n\n管理员可以审核预约、管理设备和其他用户。`
        : `确定要撤销「${row.real_name}」的管理员权限吗？\n\n撤销后该用户将变为普通用户。`,
      actionText,
      {
        confirmButtonText: '确认',
        cancelButtonText: '取消',
        type: isGranting ? 'warning' : 'info',
      },
    )
  } catch {
    return
  }

  try {
    await userApi.changeUserRole(row.id, isGranting ? 'admin' : 'student')
    ElMessage.success(`已${actionText}`)
    refresh()
  } catch {
    // 拦截器已提示（比如"系统至少需要保留一名管理员"）
  }
}

// ---------------------------------------------------------------------------
//  删除用户
// ---------------------------------------------------------------------------
const deleting = ref(false)

async function handleDelete(row) {
  // 用输入用户名确认的方式，避免误删（删除是不可逆的）
  let input = ''
  try {
    const { value } = await ElMessageBox.prompt(
      `删除用户是不可恢复的操作，该用户的所有预约和收藏记录也会一并删除。\n\n` +
        `请输入用户名「${row.username}」以确认删除：`,
      '危险操作确认',
      {
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        type: 'error',
        inputPattern: new RegExp(`^${row.username}$`),
        inputErrorMessage: '输入的用户名不匹配',
      },
    )
    input = value
  } catch {
    return
  }

  if (input !== row.username) return

  deleting.value = true
  try {
    await userApi.deleteUser(row.id)
    ElMessage.success(`用户「${row.real_name}」已删除`)
    refresh()
  } catch {
    // 拦截器已提示
  } finally {
    deleting.value = false
  }
}

// ---------------------------------------------------------------------------
//  详情弹窗
// ---------------------------------------------------------------------------
const detailVisible = ref(false)
const detailUser = ref(null)

function openDetail(row) {
  detailUser.value = row
  detailVisible.value = true
}

// ---------------------------------------------------------------------------
//  统计
// ---------------------------------------------------------------------------
const stats = ref({})

async function loadStats() {
  try {
    const statisticsApi = (await import('@/api/statistics')).default
    stats.value = await statisticsApi.getUserStatistics()
  } catch {
    stats.value = {}
  }
}

onMounted(() => {
  loadStats()
})
</script>

<template>
  <div class="page-container">
    <PageHeader title="用户管理" description="管理系统用户、角色权限与账号状态">
      <template #actions>
        <el-button @click="refresh">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </template>
    </PageHeader>

    <!-- ==================== 统计概览 ==================== -->
    <div class="overview-grid">
      <div class="overview-card card">
        <div class="overview-value">{{ stats.total_users ?? 0 }}</div>
        <div class="overview-label">用户总数</div>
      </div>
      <div class="overview-card card">
        <div class="overview-value">{{ stats.active_users_30d ?? 0 }}</div>
        <div class="overview-label">
          近 30 天活跃
          <el-tooltip content="基于每次访问时更新的 last_used_at 字段统计，是真实的活跃口径">
            <el-icon class="info-icon"><QuestionFilled /></el-icon>
          </el-tooltip>
        </div>
      </div>
      <div class="overview-card card">
        <div class="overview-value">{{ stats.online_sessions ?? 0 }}</div>
        <div class="overview-label">当前在线</div>
      </div>
      <div class="overview-card card">
        <div class="overview-value">{{ stats.admin_count ?? 0 }}</div>
        <div class="overview-label">管理员</div>
      </div>
      <div class="overview-card card">
        <div class="overview-value">{{ stats.new_users_7d ?? 0 }}</div>
        <div class="overview-label">近 7 天新增</div>
      </div>
      <div class="overview-card card">
        <div class="overview-value">{{ stats.disabled_users ?? 0 }}</div>
        <div class="overview-label">已禁用</div>
      </div>
    </div>

    <!-- ==================== 筛选栏 ==================== -->
    <div class="filter-bar card">
      <el-input
        v-model="filters.keyword"
        placeholder="搜索用户名 / 姓名 / 邮箱 / 手机号"
        clearable
        class="filter-search"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>

      <el-select v-model="filters.status" placeholder="全部状态" clearable class="filter-item">
        <el-option label="正常" value="active" />
        <el-option label="已禁用" value="disabled" />
      </el-select>

      <el-select v-model="filters.role" placeholder="全部角色" clearable class="filter-item">
        <el-option
          v-for="role in ROLE_OPTIONS"
          :key="role.value"
          :label="role.label"
          :value="role.value"
        />
      </el-select>
    </div>

    <!-- ==================== 表格 ==================== -->
    <div class="card table-card">
      <el-table v-loading="loading" :data="list" style="width: 100%">
        <el-table-column label="用户" min-width="200">
          <template #default="{ row }">
            <div class="user-cell">
              <el-avatar :size="36" class="user-avatar">
                {{ (row.real_name || row.username || '?').charAt(0) }}
              </el-avatar>
              <div>
                <div class="user-name">
                  {{ row.real_name }}
                  <el-tag v-if="isSelf(row)" size="small" type="primary" effect="plain">
                    当前登录
                  </el-tag>
                </div>
                <div class="text-muted">@{{ row.username }}</div>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="email" label="邮箱" min-width="180" show-overflow-tooltip />
        <el-table-column prop="phone" label="手机号" width="130" />

        <el-table-column label="角色" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.is_admin" type="danger" size="small" effect="light">
              管理员
            </el-tag>
            <el-tag v-else type="primary" size="small" effect="plain">
              {{ row.roles?.[0]?.name || '普通用户' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="收藏数" width="90" align="center">
          <template #default="{ row }">
            <span class="text-muted">{{ row.collection_count ?? 0 }}</span>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="getUserStatus(row.status).type" size="small" effect="light">
              {{ getUserStatus(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="注册时间" width="120">
          <template #default="{ row }">
            <span class="text-muted">{{ formatDate(row.created_at) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="220" align="center" fixed="right">
          <template #default="{ row }">
            <!-- 对自己那一行禁用所有危险操作（防呆） -->
            <template v-if="isSelf(row)">
              <el-tooltip content="不能对自己执行该操作">
                <span class="disabled-actions">无法操作当前账号</span>
              </el-tooltip>
            </template>
            <template v-else>
              <el-button
                :type="row.status === 'active' ? 'warning' : 'success'"
                size="small"
                link
                @click="toggleStatus(row)"
              >
                {{ row.status === 'active' ? '禁用' : '启用' }}
              </el-button>
              <el-button
                :type="row.is_admin ? 'info' : 'danger'"
                size="small"
                link
                @click="toggleAdmin(row)"
              >
                {{ row.is_admin ? '撤管' : '授权' }}
              </el-button>
              <el-button type="danger" size="small" link @click="handleDelete(row)">
                删除
              </el-button>
            </template>
            <el-button size="small" link @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="isEmpty" description="没有符合条件的用户" :image-size="80" />

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

    <!-- ==================== 详情弹窗 ==================== -->
    <el-dialog v-model="detailVisible" title="用户详情" width="520px">
      <el-descriptions v-if="detailUser" :column="1" border>
        <el-descriptions-item label="用户ID">{{ detailUser.id }}</el-descriptions-item>
        <el-descriptions-item label="用户名">{{ detailUser.username }}</el-descriptions-item>
        <el-descriptions-item label="真实姓名">{{ detailUser.real_name }}</el-descriptions-item>
        <el-descriptions-item label="邮箱">{{ detailUser.email }}</el-descriptions-item>
        <el-descriptions-item label="手机号">{{ detailUser.phone }}</el-descriptions-item>
        <el-descriptions-item label="角色">
          <el-tag
            v-for="role in detailUser.roles || []"
            :key="role.code"
            size="small"
            :type="role.code === 'admin' ? 'danger' : 'primary'"
            effect="plain"
            style="margin-right: 4px"
          >
            {{ role.name }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="账号状态">
          <el-tag :type="getUserStatus(detailUser.status).type" size="small">
            {{ getUserStatus(detailUser.status).label }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="收藏设备数">
          {{ detailUser.collection_count ?? 0 }}
        </el-descriptions-item>
        <el-descriptions-item label="注册时间">
          {{ formatDate(detailUser.created_at, 'datetime') }}
        </el-descriptions-item>
      </el-descriptions>

      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ==================== 概览 ==================== */
.overview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--space-3);
  margin-bottom: var(--space-5);
}

.overview-card {
  padding: var(--space-4);
  text-align: center;
}

.overview-value {
  font-size: var(--font-2xl);
  font-weight: 700;
  color: var(--color-text-primary);
  font-variant-numeric: tabular-nums;
}

.overview-label {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  margin-top: var(--space-1);
}

.info-icon {
  color: var(--color-text-placeholder);
  cursor: help;
}

/* ==================== 筛选栏 ==================== */
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding: var(--space-4);
  margin-bottom: var(--space-5);
}

.filter-search {
  width: 280px;
}

.filter-item {
  width: 150px;
}

/* ==================== 表格 ==================== */
.table-card {
  padding: var(--space-4);
}

.user-cell {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.user-avatar {
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-dark));
  color: #fff;
  font-weight: 600;
  flex-shrink: 0;
}

.user-name {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: 600;
  color: var(--color-text-primary);
}

.disabled-actions {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  margin-top: var(--space-5);
}

/* ==================== 响应式 ==================== */
@media (max-width: 900px) {
  .filter-search,
  .filter-item {
    width: 100%;
  }
}
</style>
