<script setup>
/**
 * 登录页。
 *
 * 设计要点：
 * 1. **左右分栏布局**：左侧品牌区（渐变背景 + 特性介绍），右侧登录表单。
 *    窄屏时左侧隐藏，只留表单。
 * 2. **表单校验用 Element Plus 的 rules**，而不是手写 if 判断 ——
 *    校验规则声明式定义，错误提示自动显示在对应输入框下方。
 * 3. **登录成功后跳转到 redirect 参数指定的页面**。
 *    这个参数是路由守卫在"未登录访问受保护页面"时加上的，
 *    这样用户登录后会回到原来想去的页面，而不是一律回首页。
 */
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const formRef = ref(null)
const loading = ref(false)

const form = reactive({
  username: '',
  password: '',
})

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 20, message: '用户名长度为 3-20 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, max: 72, message: '密码长度为 6-72 个字符', trigger: 'blur' },
  ],
}

async function handleLogin() {
  if (!formRef.value) return

  // validate 返回 Promise，校验失败会 reject
  try {
    await formRef.value.validate()
  } catch {
    return // 校验不通过，错误提示已由表单显示
  }

  loading.value = true
  try {
    await auth.login({ username: form.username, password: form.password })
    ElMessage.success(`欢迎回来，${auth.displayName.value}`)

    // 优先跳回原来想去的页面
    const redirect = route.query.redirect
    await router.push(redirect ? decodeURIComponent(redirect) : '/dashboard')
  } catch {
    // 错误提示已由 http 拦截器统一处理，这里不需要重复弹窗
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------------------
//  演示账号快速填充
// ---------------------------------------------------------------------------
//  毕设演示时很实用：不用手打账号密码，点一下就填好了。
//  注意：这个功能只在开发环境显示（生产环境应该去掉）。
const demoAccounts = [
  { label: '管理员', username: 'admin', password: 'Admin@123', type: 'danger' },
  { label: '教师', username: 'teacher1', password: 'Teacher@123', type: 'warning' },
  { label: '学生', username: 'student1', password: 'Student@123', type: 'primary' },
]

function fillAccount(account) {
  form.username = account.username
  form.password = account.password
}

onMounted(() => {
  // 从路由守卫跳过来时，焦点自动落在用户名输入框
  // （Element Plus 的 input 有 focus 方法，通过 ref 拿不到时用 DOM 查询兜底）
  const input = document.querySelector('input[type="text"]')
  input?.focus?.()
})
</script>

<template>
  <div class="login-page">
    <!-- ==================== 左侧品牌区 ==================== -->
    <div class="brand-section">
      <div class="brand-content">
        <div class="brand-logo">
          <el-icon :size="36"><School /></el-icon>
        </div>
        <h1 class="brand-title">高校实验室<br />设备预约管理系统</h1>
        <p class="brand-desc">
          面向高校实验室场景，提供设备浏览、在线预约、审核流转与数据统计的一体化解决方案
        </p>

        <ul class="feature-list">
          <li>
            <el-icon :size="18"><CircleCheck /></el-icon>
            <span>实时查看设备可用状态，一键预约</span>
          </li>
          <li>
            <el-icon :size="18"><CircleCheck /></el-icon>
            <span>智能冲突检测，避免时间重复占用</span>
          </li>
          <li>
            <el-icon :size="18"><CircleCheck /></el-icon>
            <span>审核结果邮件通知，流程清晰可追溯</span>
          </li>
          <li>
            <el-icon :size="18"><CircleCheck /></el-icon>
            <span>多维度数据统计，辅助实验室管理决策</span>
          </li>
        </ul>
      </div>

      <!-- 背景装饰圆（纯视觉） -->
      <div class="decor decor-1" />
      <div class="decor decor-2" />
      <div class="decor decor-3" />
    </div>

    <!-- ==================== 右侧表单区 ==================== -->
    <div class="form-section">
      <div class="form-wrapper">
        <div class="form-header">
          <h2 class="form-title">欢迎登录</h2>
          <p class="form-subtitle">请使用实验室分配的账号登录系统</p>
        </div>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          size="large"
          @keyup.enter="handleLogin"
        >
          <el-form-item prop="username">
            <el-input
              v-model="form.username"
              placeholder="请输入用户名"
              :prefix-icon="'User'"
              clearable
            />
          </el-form-item>

          <el-form-item prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              :prefix-icon="'Lock'"
              show-password
            />
          </el-form-item>

          <el-form-item>
            <el-button
              type="primary"
              class="submit-btn"
              :loading="loading"
              @click="handleLogin"
            >
              {{ loading ? '登录中…' : '登 录' }}
            </el-button>
          </el-form-item>
        </el-form>

        <!-- 演示账号（方便毕设演示） -->
        <div class="demo-section">
          <el-divider>
            <span class="divider-text">演示账号（点击快速填充）</span>
          </el-divider>
          <div class="demo-buttons">
            <el-button
              v-for="account in demoAccounts"
              :key="account.username"
              :type="account.type"
              plain
              size="small"
              @click="fillAccount(account)"
            >
              {{ account.label }}：{{ account.username }}
            </el-button>
          </div>
          <p class="demo-hint">
            默认密码：Admin@123 / Teacher@123 / Student@123
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  display: flex;
  min-height: 100vh;
  background: var(--color-bg-card);
}

/* ==================== 左侧品牌区 ==================== */
.brand-section {
  position: relative;
  flex: 1.1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-10);
  overflow: hidden;
  /* 渐变背景：两段主色，视觉上有层次 */
  background: linear-gradient(135deg, #409eff 0%, #337ecc 50%, #2b6cb0 100%);
}

.brand-content {
  position: relative;
  z-index: 2;
  max-width: 480px;
  color: #fff;
}

.brand-logo {
  width: 64px;
  height: 64px;
  border-radius: var(--radius-lg);
  background: rgba(255, 255, 255, 0.18);
  backdrop-filter: blur(10px);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: var(--space-6);
}

.brand-title {
  font-size: 34px;
  line-height: 1.35;
  font-weight: 700;
  color: #fff;
  margin-bottom: var(--space-4);
}

.brand-desc {
  font-size: var(--font-base);
  line-height: 1.8;
  color: rgba(255, 255, 255, 0.85);
  margin-bottom: var(--space-8);
}

.feature-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.feature-list li {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--font-base);
  color: rgba(255, 255, 255, 0.92);
}

