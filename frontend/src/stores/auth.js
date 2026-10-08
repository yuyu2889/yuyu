/**
 * 登录状态管理。
 *
 * ============================================================================
 *  为什么不用 Pinia / Vuex？
 * ============================================================================
 *  这个项目需要共享的状态只有"当前登录用户"这一项。
 *  引入一个状态管理库会带来：
 *    - 额外的依赖和学习成本
 *    - 一层间接（改状态要走 action/mutation）
 *  而用一个 `reactive` 对象 + 若干函数就完全够用，
 *  且逻辑一眼看得懂、调试简单。
 *
 *  这是"按需引入复杂度"的取舍 —— 不是 Pinia 不好，
 *  而是"为了一个用户对象引入整个状态管理库"不划算。
 *  如果将来状态变多（比如多标签页数据、复杂表单草稿），
 *  再迁移到 Pinia 也不迟。
 * ============================================================================
 */
import { computed, reactive } from 'vue'
import { STORAGE_KEYS } from '@/config/constants'
import authApi from '@/api/auth'

// ---------------------------------------------------------------------------
//  状态
// ---------------------------------------------------------------------------
const state = reactive({
  token: localStorage.getItem(STORAGE_KEYS.TOKEN) || '',
  user: readStoredUser(),
  // 是否已经尝试过恢复登录态（避免每次进页面都请求 /auth/me）
  initialized: false,
})

function readStoredUser() {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.USER)
    return raw ? JSON.parse(raw) : null
  } catch {
    // localStorage 里的数据可能被手动改坏，解析失败时静默忽略
    return null
  }
}

// ---------------------------------------------------------------------------
//  计算属性
// ---------------------------------------------------------------------------
const isLoggedIn = computed(() => Boolean(state.token))
const isAdmin = computed(() => Boolean(state.user?.is_admin))
const displayName = computed(() => state.user?.real_name || state.user?.username || '未登录')

// ---------------------------------------------------------------------------
//  操作
// ---------------------------------------------------------------------------

/**
 * 保存登录结果。
 * 同时写入 localStorage，这样刷新页面后登录态不会丢。
 */
function setAuth({ token, user }) {
  state.token = token
  state.user = user
  localStorage.setItem(STORAGE_KEYS.TOKEN, token)
  localStorage.setItem(STORAGE_KEYS.USER, JSON.stringify(user))
}

/** 清空登录态 */
function clearAuth() {
  state.token = ''
  state.user = null
  localStorage.removeItem(STORAGE_KEYS.TOKEN)
  localStorage.removeItem(STORAGE_KEYS.USER)
}

/** 登录 */
async function login(credentials) {
  const data = await authApi.login(credentials)
  setAuth(data)
  state.initialized = true
  return data
}

/** 注册（注册成功后直接是登录状态） */
async function register(form) {
  const data = await authApi.register(form)
  setAuth(data)
  state.initialized = true
  return data
}

/** 退出登录 */
async function logout() {
  try {
    // 通知后端撤销 Token（这样即使 Token 泄露也无法再用）
    if (state.token) {
      await authApi.logout()
    }
  } catch {
    // 后端调用失败也要清理本地状态，否则用户会卡在"登不出去"的状态
  } finally {
    clearAuth()
  }
}

/**
 * 恢复登录态 / 刷新用户信息。
 *
 * 使用场景：
 * 1. 应用启动时：如果 localStorage 里有 Token，用它换一次最新的用户信息
 *    （因为用户信息可能被别人改过，比如管理员改了你的角色）
 * 2. 修改资料后：刷新当前用户信息
 *
 * @param {boolean} force 是否强制请求（忽略 initialized 标记）
 */
async function fetchCurrentUser(force = false) {
  if (!state.token) {
    state.initialized = true
    return null
  }
  if (state.initialized && !force) {
    return state.user
  }

  try {
    const user = await authApi.getCurrentUser()
    state.user = user
    localStorage.setItem(STORAGE_KEYS.USER, JSON.stringify(user))
    return user
  } catch {
    // Token 已失效（比如过期、被撤销、用户被禁用）
    // 这里不弹提示 —— 因为 http 拦截器已经处理了 401 的提示和跳转
    clearAuth()
    return null
  } finally {
    state.initialized = true
  }
}

/** 局部更新用户信息（比如改完资料后，不用重新请求） */
function updateUser(partial) {
  state.user = { ...state.user, ...partial }
  localStorage.setItem(STORAGE_KEYS.USER, JSON.stringify(state.user))
}

export function useAuthStore() {
  return {
    // 状态（只读，通过下面的方法修改）
    state,
    // 计算属性
    isLoggedIn,
    isAdmin,
    displayName,
    // 方法
    login,
    register,
    logout,
    setAuth,
    clearAuth,
    fetchCurrentUser,
    updateUser,
  }
}
