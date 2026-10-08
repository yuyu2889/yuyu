/**
 * 前端配置常量。
 *
 * 设计要点：所有"魔法字符串"集中在这里，避免散落在各处。
 * 比如业务错误码、存储键名、分页默认值 —— 改的时候只改一处。
 */

/** 后端 API 前缀（与后端 settings.api_prefix 保持一致） */
export const API_PREFIX = '/api/v1'

/** localStorage 的存储键名 */
export const STORAGE_KEYS = {
  TOKEN: 'lab_token',
  USER: 'lab_user',
  THEME: 'lab_theme',
  SIDEBAR_COLLAPSED: 'lab_sidebar_collapsed',
}

/** 分页默认值 */
export const PAGINATION = {
  DEFAULT_PAGE: 1,
  DEFAULT_SIZE: 10,
  SIZE_OPTIONS: [10, 20, 50, 100],
}

/** 业务状态码（与后端 app/core/response.py 的 ErrorCode 对应） */
export const ERROR_CODE = {
  SUCCESS: 0,
  PARAM_ERROR: 1000,
  UNAUTHORIZED: 1001,
  TOKEN_EXPIRED: 1002,
  FORBIDDEN: 1003,
  NOT_FOUND: 1004,
  INTERNAL_ERROR: 1500,
  BOOKING_USER_CONFLICT: 4003,
  BOOKING_EQUIPMENT_CONFLICT: 4004,
}

/** 预约状态：值 → 展示配置 */
export const BOOKING_STATUS = {
  pending: { label: '待审核', type: 'warning', color: 'var(--color-warning)' },
  approved: { label: '已通过', type: 'success', color: 'var(--color-success)' },
  rejected: { label: '已拒绝', type: 'danger', color: 'var(--color-danger)' },
  cancelled: { label: '已取消', type: 'info', color: 'var(--color-info)' },
  completed: { label: '已完成', type: 'primary', color: 'var(--color-primary)' },
}

/** 设备状态：值 → 展示配置 */
export const EQUIPMENT_STATUS = {
  available: { label: '可用', type: 'success', color: 'var(--color-success)' },
  busy: { label: '使用中', type: 'warning', color: 'var(--color-warning)' },
  maintenance: { label: '维护中', type: 'danger', color: 'var(--color-danger)' },
}

/** 用户状态 */
export const USER_STATUS = {
  active: { label: '正常', type: 'success' },
  disabled: { label: '已禁用', type: 'danger' },
}

/** 角色选项（用于筛选下拉） */
export const ROLE_OPTIONS = [
  { value: 'student', label: '学生' },
  { value: 'teacher', label: '教师' },
  { value: 'admin', label: '管理员' },
]

/** 预约时间选择范围约束（与后端 schemas/booking.py 的规则保持一致） */
export const BOOKING_RULES = {
  DAY_START: '08:00',
  DAY_END: '22:00',
  MIN_DURATION_MINUTES: 30,
  MAX_DURATION_MINUTES: 8 * 60,
  MAX_ADVANCE_DAYS: 30,
  // 时间选择器的粒度（分钟）
  STEP_MINUTES: 30,
}

/** 查状态配置的辅助函数（带兜底，避免未知状态导致页面报错） */
export function getBookingStatus(status) {
  return BOOKING_STATUS[status] || { label: status || '未知', type: 'info', color: 'var(--color-info)' }
}

export function getEquipmentStatus(status) {
  return EQUIPMENT_STATUS[status] || { label: status || '未知', type: 'info', color: 'var(--color-info)' }
}

export function getUserStatus(status) {
  return USER_STATUS[status] || { label: status || '未知', type: 'info' }
}
