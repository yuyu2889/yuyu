/**
 * 设备与收藏相关接口。
 *
 * 注意图片处理：后端返回的 image 字段已经是可直接使用的路径
 * （形如 /static/uploads/equipment/2026/10/xxx.jpg），
 * 前端直接用 <img :src="item.image"> 即可，**不需要任何拼接或猜测**。
 * 这是 V2 相对原项目最重要的改进之一。
 */
import http from './http'

export const equipmentApi = {
  // ---------- 分类 ----------

  /** 分类列表（后端有 Redis 缓存，可放心频繁调用） */
  listCategories() {
    return http.get('/categories')
  },

  /** 新增分类（管理员） */
  createCategory(data) {
    return http.post('/categories', data)
  },

  /** 删除分类（管理员，分类下有设备时会被拒绝） */
  deleteCategory(categoryId) {
    return http.delete(`/categories/${categoryId}`)
  },

  // ---------- 实验室 ----------

  /** 实验室列表 */
  listLaboratories() {
    return http.get('/laboratories')
  },

  /** 新增实验室（管理员） */
  createLaboratory(data) {
    return http.post('/laboratories', data)
  },

  // ---------- 设备 ----------

  /**
   * 设备列表（分页 + 多条件筛选）
   * @param {{page, page_size, keyword, category_id, lab_id, status, order_by}} params
   */
  listEquipments(params) {
    return http.get('/equipment', { params })
  },

  /** 所有可用设备（不分页，用于预约时的设备选择） */
  listAvailableEquipments() {
    return http.get('/equipment/available')
  },

  /**
   * 设备详情
   * 注意：每次调用会让浏览量 +1，而且登录状态下会额外返回 is_collected
   */
  getEquipmentDetail(equipmentId) {
    return http.get(`/equipment/${equipmentId}`)
  },

  /** 新增设备（管理员） */
  createEquipment(data) {
    return http.post('/equipment', data)
  },

  /** 更新设备（管理员） */
  updateEquipment(equipmentId, data) {
    return http.put(`/equipment/${equipmentId}`, data)
  },

  /** 删除设备（管理员，有未完成预约时会被拒绝） */
  deleteEquipment(equipmentId) {
    return http.delete(`/equipment/${equipmentId}`)
  },

  // ---------- 设备图片 ----------

  /**
   * 上传设备图片（管理员）
   * @param {number} equipmentId
   * @param {File} file
   */
  uploadEquipmentImage(equipmentId, file) {
    const formData = new FormData()
    formData.append('file', file)
    return http.post(`/equipment/${equipmentId}/image`, formData, {
      // 让浏览器自动设置 multipart 边界，不要手写 Content-Type
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  /** 删除设备图片（管理员） */
  deleteEquipmentImage(equipmentId) {
    return http.delete(`/equipment/${equipmentId}/image`)
  },

  // ---------- 收藏 ----------

  /** 检查是否已收藏 */
  checkCollection(equipmentId) {
    return http.get('/collections/check', { params: { equipment_id: equipmentId } })
  },

  /** 添加收藏 */
  addCollection(equipmentId) {
    return http.post(`/collections/${equipmentId}`)
  },

  /** 取消收藏 */
  removeCollection(equipmentId) {
    return http.delete(`/collections/${equipmentId}`)
  },

  /** 我的收藏列表 */
  listCollections(params) {
    return http.get('/collections', { params })
  },

  /** 清空收藏 */
  clearCollections() {
    return http.delete('/collections')
  },
}

export default equipmentApi
