<script setup>
import { Gauge } from '@lucide/vue'
import { reasoningLevels } from '../model/languages'

const value = defineModel({ type: String, default: 'none' })
</script>

<template>
  <a-select
    v-model:value="value"
    :options="reasoningLevels"
    :bordered="false"
    :dropdown-match-select-width="false"
    aria-label="思考强度"
    class="reasoning-picker"
  >
    <template #optionLabel="{ label }"><Gauge :size="15" aria-hidden="true" /> {{ label }}</template>
  </a-select>
</template>

<style scoped lang="less">
.reasoning-picker {
  width: 80px;
  color: var(--gray-600);
}

.reasoning-picker :deep(.ant-select-selection-item) {
  display: flex;
  align-items: center;
  gap: 5px;
  color: inherit;
}
</style>
