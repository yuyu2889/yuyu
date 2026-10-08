/**
 * HTTP 请求层（基于 axios）。
 *
 * ============================================================================
 *  这是前端架构里最重要的一层，原项目在这里有几个明显问题：
 * ============================================================================
 *
 *  原项目的问题：
 *  1. 每个 API 模块各自处理错误，有的地方弹 ElMessage，有的地方只 console.log
 *  2. Token 过期（401）时不会自动跳转登录页，用户会看到"数据加载失败"却不知道原因
 *  3. 后端返回的是 {code, message, data}，但代码里到处写 res.data.data 这种嵌套取值
 *  4. 图片路径要前端自己拼，而且三个地方用了三种拼法
 *
 *  V2 的解法：
 *  1. 拦截器统一处理错误（一个地方决定怎么提示用户）
 *  2. 401 自动清理登录态并跳转登录页（带 redirect 参数，登录后回到原页面）
 *  3. 响应拦截器直接返回 data 字段，业务代码不用层层解嵌套
 *  4. 图片 URL 由后端返回完整路径，前端不做任何拼装
 * ============================================================================
 */
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { API_PREFIX, ERROR_CODE, STORAGE_KEYS } from '@/config/constants'

// ---------------------------------------------------------------------------
//  创建实例
// ---------------------------------------------------------------------------
const http = axios.create({
  // 开发环境走 vite 的 proxy（配置见 vite.config.js），
  // 所以这里用相对路径即可 —— 不硬编码后端地址，
  // 换环境（本地/测试/生产）时不需要改代码，只改 vite 配置或 Nginx
  baseURL: API_PREFIX,
  timeout: 20000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// ---------------------------------------------------------------------------
//  请求拦截器：自动携带 Token
// ---------------------------------------------------------------------------
http.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem(STORAGE_KEYS.TOKEN)
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error),
)

// ---------------------------------------------------------------------------
//  防止重复提示
// ---------------------------------------------------------------------------
//  场景：页面初始化时并发发了 5 个请求，但 Token 恰好过期，
//  5 个请求都返回 401 → 如果每个都弹一次提示，会同时冒出 5 个弹窗。
//  所以用一个时间戳做节流：同一类错误 2 秒内只提示一次。
let lastErrorTime = 0
let lastErrorMessage = ''

function showErrorOnce(message) {
  const now = Date.now()
  if (message === lastErrorMessage && now - lastErrorTime < 2000) {
    return
  }
  lastErrorTime = now
  lastErrorMessage = message
  ElMessage.error(message)
}

// ---------------------------------------------------------------------------
//  401 处理：清理登录态并跳转登录页
// ---------------------------------------------------------------------------
let isRedirecting = false

function handleUnauthorized() {
  // 同样要防重：并发的多个 401 只需要跳转一次
  if (isRedirecting) return
  isRedirecting = true

  localStorage.removeItem(STORAGE_KEYS.TOKEN)
  localStorage.removeItem(STORAGE_KEYS.USER)

  const currentPath = window.location.pathname + window.location.search
  // 登录页本身不需要再跳转，否则会死循环
  if (!currentPath.startsWith('/login')) {
    ElMessage.warning('登录已过期，请重新登录')
    // 带上 redirect 参数，登录成功后能回到原来在看的页面
    window.location.href = `/login?redirect=${encodeURIComponent(currentPath)}`
  }

  // 给跳转留出时间，然后解除标记
  setTimeout(() => {
    isRedirecting = false
  }, 2000)
}

// ---------------------------------------------------------------------------
//  响应拦截器：统一解包 + 统一错误处理
// ---------------------------------------------------------------------------
http.interceptors.response.use(
  (response) => {
    const body = response.data

    // 后端统一格式：{code, message, data}
    // code === 0 表示成功
    if (body && typeof body.code !== 'undefined') {
      if (body.code === ERROR_CODE.SUCCESS) {
        // 关键：直接返回 data，业务代码里不用再写 res.data.data
        return body.data
      }

      // 业务层面的失败（HTTP 200 但 code != 0）
      // 比如"该时段已被预约"，这类错误需要提示用户
      const message = body.message || '操作失败'
      showErrorOnce(message)
      return Promise.reject(new Error(message))
    }

    // 不是标准格式（比如静态资源），原样返回
    return body
  },
  (error) => {
    // ---------- 网络层错误 ----------
    if (!error.response) {
      const message = error.code === 'ECONNABORTED'
        ? '请求超时，请检查网络后重试'
        : '网络连接失败，请检查后端服务是否已启动'
      showErrorOnce(message)
      return Promise.reject(error)
    }

    // ---------- HTTP 错误 ----------
    const { status, data } = error.response
    const message = data?.message || ''

    switch (status) {
      case 401:
        // 未登录或 Token 失效 —— 交给专门的处理函数
        handleUnauthorized()
        break

      case 403:
        showErrorOnce(message || '权限不足，无法执行该操作')
        break

      case 404:
        showErrorOnce(message || '请求的资源不存在')
        break

      case 409:
        // 业务冲突（如时间段已被预约），后端会给出明确提示，直接用
        showErrorOnce(message || '数据冲突，请刷新后重试')
        break

      case 422:
        // 参数校验失败，后端的 message 已经把每个字段的错误整理成人话了
        showErrorOnce(message || '提交的数据不符合要求')
        break

      case 429:
        showErrorOnce(message || '操作过于频繁，请稍后再试')
        break

      case 500:
      case 502:
      case 503:
        showErrorOnce(message || '服务器繁忙，请稍后重试')
        break

      default:
        showErrorOnce(message || `请求失败（${status}）`)
    }

    return Promise.reject(error)
  },
)

export default http
