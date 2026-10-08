<script setup>
/**
 * 统计数字卡片。
 *
 * 设计要点：
 * 1. 用 CSS 变量取色，自动支持深色模式
 * 2. 支持"趋势"文字（比如"较昨日 +3"）和图标
 * 3. 可点击（有 onClick 时显示手型光标和悬停效果）
 */
defineProps({
  label: { type: String, required: true },
  value: { type: [Number, String], default: 0 },
  icon: { type: String, default: 'DataLine' },
  color: { type: String, default: 'var(--color-primary)' },
  trend: { type: String, default: '' },
  clickable: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['click'])
</script>

<template>
  <div
    class="stat-card card"
    :class="{ clickable }"
    @click="clickable && emit('click')"
  >
    <div class="stat-icon" :style="{ background: color }">
      <el-icon :size="22" color="#fff"><component :is="icon" /></el-icon>
    </div>

    <div class="stat-body">
      <div class="stat-label">{{ label }}</div>
      <!-- 用 v-if 切换骨架屏，加载时不会出现"0 闪一下再变真实值" -->
      <el-skeleton v-if="loading" :rows="1" animated class="stat-skeleton" />
      <div v-else class="stat-value">{{ value }}</div>
      <div v-if="trend && !loading" class="stat-trend">{{ trend }}</div>
    </div>
  </div>
</template>

<style scoped>
.stat-card {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-5);
  transition: transform var(--transition-fast), box-shadow var(--transition-base);
}

.stat-card.clickable {
  cursor: pointer;
}

.stat-card.clickable:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.stat-icon {
  width: 48px;
  height: 48px;
  flex-shrink: 0;
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
}

.stat-body {
  min-width: 0;
  flex: 1;
}

.stat-label {
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stat-value {
  font-size: var(--font-2xl);
  font-weight: 700;
  color: var(--color-text-primary);
  line-height: 1.2;
  font-variant-numeric: tabular-nums; /* 数字等宽，避免变化的数字导致布局跳动 */
}

.stat-trend {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
  margin-top: 2px;
}

.stat-skeleton {
  padding: var(--space-2) 0;
}
</style>
