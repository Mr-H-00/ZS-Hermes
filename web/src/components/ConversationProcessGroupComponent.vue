<template>
  <section class="conversation-process-group">
    <button
      type="button"
      class="process-summary"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <span>{{ summary }}</span>
    </button>
    <div v-if="expanded" class="process-content">
      <template v-for="item in items" :key="item.key">
        <AgentMessageComponent
          v-if="item.type === 'message'"
          :message="item.message"
          :hide-tool-calls="true"
          :mention="mention"
        />
        <ToolCallsGroupComponent v-else :tool-calls="item.toolCalls" />
      </template>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import AgentMessageComponent from '@/components/AgentMessageComponent.vue'
import ToolCallsGroupComponent from '@/components/ToolCallsGroupComponent.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  messageCount: { type: Number, default: 0 },
  toolCallCount: { type: Number, default: 0 },
  durationMs: { type: Number, default: 0 },
  mention: { type: Object, default: () => null }
})

const expanded = ref(false)
const summary = computed(() => {
  if (!props.durationMs) return '处理过程'
  const totalSeconds = Math.max(0, Math.round(props.durationMs / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `耗时${minutes}分钟${seconds}秒`
})
</script>

<style scoped lang="less">
.process-summary {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 30px;
  padding: 4px 0;
  border: 0;
  border-bottom: 1px solid var(--gray-150);
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
  font: inherit;
  text-align: left;

  &:hover,
  &:focus-visible {
    color: var(--gray-700);
  }

  &:focus-visible {
    outline: 2px solid var(--main-300);
    outline-offset: 2px;
  }
}

.process-content {
  padding-top: 10px;
}
</style>
