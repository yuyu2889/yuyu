<script setup>
/**
 * 设备图片组件 —— V2 图片处理的"唯一出口"。
 *
 * ============================================================================
 *  这是相对原项目最重要的一处简化。
 * ============================================================================
 *
 *  原项目的问题：数据库存相对路径 `equipment/1.jpg`，然后前端在三处用了
 *  三种不同的解析方式：
 *    1. helpers.js 里拼 `${API_BASE}/static/${image}`
 *    2. Admin.vue 里用正则从 image 抠出扩展名，重组成 `${API_BASE}/static/equipment/${id}.${ext}`
 *    3. EquipmentManagement.vue 直接把 image 当 src（结果 404）
 *
 *  V2 的做法：
 *  - 后端返回的 image 已经是**可直接使用的路径**（/static/uploads/...）
 *  - 前端只有一个组件负责展示图片，逻辑只有三种情况：
 *      无图 → 渐变占位卡片
 *      有图但加载失败 → 同样显示占位卡片（onerror 兜底）
 *      有图 → 正常显示
 *  - **不做任何路径拼接或文件名推断**
 * ============================================================================
 */
import { computed, ref, watch } from 'vue'

const props = defineProps({
  /** 图片路径（后端已处理成可直接使用的形式，可能为 null） */
  src: { type: String, default: null },
  /** 设备名称，用于生成占位卡的文字和配色 */
  name: { type: String, default: '' },
  /** 高度（支持任意 CSS 值） */
  height: { type: String, default: '160px' },
  /** 填充方式 */
  fit: { type: String, default: 'cover' },
  /** 是否圆角 */
  radius: { type: String, default: 'var(--radius-md)' },
})

// 图片加载失败时切换到占位卡
const loadFailed = ref(false)

// src 变化时重置失败状态（否则切换设备后还是占位图）
watch(
  () => props.src,
  () => {
    loadFailed.value = false
  },
)

const showPlaceholder = computed(() => !props.src || loadFailed.value)

/**
 * 根据名称生成确定的配色。
 *
 * 为什么用"名称哈希"而不是随机色？
 * 因为随机会导致同一个设备每次刷新颜色都变，视觉上很混乱。
 * 用名称哈希保证同一设备永远同一个颜色，而且不同设备颜色有区分度。
 */
const PLACEHOLDER_PALETTE = [
  { bg: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', text: '#ffffff' },
  { bg: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)', text: '#ffffff' },
  { bg: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)', text: '#ffffff' },
  { bg: 'linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)', text: '#ffffff' },
  { bg: 'linear-gradient(135deg, #fa709a 0%, #fee140 100%)', text: '#ffffff' },
  { bg: 'linear-gradient(135deg, #30cfd0 0%, #330867 100%)', text: '#ffffff' },
  { bg: 'linear-gradient(135deg, #a8edea 0%, #fed6e3 100%)', text: '#2d3748' },
  { bg: 'linear-gradient(135deg, #ff9a9e 0%, #fecfef 100%)', text: '#2d3748' },
]

const placeholderStyle = computed(() => {
  const name = props.name || '未命名'
  // 简单的字符哈希（累加字符编码），保证同名同色
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) % 9973
  }
  const theme = PLACEHOLDER_PALETTE[hash % PLACEHOLDER_PALETTE.length]
  return {
    background: theme.bg,
    color: theme.text,
  }
})

/** 占位卡上显示的文字：取名称前 2 个字，避免太长挤满 */
const placeholderText = computed(() => {
  const name = props.name || '设备'
  return name.length > 3 ? name.slice(0, 3) : name
})

/** 第一个字符（用于小尺寸占位时只显示一个字） */
const placeholderChar = computed(() => (props.name || '设').charAt(0))
</script>

<template>
  <div class="equipment-image" :style="{ height, borderRadius: radius }">
    <!-- 正常图片 -->
    <el-image
      v-if="!showPlaceholder"
      :src="src"
      :fit="fit"
      class="image"
      loading="lazy"
      @error="loadFailed = true"
    >
      <!-- 加载中的骨架 -->
      <template #placeholder>
        <div class="image-skeleton">
          <el-icon class="is-loading" :size="20"><Loading /></el-icon>
        </div>
      </template>
    </el-image>

    <!-- 占位卡（无图 或 加载失败） -->
    <div v-else class="placeholder" :style="placeholderStyle">
      <span class="placeholder-text">{{ placeholderText }}</span>
      <span class="placeholder-hint">
        {{ loadFailed ? '图片加载失败' : '暂无图片' }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.equipment-image {
  position: relative;
  width: 100%;
  overflow: hidden;
  background: var(--color-bg-hover);
}

.image {
  width: 100%;
  height: 100%;
  display: block;
  transition: transform var(--transition-base);
}

.equipment-image:hover .image {
  transform: scale(1.03);
}

.image-skeleton {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-placeholder);
  background: var(--color-bg-hover);
}

.placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  user-select: none;
}

.placeholder-text {
  font-size: var(--font-lg);
  font-weight: 700;
  letter-spacing: 1px;
  opacity: 0.95;
}

.placeholder-hint {
  font-size: var(--font-xs);
  opacity: 0.7;
}
</style>
