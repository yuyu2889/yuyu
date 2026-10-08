/**
 * 认证相关接口。
 *
 * 设计原则：API 模块只负责"描述接口"，不做错误处理、不做提示 ——
 * 那些都在 http.js 的拦截器里统一做了。
 * 这样每个接口定义就是一行，非常清晰。
 */
import http from './http'

export const authApi = {
  /**
   * 用户注册
   * @param {{username, password, real_name, email, phone}} data
   * @returns {Promise<{token, expires_at, expires_in, user}>}
   */
  register(data) {
    return http.post('/auth/register', data)
  },

  /**
   * 用户登录
   * @param {{username, password}} data
   * @returns {Promise<{token, expires_at, expires_in, user}>}
   */
  login(data) {
    return http.post('/auth/login', data)
  },

  /** 退出登录（后端会撤销 Token） */
  logout() {
    return http.post('/auth/logout')
  },

  /** 获取当前登录用户（用于刷新页面后恢复登录态） */
  getCurrentUser() {
    return http.get('/auth/me')
  },
}

export default authApi
