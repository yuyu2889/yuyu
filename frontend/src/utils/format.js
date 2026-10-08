/**
 * 通用格式化与工具函数。
 *
 * 为什么集中在这里？
 * 原项目把这些函数散落在多个文件里（helpers.js、各个组件内部），
 * 结果同一个"格式化日期"的逻辑有三份不同实现，格式还不一致
 * （有的显示 2026-10-08，有的显示 2026/10/08，有的显示 10-08）。
 * 集中后格式自动统一。
 */

/**
 * 格式化日期。
 * @param {string|Date} value
 * @param {string} format 'date' | 'datetime' | 'time' | 'short'
 */
export function formatDate(value, format = 'date') {
  if (!value) return '-'

  const date = value instanceof Date ? value : new Date(value)
  // 无效日期兜底（后端可能返回 null 或格式不对的字符串）
  if (Number.isNaN(date.getTime())) return '-'

  const pad = (n) => String(n).padStart(2, '0')
  const y = date.getFullYear()
  const m = pad(date.getMonth() + 1)
  const d = pad(date.getDate())
  const hh = pad(date.getHours())
  const mm = pad(date.getMinutes())

  switch (format) {
    case 'datetime':
      return `${y}-${m}-${d} ${hh}:${mm}`
    case 'time':
      return `${hh}:${mm}`
    case 'short':
      return `${m}-${d}`
    default:
      return `${y}-${m}-${d}`
  }
}

/**
 * 时间字符串截断成 HH:mm。
 * 后端返回的 time 类型是 "09:00:00"，界面上一般只显示 "09:00"。
 */
export function formatTime(value) {
  if (!value) return '-'
  const str = String(value)
  // 已经是 HH:mm 就不用处理
  const match = str.match(/^(\d{2}):(\d{2})/)
  return match ? `${match[1]}:${match[2]}` : str
}

/**
 * 相对时间描述（"3 分钟前"、"2 天后"）。
 * 用于让时间更有"温度"，比干巴巴的时间戳易读。
 */
export function formatRelativeTime(value) {
  if (!value) return '-'
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return '-'

  const diffMs = date.getTime() - Date.now()
  const absDiff = Math.abs(diffMs)
  const suffix = diffMs < 0 ? '前' : '后'

  const minute = 60 * 1000
  const hour = 60 * minute
  const day = 24 * hour
  const month = 30 * day
  const year = 365 * day

  if (absDiff < minute) return '刚刚'
  if (absDiff < hour) return `${Math.floor(absDiff / minute)} 分钟${suffix}`
  if (absDiff < day) return `${Math.floor(absDiff / hour)} 小时${suffix}`
  if (absDiff < month) return `${Math.floor(absDiff / day)} 天${suffix}`
  if (absDiff < year) return `${Math.floor(absDiff / month)} 个月${suffix}`
  return `${Math.floor(absDiff / year)} 年${suffix}`
}

/**
 * 计算两个时间之间的时长（用于预约时长展示）。
 * @returns {string} 如 "2 小时"、"1.5 小时"、"30 分钟"
 */
export function calcDuration(startTime, endTime) {
  if (!startTime || !endTime) return '-'

  const toMinutes = (t) => {
    const [h, m] = String(t).split(':').map(Number)
    return (h || 0) * 60 + (m || 0)
  }

  const minutes = toMinutes(endTime) - toMinutes(startTime)
  if (minutes <= 0) return '-'
  if (minutes < 60) return `${minutes} 分钟`

  const hours = minutes / 60
  // 整数小时不显示小数点
  return Number.isInteger(hours) ? `${hours} 小时` : `${hours.toFixed(1)} 小时`
}

/** 格式化金额 */
export function formatPrice(value) {
  if (value === null || value === undefined || value === '') return '-'
  const num = Number(value)
  if (Number.isNaN(num)) return '-'
  // 用千分位分隔，更易读
  return `¥${num.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/**
 * 格式化大数字（1000 → 1.0k）。
 * 统计卡片上数字很大时用，避免撑破布局。
 */
export function formatNumber(value) {
  const num = Number(value)
  if (Number.isNaN(num)) return '0'
  if (num < 1000) return String(num)
  if (num < 10000) return `${(num / 1000).toFixed(1)}k`
  if (num < 1000000) return `${(num / 10000).toFixed(1)}w`
  return `${(num / 1000000).toFixed(1)}M`
}

/**
 * 防抖。
 * 用于搜索框：用户连续输入时不必每敲一个字都发请求。
 */
export function debounce(fn, delay = 300) {
  let timer = null
  return function (...args) {
    clearTimeout(timer)
    timer = setTimeout(() => fn.apply(this, args), delay)
  }
}

/**
 * 深拷贝（仅处理纯数据对象，够本项目用）。
 * 用 structuredClone 如果浏览器支持，否则降级到 JSON 方式。
 */
export function deepClone(obj) {
  if (obj === null || typeof obj !== 'object') return obj
  if (typeof structuredClone === 'function') {
    try {
      return structuredClone(obj)
    } catch {
      // 含有函数等不可克隆的值时降级
    }
  }
  return JSON.parse(JSON.stringify(obj))
}

/**
 * 生成"今天 + N 天"的日期字符串（用于日期选择器的快捷选项）
 */
export function dateOffset(days) {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return formatDate(d)
}

/**
 * 判断某个日期是否是过去（用于前端提前提示，避免白跑一次请求）
 */
export function isPastDate(dateStr) {
  if (!dateStr) return false
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const target = new Date(dateStr)
  target.setHours(0, 0, 0, 0)
  return target < today
}
