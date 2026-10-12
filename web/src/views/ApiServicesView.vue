<script setup>
import { computed, ref } from 'vue'
import PageHeader from '@/components/shared/PageHeader.vue'
import ModelProviderManagePanel from '@/components/model-management/ModelProviderManagePanel.vue'

const providerRef = ref(null)
const loading = computed(() => providerRef.value?.loading || false)
const stats = computed(() => providerRef.value?.stats || {})
</script>

<template>
  <div class="api-services-view">
    <PageHeader title="API服务" :loading="loading" :show-border="true">
      <template #info>
        <div class="summary-strip">
          <span>{{ stats.total || 0 }} 个供应商</span>
          <span>{{ stats.enabled || 0 }} 个启用</span>
          <span v-if="stats.warning > 0" class="warning-count">
            {{ stats.warning }} 个凭证缺失
          </span>
          <span>{{ stats.models || 0 }} 个模型</span>
        </div>
      </template>
    </PageHeader>
    <div class="api-services-content">
      <ModelProviderManagePanel ref="providerRef" />
    </div>
  </div>
</template>

<style scoped lang="less">
.api-services-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.api-services-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.summary-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;

  span {
    padding: 6px 10px;
    border: 1px solid var(--gray-100);
    border-radius: 7px;
    background: var(--gray-10);
    color: var(--gray-700);
    font-size: 12px;
    line-height: 18px;
  }

  .warning-count {
    background: var(--color-warning-50);
    border-color: var(--color-warning-100);
    color: var(--color-warning-700);
  }
}
</style>
