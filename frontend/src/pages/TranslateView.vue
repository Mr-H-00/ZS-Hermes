<script setup>
import TranslationPanel from '@/modules/translation/ui/TranslationPanel.vue'
</script>

<template>
  <div class="translate-view">
    <TranslationPanel />
  </div>
</template>

<style scoped lang="less">
.translate-view {
  display: flex;
  min-width: 0;
  min-height: 0;
  padding: 2px 3px 3px 4px;
  background: var(--gray-50);
}
</style>
