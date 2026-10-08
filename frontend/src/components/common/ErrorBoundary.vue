<script setup>
/**
 * 错误边界组件。
 *
 * ============================================================================
 *  为什么需要它？（这是一个很实际的工程问题）
 * ============================================================================
 *  Vue 默认的行为是：如果某个页面组件在渲染时抛异常，
 *  整个组件树会渲染失败 —— 用户看到的是一片**白屏**，
 *  而且**没有任何提示**，完全不知道发生了什么。
 *
 *  这和 React 里需要 ErrorBoundary 是同一个问题。
 *  Vue 3 虽然没有官方的 ErrorBoundary 组件，
 *  但可以用 onErrorCaptured 钩子实现同样的效果。
 *
 *  加了这个组件之后：
 *    - 页面组件崩溃时，用户会看到明确的错误信息和解决建议
 *    - 侧边栏、顶栏仍然正常（因为错误被限制在内容区域内）
 *    - 提供"重试"按钮，可以重新渲染
 *
 *  我在做这个项目时遇到"数据统计页白屏"的问题，
 *  如果有错误边界，一眼就能看到原因，不用靠猜。
 * ============================================================================
 */
import { onErrorCaptured, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const error = ref(null)
const errorInfo = ref('')

// 捕获子组件树里抛出的所有异常
onErrorCaptured((err, instance, info) => {
  error.value = err
  errorInfo.value = info
  // 在控制台也打一份，方便开发时看完整堆栈
  console.error('[ErrorBoundary] 捕获到组件异常:', info, err)
  // 返回 false 阻止错误继续向上冒泡（否则会传到 app.config.errorHandler）
  return false
})

/** 切换路由时清除错误状态（否则跳到别的页面还显示旧错误） */
watch(
  () => route.fullPath,
  () => {
    error.value = null
    errorInfo.value = ''
  },
)

/** 重试：清空错误，让子组件重新挂载 */
function retry() {
  error.value = null
  errorInfo.value = ''
}
</script>

<template>
  <!-- 正常情况下直接渲染子组件 -->
  <slot v-if="!error" />

  <!-- 出错时显示这个（替代白屏） -->
  <div v-else class="error-boundary">
    <div class="error-card">
      <div class="error-icon">
        <el-icon :size="42"><WarningFilled /></el-icon>
      </div>

      <h2 class="error-title">页面出现了问题</h2>
      <p class="error-desc">
        这个页面的组件在渲染时抛出了异常，已被错误边界捕获。
        其他页面仍然可以正常使用。
      </p>

      <div class="error-detail">
        <div class="detail-row">
          <span class="detail-label">错误类型</span>
          <code class="detail-value">{{ error?.name || 'Error' }}</code>
        </div>
        <div class="detail-row">
          <span class="detail-label">错误信息</span>
          <code class="detail-value">{{ error?.message || '(无)' }}</code>
        </div>
        <div v-if="errorInfo" class="detail-row">
          <span class="detail-label">发生位置</span>
          <code class="detail-value">{{ errorInfo }}</code>
        </div>
      </div>

      <el-collapse v-if="error?.stack" class="stack-collapse">
        <el-collapse-item title="查看完整错误堆栈（排查问题时很有用）">
          <pre class="stack-text">{{ error.stack }}</pre>
        </el-collapse-item>
      </el-collapse>

      <div class="error-actions">
        <el-button type="primary" @click="retry">
          <el-icon><Refresh /></el-icon>重试
        </el-button>
        <el-button @click="$router.push('/dashboard')">
          <el-icon><HomeFilled /></el-icon>返回工作台
        </el-button>
      </div>

      <p class="error-tip">
        提示：按 <kbd>F12</kbd> 打开开发者工具的 Console 标签，可以看到完整的错误堆栈。
      </p>
    </div>
  </div>
</template>

<style scoped>
.error-boundary {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  padding: var(--space-8);
}

.error-card {
  max-width: 680px;
  width: 100%;
  text-align: center;
}

.error-icon {
  width: 76px;
  height: 76px;
  margin: 0 auto var(--space-5);
  border-radius: 50%;
  background: var(--color-danger-pale);
  color: var(--color-danger);
  display: flex;
  align-items: center;
  justify-content: center;
}

.error-title {
  font-size: var(--font-xl);
  font-weight: 700;
  color: var(--color-text-primary);
  margin-bottom: var(--space-3);
}

.error-desc {
  font-size: var(--font-base);
  color: var(--color-text-secondary);
  line-height: 1.7;
  margin-bottom: var(--space-6);
}

.error-detail {
  text-align: left;
  background: var(--color-bg-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  margin-bottom: var(--space-4);
}

.detail-row {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  font-size: var(--font-sm);
  border-bottom: 1px dashed var(--color-border-light);
}

.detail-row:last-child {
  border-bottom: none;
}

.detail-label {
  width: 80px;
  flex-shrink: 0;
  color: var(--color-text-placeholder);
}

.detail-value {
  flex: 1;
  min-width: 0;
  color: var(--color-danger);
  font-family: Consolas, Monaco, monospace;
  font-size: var(--font-xs);
  word-break: break-all;
  text-align: left;
}

.stack-collapse {
  margin-bottom: var(--space-5);
  text-align: left;
}

.stack-text {
  margin: 0;
  padding: var(--space-3);
  background: var(--color-bg-hover);
  border-radius: var(--radius-sm);
  font-size: var(--font-xs);
  line-height: 1.6;
  color: var(--color-text-secondary);
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.error-actions {
  display: flex;
  gap: var(--space-3);
  justify-content: center;
  margin-bottom: var(--space-5);
}

.error-tip {
  font-size: var(--font-xs);
  color: var(--color-text-placeholder);
}

.error-tip kbd {
  padding: 1px 5px;
  border: 1px solid var(--color-border);
  border-radius: 3px;
  background: var(--color-bg-hover);
  font-family: Consolas, monospace;
}
</style>
