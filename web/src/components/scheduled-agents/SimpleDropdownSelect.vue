<template>
  <div class="simple-dropdown-select">
    <a-dropdown
      v-model:open="open"
      :trigger="['click']"
      placement="bottomRight"
      overlay-class-name="simple-dropdown-overlay"
    >
      <button
        ref="triggerRef"
        type="button"
        class="simple-dropdown-trigger"
        :aria-label="ariaLabel || currentLabel"
        aria-haspopup="menu"
        :aria-expanded="open"
        @keydown="handleTriggerKeydown"
      >
        <span class="simple-dropdown-text">{{ currentLabel }}</span>
        <ChevronDown :size="15" class="simple-dropdown-chevron" />
      </button>

      <template #overlay>
        <div ref="panelRef" class="simple-dropdown-panel" role="menu" :aria-label="ariaLabel">
          <button
            v-for="(option, index) in options"
            :key="option.value"
            type="button"
            role="menuitemradio"
            tabindex="-1"
            :aria-checked="modelValue === option.value"
            class="simple-dropdown-item"
            :class="{ selected: modelValue === option.value }"
            @click="selectOption(option.value)"
            @keydown="handleOptionKeydown($event, index)"
          >
            <span class="simple-dropdown-item-label">{{ option.label }}</span>
            <Check
              v-if="modelValue === option.value"
              :size="14"
              class="simple-dropdown-item-check"
            />
          </button>
        </div>
      </template>
    </a-dropdown>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Check, ChevronDown } from '@lucide/vue'
import { useOutsidePointerdown } from '@/composables/useOutsidePointerdown'

const props = defineProps({
  modelValue: { type: [String, Number], default: '' },
  options: { type: Array, default: () => [] },
  ariaLabel: { type: String, default: '' }
})

const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const triggerRef = ref(null)
const panelRef = ref(null)
let focusGeneration = 0
let focusTimer = null

const selectedIndex = computed(() =>
  props.options.findIndex((option) => option.value === props.modelValue)
)
const currentOption = computed(() => props.options[selectedIndex.value])
const currentLabel = computed(() => currentOption.value?.label || String(props.modelValue ?? ''))

/** 将焦点移动到指定菜单项。 */
function focusOption(index) {
  const count = props.options.length
  if (!count) return
  panelRef.value?.querySelectorAll('.simple-dropdown-item')[(index + count) % count]?.focus()
}

/** 关闭菜单并把焦点还给触发按钮。 */
function closeMenu() {
  cancelPendingFocus()
  open.value = false
  void nextTick(() => triggerRef.value?.focus())
}

/** 等待 Teleport 菜单挂载后聚焦当前选项。 */
function focusInitialOption(generation, attempt = 0) {
  if (!open.value || generation !== focusGeneration) return
  const index = selectedIndex.value < 0 ? 0 : selectedIndex.value
  const option = panelRef.value?.querySelectorAll('.simple-dropdown-item')[index]
  if (option) {
    option.focus()
    return
  }
  if (attempt < 5) {
    requestAnimationFrame(() => focusInitialOption(generation, attempt + 1))
  }
}

/** 使尚未执行的聚焦任务失效。 */
function cancelPendingFocus() {
  focusGeneration += 1
  if (focusTimer !== null) window.clearTimeout(focusTimer)
  focusTimer = null
}

/** 使用键盘打开菜单并将焦点移入选项。 */
function openWithKeyboard() {
  cancelPendingFocus()
  open.value = true
  const generation = focusGeneration
  focusTimer = window.setTimeout(() => {
    focusTimer = null
    focusInitialOption(generation)
  }, 100)
}

/** 使用标准菜单按钮按键触发键盘打开路径。 */
function handleTriggerKeydown(event) {
  if (!['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) return
  event.preventDefault()
  event.stopPropagation()
  openWithKeyboard()
}

/** 选择当前值并恢复触发按钮焦点。 */
function selectOption(value) {
  emit('update:modelValue', value)
  closeMenu()
}

/** 处理菜单项的方向键与退出操作。 */
function handleOptionKeydown(event, index) {
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    closeMenu()
    return
  }

  const targetIndex = {
    ArrowDown: index + 1,
    ArrowUp: index - 1,
    Home: 0,
    End: props.options.length - 1
  }[event.key]
  if (targetIndex === undefined) return
  event.preventDefault()
  event.stopPropagation()
  focusOption(targetIndex)
}

useOutsidePointerdown(open, [triggerRef, panelRef])
watch(open, (isOpen) => {
  if (!isOpen) cancelPendingFocus()
})
onBeforeUnmount(cancelPendingFocus)
</script>

<style scoped lang="less">
.simple-dropdown-select {
  display: block;
  width: 100%;
}

.simple-dropdown-trigger {
  display: inline-flex;
  width: 100%;
  height: 30px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: 5px;
  background: transparent;
  color: var(--gray-900);
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  text-align: right;
  transition:
    background-color 0.15s ease,
    color 0.15s ease;

  &:hover {
    background: var(--gray-50);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 1px;
  }
}

.simple-dropdown-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.simple-dropdown-chevron {
  flex-shrink: 0;
  margin-left: 2px;
  color: var(--gray-500);
}
</style>

<style lang="less">
.simple-dropdown-overlay .simple-dropdown-panel {
  min-width: 120px;
  max-height: 260px;
  overflow-y: auto;
  padding: 4px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  box-shadow: 0 8px 24px var(--shadow-4);
}

.simple-dropdown-overlay .simple-dropdown-item {
  display: grid;
  width: 100%;
  min-height: 32px;
  padding: 0 8px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-800);
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  text-align: left;
  grid-template-columns: minmax(0, 1fr) 14px;
  align-items: center;
  gap: 7px;
}

.simple-dropdown-overlay .simple-dropdown-item:hover,
.simple-dropdown-overlay .simple-dropdown-item.selected {
  background: var(--gray-50);
}

.simple-dropdown-overlay .simple-dropdown-item:focus-visible {
  outline: 2px solid var(--main-color);
  outline-offset: -2px;
}

.simple-dropdown-overlay .simple-dropdown-item-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.simple-dropdown-overlay .simple-dropdown-item-check {
  color: var(--main-color);
}
</style>
