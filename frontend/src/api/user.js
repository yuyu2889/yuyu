/**
 * 用户相关接口。
 */
import http from './http'

export const userApi = {
  // ---------- 个人资料 ----------

  /** 获取个人信息 */
  getProfile() {
    return http.get('/users/me')
  },

  /** 更新个人信息（支持部分更新，只传要改的字段） */
  updateProfile(data) {
    return http.put('/users/me', data)
  },

  /**
   * 修改密码
   * 注意：修改成功后所有登录凭证会被撤销，需要重新登录
   */
  changePassword(data) {
    return http.put('/users/me/password', data)
  },

  // ---------- 管理员功能 ----------

  /** 用户列表（管理员），支持关键字/状态/角色筛选 */
  listUsers(params) {
    return http.get('/users/', { params })
  },

  /** 启用/禁用用户（管理员） */
  changeUserStatus(userId, data) {
    return http.put(`/users/${userId}/status`, data)
  },

  /** 变更用户角色（管理员） */
  changeUserRole(userId, targetRole) {
    return http.put(`/users/${userId}/role`, null, { params: { target_role: targetRole } })
  },

  /** 删除用户（管理员） */
  deleteUser(userId) {
    return http.delete(`/users/${userId}`)
  },
}

export default userApi
