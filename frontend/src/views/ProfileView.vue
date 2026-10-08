<script setup>
/**
 * 个人中心：资料维护 + 修改密码。
 *
 * 设计要点：
 * 1. 两个 tab 用 el-tabs 切换，而不是两个页面 ——
 *    这两件事都属于"账号管理"，放一起符合用户心智。
 * 2. 修改密码后**必须重新登录**（后端会撤销所有 Token）。
 *    这里明确告诉用户这个后果，并自动跳转登录页，
 *    而不是让用户疑惑"为什么我改完密码就掉线了"。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import userApi from '@/api/user'
import statisticsApi from '@/api/statistics'
import { formatDate } from '@/utils/format'
import PageHeader from '@/components/common/PageHeader.vue'

const router = useRouter()
const auth = useAuthStore()

const activeTab = ref('profile')

// ---------------------------------------------------------------------------
//  个人资料
// ---------------------------------------------------------------------------
const profileFormRef = ref(null)
const profileLoading = ref(false)
const profileForm = reactive({
  real_name: '',
  email: '',
  phone: '',
})

const profileRules = {
  real_name: [
    { required: true, message: '请输入真实姓名', trigger: 'blur' },
    { min: 2, max: 20, message: '姓名长度为 2-20 个字符', trigger: 'blur' },
  ],
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '请输入正确的邮箱格式', trigger: 'blur' },
  ],
  phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    { pattern: /^1[3-9]\d{9}$/, message: '请输入正确的 11 位手机号', trigger: 'blur' },
  ],
}

// ---------------------------------------------------------------------------
//  修改密码
// ---------------------------------------------------------------------------
const passwordFormRef = ref(null)
const passwordLoading = ref(false)
const passwordForm = reactive({
  old_password: '',
  new_password: '',
  confirm_password: '',
})

const passwordRules = {
  old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 8, max: 72, message: '密码长度为 8-72 个字符', trigger: 'blur' },
    {
      // 自定义校验器：密码不能是纯数字或纯字母
      // 前端做这个校验是为了"提前提示"，真正的安全校验在后端
      validator: (rule, value, callback) => {
        if (!value) return callback()
        if (/^\d+$/.test(value)) return callback(new Error('密码不能是纯数字'))
        if (/^[a-zA-Z]+$/.test(value)) return callback(new Error('密码不能是纯字母'))
        callback()
      },
      trigger: 'blur',
    },
  ],
  confirm_password: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (rule, value, callback) => {
        if (value !== passwordForm.new_password) {
          return callback(new Error('两次输入的密码不一致'))
        }
        callback()
      },
      trigger: 'blur',
    },
  ],
}

// ---------------------------------------------------------------------------
//  个人统计
// ---------------------------------------------------------------------------
const myStats = ref({})

// ---------------------------------------------------------------------------
//  数据加载
// ---------------------------------------------------------------------------
async function loadProfile() {
  try {
    const data = await userApi.getProfile()
    profileForm.real_name = data.real_name || ''
    profileForm.email = data.email || ''
    profileForm.phone = data.phone || ''
  } catch {
    // 拦截器已提示
  }
}

async function loadMyStats() {
  try {
    myStats.value = await statisticsApi.getMySummary()
  } catch {
    myStats.value = {}
  }
}

// ---------------------------------------------------------------------------
//  提交
// ---------------------------------------------------------------------------
async function handleUpdateProfile() {
  try {
    await profileFormRef.value.validate()
  } catch {
    return
  }

  profileLoading.value = true
  try {
    const updated = await userApi.updateProfile({ ...profileForm })
    // 同步更新全局登录态，这样顶栏显示的名字会立即变化
    auth.updateUser(updated)
    ElMessage.success('个人信息已更新')
  } catch {
    // 拦截器已提示
  } finally {
    profileLoading.value = false
  }
}

async function handleChangePassword() {
  try {
    await passwordFormRef.value.validate()
  } catch {
    return
  }

  passwordLoading.value = true
  try {
    await userApi.changePassword({
      old_password: passwordForm.old_password,
      new_password: passwordForm.new_password,
    })

    // 关键：改密码后后端撤销了所有 Token，必须重新登录
    ElMessage.success('密码修改成功，请使用新密码重新登录')
    auth.clearAuth()
    // 给用户一点时间看到提示
    setTimeout(() => router.push({ name: 'Login' }), 1200)
  } catch {
    // 拦截器已提示
  } finally {
    passwordLoading.value = false
  }
}

/** 重置密码表单 */
function resetPasswordForm() {
  passwordFormRef.value?.resetFields()
}

onMounted(() => {
  loadProfile()
  loadMyStats()
})
</script>

