/**
 * 预约相关接口。
 */
import http from './http'

export const bookingApi = {
  /**
   * 冲突预检（不创建预约）
   *
   * 用途：用户选完时间段后，提交前先问一次"这个时段能用吗"，
   * 可以提前给出反馈，避免填完一堆信息才被拒绝。
   *
   * ⚠️ 预检结果仅供参考，不代表一定能预约成功
   * （预检到真正提交之间可能被别人抢先占用）。
   */
  checkConflict(data) {
    return http.post('/bookings/check-conflict', data)
  },

  /**
   * 创建预约
   * @param {{equipment_id, booking_date, start_time, end_time, purpose, notes}} data
   */
  createBooking(data) {
    return http.post('/bookings', data)
  },

  /** 我的预约列表 */
  listMyBookings(params) {
    return http.get('/bookings/my', { params })
  },

  /** 所有预约列表（管理员） */
  listAllBookings(params) {
    return http.get('/bookings', { params })
  },

  /** 预约详情（只能看自己的；管理员可看全部） */
  getBooking(bookingId) {
    return http.get(`/bookings/${bookingId}`)
  },

  /**
   * 审核预约（管理员）
   * @param {number} bookingId
   * @param {{result: 'approved'|'rejected', note?: string}} data
   */
  auditBooking(bookingId, data) {
    return http.post(`/bookings/${bookingId}/audit`, data)
  },

  /** 取消预约 */
  cancelBooking(bookingId, reason) {
    return http.put(`/bookings/${bookingId}/cancel`, { reason })
  },

  /** 待审核数量（管理员看全平台，普通用户看自己的） */
  getPendingCount() {
    return http.get('/bookings/stats/pending-count')
  },
}

export default bookingApi
