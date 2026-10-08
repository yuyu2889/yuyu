<script setup>
/**
 * 通用页面头部组件。
 *
 * 为什么抽这个组件？
 * 因为原项目每个页面都重复写一遍"标题 + 描述 + 右侧操作按钮"的结构，
 * 导致不同页面的间距、字号都有细微差异。
 * 抽成组件后视觉自动统一。
 */
defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
})
</script>

<template>
  <div class="page-header">
    <div class="header-main">
      <h2 class="title">{{ title }}</h2>
      <p v-if="description" class="description">{{ description }}</p>
    </div>
    <div class="header-actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-6);
  flex-wrap: wrap;
}

.title {
  font-size: var(--font-xl);
  font-weight: 700;
  color: var(--color-text-primary);
  line-height: 1.3;
}

.description {
  margin: var(--space-1) 0 0;
  font-size: var(--font-sm);
  color: var(--color-text-secondary);
  line-height: 1.5;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}
</style>
