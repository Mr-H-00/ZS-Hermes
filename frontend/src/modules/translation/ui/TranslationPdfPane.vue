<script setup>
import { Download, Languages, LoaderCircle, X } from '@lucide/vue'
import PdfPreview from '@/shared/ui/PdfPreview.vue'

defineProps({
  name: { type: String, required: true },
  source: { type: String, required: true },
  result: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  available: { type: Boolean, default: false },
  checking: { type: Boolean, default: false },
  error: { type: String, default: '' },
  textActive: { type: Boolean, default: false }
})
const emit = defineEmits(['close'])
</script>

<template>
  <section class="pdf-pane" aria-label="原文 PDF">
    <header class="pdf-header">
      <strong :title="name">{{ name }}</strong>
      <button class="translation-icon" type="button" title="关闭 PDF 翻译" aria-label="关闭 PDF 翻译" @click="emit('close')">
        <X :size="15" aria-hidden="true" />
      </button>
    </header>
    <PdfPreview :url="source" class="pdf-preview" :horizontal-padding="20" />
  </section>
  <section v-if="!textActive" class="pdf-pane translated-pdf" aria-label="译文 PDF">
    <header class="pdf-header">
      <span>译文 PDF</span>
      <a
        v-if="result"
        class="translation-icon"
        :href="result"
        :download="name.replace(/\.pdf$/i, '') + '-译文.pdf'"
        title="导出译文 PDF"
        aria-label="导出译文 PDF"
      ><Download :size="15" aria-hidden="true" /></a>
    </header>
    <PdfPreview v-if="result" :url="result" class="pdf-preview" :horizontal-padding="20" />
    <div v-else class="pdf-empty" :role="error ? 'alert' : 'status'">
      <LoaderCircle v-if="busy || checking" :size="18" class="pdf-loading" aria-hidden="true" />
      <Languages v-else :size="21" aria-hidden="true" />
      <strong v-if="error">PDF 翻译失败</strong>
      <strong v-else-if="busy">正在翻译 PDF...</strong>
      <strong v-else-if="checking">正在检测 BabelDOC...</strong>
      <strong v-else-if="available">等待翻译</strong>
      <strong v-else>检测到 PDF</strong>
      <span v-if="error">{{ error }}</span>
      <span v-else-if="!busy && !checking">
        {{ available ? '选择目标语言并开始翻译。' : '服务端启用 BabelDOC 后可保留原始版式。当前可以翻译正文。' }}
      </span>
    </div>
  </section>
</template>

<style scoped lang="less">
.pdf-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.translated-pdf {
  border-left: 1px solid var(--gray-100);
}

.pdf-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
  min-height: 40px;
  padding: 5px 12px;
  border-bottom: 1px solid var(--gray-100);
  color: var(--gray-600);
  font-size: 13px;
}

.pdf-header strong {
  overflow: hidden;
  color: var(--gray-1000);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pdf-preview,
.pdf-empty {
  flex: 1;
  min-height: 0;
}

.pdf-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 9px;
  padding: 14px;
  color: var(--gray-600);
  text-align: center;
}

.pdf-empty strong {
  color: var(--gray-1000);
}

.pdf-loading {
  animation: pdf-loading 0.85s linear infinite;
}

@media (max-width: 1023px) {
  .translated-pdf {
    border-top: 1px solid var(--gray-100);
    border-left: 0;
  }
}

@keyframes pdf-loading {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .pdf-loading { animation-duration: 2s; }
}
</style>
