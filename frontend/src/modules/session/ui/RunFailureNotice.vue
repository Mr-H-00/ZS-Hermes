<script setup>
import { CircleAlert } from '@lucide/vue'

defineProps({
  errorMessage: { type: String, default: '' }
})
</script>

<template>
  <div class="run-failure-notice" role="alert">
    <CircleAlert :size="18" class="notice-icon" aria-hidden="true" />
    <div class="notice-content">
      <div class="notice-title">本次运行未能完成</div>
      <p class="notice-description">{{ errorMessage || '请检查模型配置后重试。' }}</p>
    </div>
  </div>
</template>

<style scoped lang="less">
.run-failure-notice {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 12px 0;
  padding: 14px 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);
}

.notice-icon {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--gray-500);
}

.notice-content {
  min-width: 0;
}

.notice-title {
  color: var(--gray-800);
  font-size: 13px;
  font-weight: 600;
  line-height: 22px;
}

.notice-description {
  margin: 4px 0 0;
  color: var(--gray-600);
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
