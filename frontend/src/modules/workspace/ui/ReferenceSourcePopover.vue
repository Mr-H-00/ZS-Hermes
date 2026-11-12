<template>
  <Teleport to="body">
    <div
      :id="previewId"
      ref="card"
      class="reference-source-popover"
      role="dialog"
      aria-label="来源预览"
      :style="position"
      @pointerenter="emit('keep-open')"
      @pointerleave="emit('leave')"
      @focusin="emit('keep-open')"
      @focusout="emit('leave')"
    >
      <div v-if="citations.length > 1" class="reference-source-pagination">
        <div class="reference-source-navigation">
          <button type="button" aria-label="上一个来源" :aria-disabled="index === 0" @click="move(-1)">
            <ArrowLeft :size="17" />
          </button>
          <button type="button" aria-label="下一个来源" :aria-disabled="index === citations.length - 1" @click="move(1)">
            <ArrowRight :size="17" />
          </button>
        </div>
        <span aria-live="polite">{{ index + 1 }}/{{ citations.length }}</span>
      </div>
      <div class="reference-source-origin">
        <Globe v-if="citation.source.kind === 'web'" :size="15" />
        <FileText v-else :size="15" />
        <span>{{ referenceSourceLabel(citation.source) }}</span>
      </div>
      <button class="reference-source-title" type="button" @click="emit('open', citation)">
        {{ citation.source.title }}
      </button>
      <p class="reference-source-quote">{{ citation.quote }}</p>
      <div class="reference-source-footer">
        <span>{{ location }}</span>
        <button type="button" class="reference-source-open" @click="emit('open', citation)">
          {{ openLabel }}<ArrowUpRight :size="13" />
        </button>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ArrowLeft, ArrowRight, ArrowUpRight, FileText, Globe } from '@lucide/vue'
import { referenceSourceLabel } from '@/shared/lib/markdownReferences'

const props = defineProps({
  anchor: { type: Object, required: true },
  citations: { type: Array, required: true },
  previewId: { type: String, required: true }
})
const emit = defineEmits(['close', 'open', 'keep-open', 'leave'])
const card = ref(null)
const index = ref(0)
const position = ref({ visibility: 'hidden' })
const citation = computed(() => props.citations[index.value])
const location = computed(() => {
  if (citation.value.source.kind === 'web') return '网页来源'
  const { source_start_line: start, source_end_line: end } = citation.value
  return start ? `第 ${start}–${end} 行` : '检索片段'
})
const openLabel = computed(() => {
  if (citation.value.source.kind === 'web') return '打开网页'
  return citation.value.source.supports_document_read && citation.value.source.file_id ? '查看原文' : '查看片段'
})

/** 将预览卡片放在胶囊附近，并限制在当前视口内。 */
async function placeCard() {
  await nextTick()
  if (!card.value || !props.anchor.isConnected) return
  const rect = props.anchor.getBoundingClientRect()
  const { width, height } = card.value.getBoundingClientRect()
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))
  const below = rect.bottom + 8
  const top = below + height <= window.innerHeight - 12 ? below : Math.max(12, rect.top - height - 8)
  position.value = { left: `${left}px`, top: `${top}px` }
}

/** 通知 Markdown 关闭当前来源。 */
function close() {
  emit('close')
}

/** 点击胶囊与卡片之外时关闭预览。 */
function handleOutside(event) {
  if (!card.value?.contains(event.target) && !props.anchor.contains(event.target)) close()
}

/** 原文滚动后关闭预览，保留卡片内部滚动。 */
function handleScroll(event) {
  if (!card.value?.contains(event.target)) close()
}

/** Escape 关闭预览并把键盘焦点交还胶囊。 */
function handleEscape(event) {
  if (event.key !== 'Escape') return
  event.preventDefault()
  props.anchor.focus()
  close()
}

/** 从胶囊进入卡片的首个可用操作。 */
async function focus() {
  await placeCard()
  await nextTick()
  card.value?.querySelector('button:not([aria-disabled="true"])')?.focus({ preventScroll: true })
}

/** 保留分页按钮的焦点，使末页仍能继续阅读和打开来源。 */
function move(delta) {
  index.value = Math.max(0, Math.min(index.value + delta, props.citations.length - 1))
}

/** 判断用户是否仍在卡片内阅读或操作。 */
function isActive() {
  return card.value?.matches(':hover') || card.value?.contains(document.activeElement)
}

watch(() => props.anchor, () => { index.value = 0; void placeCard() })
watch(index, placeCard)
onMounted(() => {
  void placeCard()
  document.addEventListener('pointerdown', handleOutside)
  document.addEventListener('keydown', handleEscape)
  window.addEventListener('resize', close)
  window.addEventListener('scroll', handleScroll, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleOutside)
  document.removeEventListener('keydown', handleEscape)
  window.removeEventListener('resize', close)
  window.removeEventListener('scroll', handleScroll, true)
})
defineExpose({ focus, isActive })
</script>

<style scoped lang="less">
.reference-source-popover {
  position: fixed;
  z-index: 1100;
  width: min(330px, calc(100vw - 24px));
  max-height: calc(100dvh - 24px);
  overflow: auto;
  padding: 16px;
  border: 1px solid var(--gray-200);
  border-radius: 14px;
  background: var(--gray-0);
  box-shadow: var(--shadow-popover);
  color: var(--gray-1000);
  font-size: 13px;
  line-height: 1.5;
  animation: reference-source-enter 160ms ease-out;
  button { font-family: inherit; color: inherit; cursor: pointer; }
  button:focus-visible { outline: 2px solid var(--main-500); outline-offset: 3px; }
}
@keyframes reference-source-enter {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}
@media (prefers-reduced-motion: reduce) {
  .reference-source-popover { animation: none; }
}
:global(.dark .reference-source-popover) { background: var(--gray-100); }
.reference-source-pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: -6px 0 10px;
  color: var(--gray-600);
  font-size: 12px;
}
.reference-source-navigation {
  display: flex;
  gap: 2px;
  button {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border: 0;
    border-radius: 7px;
    background: transparent;
    &:hover:not([aria-disabled="true"]) { background: var(--gray-100); }
    &[aria-disabled="true"] { opacity: 0.35; cursor: default; }
  }
}
.reference-source-origin {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--gray-700);
  span { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
}
.reference-source-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  padding: 0;
  margin-top: 8px;
  border: 0;
  background: transparent;
  text-align: left;
  font-weight: 600;
  font-size: 14px;
  overflow-wrap: anywhere;
  &:hover { text-decoration: underline; text-underline-offset: 3px; }
}
.reference-source-quote {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin: 10px 0 14px;
  color: var(--gray-600);
  overflow-wrap: anywhere;
}
.reference-source-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--gray-600);
  font-size: 12px;
}
.reference-source-open {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 0;
  border: 0;
  background: transparent;
  white-space: nowrap;
  &:hover { color: var(--gray-1000); }
}
</style>
