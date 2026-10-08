/**
 * 分页 + 数据加载的组合式函数。
 *
 * ============================================================================
 *  为什么需要它？
 * ============================================================================
 *  原项目每个列表页都要重复写这一套：
 *    const list = ref([]); const total = ref(0)
 *    const page = ref(1); const pageSize = ref(10); const loading = ref(false)
 *    const fetch = async () => { loading.value = true; try { ... } finally { loading.value = false } }
 *    const handlePageChange = (p) => { page.value = p; fetch() }
 *    ...
 *  7 个列表页就有 7 份几乎一样的代码，改一处逻辑要改 7 遍。
 *
 *  这个组合式函数把这一整套封装起来，页面里只需要：
 *    const { list, total, loading, pagination, fetchData, handlePageChange } =
 *      usePagination((params) => equipmentApi.listEquipments(params), { filters })
 * ============================================================================
 */
import { computed, reactive, ref, watch } from 'vue'
import { PAGINATION } from '@/config/constants'

/**
 * @param {Function} fetcher 数据获取函数，签名为 (params) => Promise<PageData>
 *                           PageData 形如 { list, total, page, page_size, total_pages, has_more }
 * @param {Object} options
 * @param {Object} options.filters 筛选条件（会被合并进请求参数）
 * @param {number} options.pageSize 每页条数
 * @param {boolean} options.immediate 是否立即加载
 */
export function usePagination(fetcher, options = {}) {
  const { filters = {}, pageSize = PAGINATION.DEFAULT_SIZE, immediate = true } = options

  const list = ref([])
  const total = ref(0)
  const loading = ref(false)
  // 错误信息（页面可以选择展示为空状态）
  const error = ref(null)

  const pagination = reactive({
    page: PAGINATION.DEFAULT_PAGE,
    page_size: pageSize,
  })

  /**
   * 加载数据。
   * @param {boolean} resetPage 是否重置到第一页（筛选条件变化时用）
   */
  async function fetchData(resetPage = false) {
    if (resetPage) {
      pagination.page = 1
    }

    loading.value = true
    error.value = null

    try {
      const params = {
        page: pagination.page,
        page_size: pagination.page_size,
        // 过滤掉空值，避免把 keyword= 这种空参数发给后端
        ...cleanParams(filters),
      }
      const data = await fetcher(params)
      list.value = data?.list || []
      total.value = data?.total || 0
      return data
    } catch (err) {
      error.value = err?.message || '加载失败'
      list.value = []
      total.value = 0
      // 不再向上抛：拦截器已经提示过用户了，
      // 这里吞掉避免每个调用处都写 try/catch
      return null
    } finally {
      loading.value = false
    }
  }

  /** 页码变化 */
  function handlePageChange(page) {
    pagination.page = page
    fetchData()
  }

  /** 每页条数变化（要重置到第一页，否则可能超出范围） */
  function handleSizeChange(size) {
    pagination.page_size = size
    pagination.page = 1
    fetchData()
  }

  /** 筛选条件变化 → 回到第一页重新查 */
  function refresh() {
    return fetchData(true)
  }

  // 如果 filters 是响应式的，自动监听变化并重新查询
  // 用 deep 监听能捕获对象内部属性的变化（比如 filters.keyword 改了）
  if (filters && typeof filters === 'object') {
    watch(
      () => ({ ...filters }),
      () => {
        // 加个小防抖：用户连续输入关键字时不必每敲一个字就查一次
        // （这里用简单的实现，不用 lodash）
        scheduleRefresh()
      },
      { deep: true },
    )
  }

  let refreshTimer = null
  function scheduleRefresh() {
    clearTimeout(refreshTimer)
    refreshTimer = setTimeout(() => refresh(), 300)
  }

  /** 是否为空（用于展示空状态） */
  const isEmpty = computed(() => !loading.value && list.value.length === 0)

  if (immediate) {
    fetchData()
  }

  return {
    list,
    total,
    loading,
    error,
    pagination,
    isEmpty,
    fetchData,
    refresh,
    handlePageChange,
    handleSizeChange,
  }
}

/**
 * 清理请求参数：去掉 null / undefined / 空字符串。
 *
 * 为什么要做这个？
 * 因为后端对某些参数做了类型校验（比如 category_id 是 int），
 * 如果传了空字符串会报 422。前端把它过滤掉就没这个问题了。
 */
function cleanParams(params) {
  const result = {}
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== null && value !== undefined && value !== '') {
      result[key] = value
    }
  }
  return result
}

export { cleanParams }
