<script setup>
import { computed, ref } from 'vue'

import PageHeader from '@/components/shared/PageHeader.vue'
import AgentManagePanel from '@/components/model-management/AgentManagePanel.vue'

const agentPanelRef = ref(null)
const activeLoading = computed(() => agentPanelRef.value?.loading || false)
const activeStats = computed(() => agentPanelRef.value?.stats || {})
</script>

<template>
  <div class="agent-manage-view">
    <PageHeader title="智能体中心" :loading="activeLoading" :show-border="true">
      <template #info>
        <div class="summary-strip">
          <span>{{ activeStats.total || 0 }} 个智能体</span>
          <span>{{ activeStats.global || 0 }} 个全局</span>
          <span v-if="activeStats.builtin">{{ activeStats.builtin }} 个内置</span>
          <span>{{ activeStats.manageable || 0 }} 个可管理</span>
        </div>
      </template>
    </PageHeader>

    <div class="agent-manage-content">
      <div class="tab-panel">
        <AgentManagePanel ref="agentPanelRef" />
      </div>
    </div>
  </div>
</template>

<style lang="less" scoped>
.agent-manage-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.agent-manage-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;

  .tab-panel {
    height: 100%;
    min-height: 0;
    overflow-y: auto;
  }
}

.summary-strip {
  display: flex;
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
}
</style>