<template>
  <div class="page-container">
    <PageHeader title="个人中心" description="维护个人资料与账号安全设置" />

    <div class="profile-layout">
      <!-- ==================== 左侧：账号卡片 ==================== -->
      <aside class="profile-aside card">
        <el-avatar :size="72" class="avatar">
          {{ auth.displayName.value.charAt(0) }}
        </el-avatar>
        <h3 class="name">{{ auth.displayName.value }}</h3>
        <div class="role">
          <el-tag v-if="auth.isAdmin.value" type="danger" effect="dark" size="small">
            管理员
          </el-tag>
          <el-tag v-else type="primary" effect="plain" size="small">普通用户</el-tag>
        </div>

        <el-divider />

        <div class="mini-stats">
          <div class="mini-stat">
            <div class="mini-value">{{ myStats.my_total_bookings ?? 0 }}</div>
            <div class="mini-label">预约总数</div>
          </div>
          <div class="mini-stat">
            <div class="mini-value">{{ myStats.my_collections ?? 0 }}</div>
            <div class="mini-label">收藏设备</div>
          </div>
          <div class="mini-stat">
            <div class="mini-value">{{ myStats.my_pending_bookings ?? 0 }}</div>
            <div class="mini-label">待审核</div>
          </div>
        </div>

        <el-divider />

        <div class="account-info">
          <div class="info-row">
            <span class="info-label">用户名</span>
            <span class="info-value">{{ auth.state.user?.username || '-' }}</span>
          </div>
          <div class="info-row">
            <span class="info-label">注册时间</span>
            <span class="info-value">
              {{ formatDate(auth.state.user?.created_at) }}
            </span>
          </div>
        </div>
      </aside>

      <!-- ==================== 右侧：表单区 ==================== -->
      <div class="profile-main card">
        <el-tabs v-model="activeTab">
          <!-- ---------- 个人资料 ---------- -->
          <el-tab-pane label="个人资料" name="profile">
            <el-form
              ref="profileFormRef"
              :model="profileForm"
              :rules="profileRules"
              label-width="90px"
              class="form"
            >
              <el-form-item label="用户名">
                <!-- 用户名不允许修改：它是登录凭证，改了会影响历史数据关联 -->
                <el-input :value="auth.state.user?.username" disabled />
                <div class="form-tip">用户名是登录凭证，不支持修改</div>
              </el-form-item>

              <el-form-item label="真实姓名" prop="real_name">
                <el-input v-model="profileForm.real_name" placeholder="请输入真实姓名" />
              </el-form-item>

              <el-form-item label="邮箱" prop="email">
                <el-input v-model="profileForm.email" placeholder="用于接收预约审核通知" />
                <div class="form-tip">预约状态变化会发送到该邮箱</div>
              </el-form-item>

              <el-form-item label="手机号" prop="phone">
                <el-input v-model="profileForm.phone" placeholder="请输入手机号" />
              </el-form-item>

              <el-form-item>
                <el-button type="primary" :loading="profileLoading" @click="handleUpdateProfile">
                  保存修改
                </el-button>
                <el-button @click="loadProfile">重置</el-button>
              </el-form-item>
            </el-form>
          </el-tab-pane>

          <!-- ---------- 修改密码 ---------- -->
          <el-tab-pane label="修改密码" name="password">
            <el-alert
              type="warning"
              :closable="false"
              show-icon
              title="修改密码后需要重新登录"
              description="出于安全考虑，修改密码后所有设备的登录状态都会失效，需要重新登录。"
              class="alert"
            />

            <el-form
              ref="passwordFormRef"
              :model="passwordForm"
              :rules="passwordRules"
              label-width="90px"
              class="form"
            >
              <el-form-item label="原密码" prop="old_password">
                <el-input
                  v-model="passwordForm.old_password"
                  type="password"
                  show-password
                  placeholder="请输入当前密码"
                />
              </el-form-item>

              <el-form-item label="新密码" prop="new_password">
                <el-input
                  v-model="passwordForm.new_password"
                  type="password"
                  show-password
                  placeholder="至少 8 位，建议包含字母和数字"
                />
              </el-form-item>

              <el-form-item label="确认密码" prop="confirm_password">
                <el-input
                  v-model="passwordForm.confirm_password"
                  type="password"
                  show-password
                  placeholder="请再次输入新密码"
                />
              </el-form-item>

              <el-form-item>
                <el-button
                  type="primary"
                  :loading="passwordLoading"
                  @click="handleChangePassword"
                >
                  确认修改
                </el-button>
                <el-button @click="resetPasswordForm">清空</el-button>
              </el-form-item>
            </el-form>
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>
  </div>
</template>

<style scoped>
.profile-layout {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: var(--space-5);
  align-items: start;
}

/* ==================== 左侧账号卡片 ==================== */
.profile-aside {
  padding: var(--space-6);
  text-align: center;
  position: sticky;
  top: var(--space-6);
}

.avatar {
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-dark));
  color: #fff;
  font-size: 30px;
  font-weight: 700;
  margin-bottom: var(--space-3);
}

.name {
  font-size: var(--font-lg);
  margin-bottom: var(--space-2);
}

.role {
  margin-bottom: var(--space-2);
}

.mini-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-2);
}

.mini-value {
  font-size: var(--font-lg);
  font-weight: 700;
  color: var(--color-primary);
  font-variant-numeric: tabular-nums;
}

.mini-label {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
}

.account-info {
  text-align: left;
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-1) 0;
  font-size: var(--font-sm);
}

.info-label {
  color: var(--color-text-secondary);
}

.info-value {
  color: var(--color-text-primary);
  font-weight: 500;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ==================== 右侧表单 ==================== */
.profile-main {
  padding: var(--space-6);
  min-height: 420px;
}

.form {
  max-width: 520px;
  margin-top: var(--space-4);
}

.form-tip {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
  line-height: 1.6;
  margin-top: 2px;
}

.alert {
  margin-bottom: var(--space-5);
}

/* ==================== 响应式 ==================== */
@media (max-width: 900px) {
  .profile-layout {
    grid-template-columns: 1fr;
  }

  .profile-aside {
    position: static;
  }
}
</style>
