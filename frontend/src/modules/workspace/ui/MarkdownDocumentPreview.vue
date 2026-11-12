<template>
  <div ref="container" class="document-text-preview">
    <div v-if="source" class="document-source" aria-label="Markdown 源码">
      <div
        v-for="(line, index) in lines"
        :key="index"
        :data-line="index + 1"
        class="source-row"
        :class="{ highlighted: isHighlighted(index + 1) }"
      >
        <span class="line-number" aria-hidden="true">{{ index + 1 }}</span>
        <code>{{ line || ' ' }}</code>
      </div>
    </div>
    <MarkdownPreview
      v-else
      :content="content"
      :resource-base-url="resourceBaseUrl"
      :start-line="startLine"
      :end-line="endLine"
      :source-lines="startLine > 0"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import MarkdownPreview from '@/modules/workspace/ui/MarkdownPreview.vue'

const props = defineProps({
  content: { type: String, default: '' },
  source: { type: Boolean, default: false },
  startLine: { type: Number, default: null },
  endLine: { type: Number, default: null },
  resourceBaseUrl: { type: String, default: '' }
})

const container = ref(null)
const lines = computed(() => props.content.split(/\r\n|\r|\n/))
const isHighlighted = (line) =>
  props.startLine > 0 && line >= props.startLine && line <= (props.endLine || props.startLine)

watch(
  [() => props.content, () => props.source, () => props.startLine, () => props.endLine],
  async () => {
    await nextTick()
    if (!props.source || !props.startLine) return
    container.value
      ?.querySelector(`[data-line="${props.startLine}"]`)
      ?.scrollIntoView({ block: 'center', behavior: 'auto' })
  },
  { immediate: true }
)
</script>

<style scoped lang="less">
.document-text-preview {
  min-width: 0;
  padding: 16px;
}
.document-source {
  font: 13px/1.7 var(--font-family-mono, monospace);
}
.source-row {
  display: flex;
  gap: 16px;
  padding: 0 8px;
  min-height: 22px;
  code {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    min-width: 0;
    flex: 1;
    color: var(--color-text);
  }
}
.line-number {
  width: 4ch;
  flex-shrink: 0;
  text-align: right;
  color: var(--color-text-secondary);
  user-select: none;
}
.highlighted {
  background: var(--color-warning-50);
  box-shadow: inset 3px 0 var(--color-warning-500);
}
</style>
