/**
 * 统计相关接口。
 */
import http from './http'

export const statisticsApi = {
  /**
   * 首页仪表盘总览（聚合接口）
   *
   * 为什么要有这个接口？
   * 原项目首页要发 5 个请求（用户信息、待审核数、收藏数、周预约数、可用设备数），
   * 每个都是一次网络往返。合并成一个接口能显著减少首屏时间。
   */
  getDashboard() {
    return http.get('/statistics/dashboard')
  },

  /** 我的数据概览（普通用户可访问） */
  getMySummary() {
    return http.get('/statistics/my/summary')
  },

  // ---------- 以下为管理员接口 ----------

  /** 用户统计（含真实口径的"活跃用户"） */
  getUserStatistics() {
    return http.get('/statistics/users')
  },

  /** 设备统计与排行 */
  getEquipmentStatistics(params) {
    return http.get('/statistics/equipment', { params })
  },

  /** 设备状态分布（饼图） */
  getEquipmentStatusDistribution() {
    return http.get('/statistics/equipment/status')
  },

  /** 预约统计 */
  getBookingStatistics() {
    return http.get('/statistics/bookings')
  },

  /** 预约趋势（折线图） */
  getBookingTrend(days = 7) {
    return http.get('/statistics/bookings/trend', { params: { days } })
  },
}

export default statisticsApi