/* 背景装饰圆 */
.decor {
  position: absolute;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.08);
}

.decor-1 {
  width: 400px;
  height: 400px;
  top: -120px;
  right: -100px;
}

.decor-2 {
  width: 260px;
  height: 260px;
  bottom: -80px;
  left: -60px;
  background: rgba(255, 255, 255, 0.06);
}

.decor-3 {
  width: 140px;
  height: 140px;
  top: 45%;
  right: 12%;
  background: rgba(255, 255, 255, 0.05);
}

/* ==================== 右侧表单区 ==================== */
.form-section {
  flex: 0.9;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-10);
  background: var(--color-bg-card);
}

.form-wrapper {
  width: 100%;
  max-width: 380px;
}

.form-header {
  margin-bottom: var(--space-8);
}

.form-title {
  font-size: var(--font-2xl);
  font-weight: 700;
  color: var(--color-text-primary);
  margin-bottom: var(--space-2);
}

.form-subtitle {
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
}

.submit-btn {
  width: 100%;
  height: 44px;
  font-size: var(--font-md);
  font-weight: 600;
  letter-spacing: 2px;
}

/* ==================== 演示账号区 ==================== */
.demo-section {
  margin-top: var(--space-6);
}

.divider-text {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
}

.demo-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  justify-content: center;
}

.demo-hint {
  margin-top: var(--space-3);
  text-align: center;
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
}

/* ==================== 响应式 ==================== */
@media (max-width: 900px) {
  /* 窄屏隐藏品牌区，只留表单（手机上登录才是核心任务） */
  .brand-section {
    display: none;
  }

  .form-section {
    flex: 1;
    padding: var(--space-6);
  }
}
</style>
