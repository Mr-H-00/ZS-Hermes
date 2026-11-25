<script setup>
import { nextTick, ref, watch } from 'vue'
import { X } from '@lucide/vue'

const props = defineProps({
  open: { type: Boolean, required: true },
  title: { type: String, required: true }
})
const emit = defineEmits(['close'])
const panel = ref(null)
let trigger

watch(() => props.open, async (open) => {
  if (open) {
    trigger = document.activeElement
    await nextTick()
    panel.value?.focus()
  } else {
    trigger?.focus()
    trigger = null
  }
})

function trapFocus(event) {
  const items = [...panel.value.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex="0"]'
  )].filter((item) => item.getClientRects().length)
  const first = items[0]
  const last = items.at(-1)
  if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.value)) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}
</script>

<template>
  <Transition name="translation-side">
    <div v-if="open" class="translation-side-layer" @click.self="emit('close')">
      <aside
        ref="panel"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
        tabindex="-1"
        class="translation-side-panel"
        @keydown.esc.stop.prevent="emit('close')"
        @keydown.tab="trapFocus"
      >
        <header class="translation-side-header">
          <h2>{{ title }}</h2>
          <div class="translation-side-actions">
            <slot name="actions" />
            <button type="button" class="translation-icon" aria-label="关闭" title="关闭" @click="emit('close')">
              <X :size="16" aria-hidden="true" />
            </button>
          </div>
        </header>
        <div class="translation-side-body"><slot /></div>
      </aside>
    </div>
  </Transition>
</template>

<style scoped lang="less">
.translation-side-layer {
  position: absolute;
  inset: 0;
  z-index: 6;
  background: rgb(0 0 0 / 50%);
}

.translation-side-panel {
  position: absolute;
  inset: 6px 6px 6px auto;
  display: flex;
  width: min(400px, calc(100% - 12px));
  flex-direction: column;
  overflow: hidden;
  border-radius: 22px;
  background: var(--gray-0);
  color: var(--gray-1000);
  box-shadow: 0 8px 30px rgb(0 0 0 / 16%);
  outline: 0;
}

.translation-side-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 24px 24px 12px;

  h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 650;
    line-height: 24px;
  }
}

.translation-side-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.translation-side-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px 24px 22px;
  overscroll-behavior: contain;
}

.translation-side-enter-active,
.translation-side-leave-active {
  transition: opacity 150ms ease;

  .translation-side-panel {
    transition: transform 180ms cubic-bezier(0.16, 1, 0.3, 1);
  }
}

.translation-side-enter-from,
.translation-side-leave-to {
  opacity: 0;

  .translation-side-panel {
    transform: translateX(100%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .translation-side-enter-active,
  .translation-side-leave-active,
  .translation-side-enter-active .translation-side-panel,
  .translation-side-leave-active .translation-side-panel {
    transition: none;
  }
}
</style>
