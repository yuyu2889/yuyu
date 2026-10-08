<script setup>
/**
 * 404 页面。
 */
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="not-found">
    <div class="content">
      <div class="code">404</div>
      <h1 class="title">页面不存在</h1>
      <p class="desc">您访问的页面可能已被移除、重命名，或者暂时不可用</p>
      <div class="actions">
        <el-button type="primary" size="large" @click="router.push('/dashboard')">
          <el-icon><HomeFilled /></el-icon>返回工作台
        </el-button>
        <el-button size="large" @click="router.back()">
          <el-icon><Back /></el-icon>返回上一页
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.not-found {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg-page);
  padding: var(--space-6);
}

.content {
  text-align: center;
  max-width: 460px;
}

.code {
  font-size: 96px;
  font-weight: 800;
  line-height: 1;
  /* 用渐变文字让 404 不那么单调 */
  background: linear-gradient(135deg, var(--color-primary), var(--color-primary-dark));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
  margin-bottom: var(--space-4);
}

.title {
  font-size: var(--font-xl);
  margin-bottom: var(--space-3);
}

.desc {
  color: var(--color-text-secondary);
  font-size: var(--font-base);
  line-height: 1.7;
  margin-bottom: var(--space-8);
}

.actions {
  display: flex;
  gap: var(--space-3);
  justify-content: center;
  flex-wrap: wrap;
}
</style>
