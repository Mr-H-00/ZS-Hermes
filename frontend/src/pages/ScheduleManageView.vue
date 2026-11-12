<script setup>
import { computed, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import PageHeader from '@/shared/ui/PageHeader.vue'
import ScheduledAgentsView from '@/modules/agents/ui/ScheduledAgents.vue'

const scheduleRef = ref(null)
const loading = computed(() => scheduleRef.value?.loading || scheduleRef.value?.saving || false)
onBeforeRouteLeave(() => scheduleRef.value?.beforeLeave?.() ?? true)
</script>

<template>
  <div class="schedule-manage-view">
    <PageHeader title="定时任务" :loading="loading" :show-border="true" />
    <div class="schedule-content">
      <ScheduledAgentsView ref="scheduleRef" />
    </div>
  </div>
</template>

<style scoped lang="less">
.schedule-manage-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--gray-0);
}

.schedule-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
</style>
