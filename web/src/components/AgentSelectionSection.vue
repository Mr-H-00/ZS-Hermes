<template>
  <section class="agent-selection" :class="{ compact }" :aria-label="ariaLabel">
    <a-dropdown
      v-model:open="dropdownOpen"
      :trigger="['click']"
      :placement="placement"
      overlay-class-name="agent-selection-overlay"
    >
      <button
        ref="triggerRef"
        type="button"
        class="input-action-btn agent-selection-trigger"
        :class="{ active: dropdownOpen }"
        :disabled="disabled"
        aria-haspopup="menu"
        :aria-expanded="dropdownOpen"
        @keydown="handleTriggerKeydown"
      >
        <FallbackAvatar
          v-if="currentAgent"
          class="agent-selection-trigger-icon"
          :src="currentAgent.icon"
          :default-src="currentAgent.defaultIcon"
          :name="currentAgent.label"
          :seed="currentAgent.value || currentAgent.label"
          kind="agent"
          :size="20"
          shape="rounded"
          alt=""
        />
        <span class="agent-selection-trigger-label" :title="currentLabel">{{ currentLabel }}</span>
        <ChevronDown :size="15" class="agent-selection-chevron" />
      </button>

      <template #overlay>
        <div
          ref="panelRef"
          class="agent-selection-panel"
          role="menu"
          :aria-label="ariaLabel"
          @keydown="handleMenuKeydown"
        >
          <button
            v-for="agent in agentOptions"
            :key="agent.value"
            type="button"
            role="menuitemradio"
            tabindex="-1"
            class="agent-selection-option"
            :class="{
              selected: agent.value === modelValue,
              disabled: locked && agent.value !== modelValue
            }"
            :aria-checked="agent.value === modelValue"
            :aria-disabled="locked && agent.value !== modelValue"
            @click="selectAgent(agent.value)"
          >
            <FallbackAvatar
              class="agent-selection-option-icon"
              :src="agent.icon"
              :default-src="agent.defaultIcon"
              :name="agent.label"
              :seed="agent.value || agent.label"
              kind="agent"
              :size="24"
              shape="rounded"
              :alt="`${agent.label}图标`"
            />
            <span class="agent-selection-option-label">{{ agent.label }}</span>
            <span v-if="agent.isBuiltin" class="agent-selection-option-badge">内置</span>
            <Check
              v-if="agent.value === modelValue"
              :size="14"
              class="agent-selection-option-check"
            />
          </button>

          <div v-if="!agentOptions.length" class="agent-selection-empty">暂无可用智能体</div>

          <div v-if="hint" class="agent-selection-hint">{{ hint }}</div>

          <template v-if="showActions">
            <div class="agent-selection-divider"></div>
            <div class="agent-selection-actions">
              <button
                type="button"
                role="menuitem"
                tabindex="-1"
                class="agent-selection-option"
                @click="emitAction('edit')"
              >
                <Settings2 :size="15" class="agent-selection-option-action-icon" />
                <span class="agent-selection-option-label">编辑智能体</span>
              </button>
              <button
                type="button"
                role="menuitem"
                tabindex="-1"
                class="agent-selection-option"
                @click="emitAction('create')"
              >
                <Plus :size="15" class="agent-selection-option-action-icon" />
                <span class="agent-selection-option-label">新建智能体</span>
              </button>
            </div>
          </template>
        </div>
      </template>
    </a-dropdown>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Check, ChevronDown, Plus, Settings2 } from '@lucide/vue'

import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import { useOutsidePointerdown } from '@/composables/useOutsidePointerdown'
import { isBuiltinAgent } from '@/stores/agent'
import { generatePixelAvatar } from '@/utils/pixelAvatar'

const props = defineProps({
  modelValue: { type: String, default: '' },
  agents: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  locked: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
  showActions: { type: Boolean, default: false },
  hint: { type: String, default: '' },
  placement: { type: String, default: 'bottomRight' },
  ariaLabel: { type: String, default: '选择智能体' }
})
const emit = defineEmits(['update:modelValue', 'blocked', 'edit', 'create'])

const dropdownOpen = ref(false)
const triggerRef = ref(null)
const panelRef = ref(null)
let focusGeneration = 0
let focusTimer = null
const agentOptions = computed(() =>
  props.agents.map((agent) => {
    const value = agent.slug || agent.id
    return {
      value,
      label: agent.name || value,
      icon: agent.icon || '',
      defaultIcon: value ? generatePixelAvatar(value) : '',
      isBuiltin: isBuiltinAgent(agent)
    }
  })
)
const currentAgent = computed(() =>
  agentOptions.value.find((agent) => agent.value === props.modelValue)
)
const currentLabel = computed(() => {
  if (props.loading) return '加载中...'
  return currentAgent.value?.label || '选择智能体'
})

/** 返回当前菜单内可聚焦的操作项。 */
function menuItems() {
  return [...(panelRef.value?.querySelectorAll('[role^="menuitem"]') || [])]
}

/** 关闭菜单并把焦点还给触发按钮。 */
function closeMenu() {
  cancelPendingFocus()
  dropdownOpen.value = false
  void nextTick(() => triggerRef.value?.focus())
}

/** 等待 Teleport 菜单挂载后聚焦当前智能体。 */
function focusInitialItem(generation, attempt = 0) {
  if (!dropdownOpen.value || generation !== focusGeneration) return
  const selected = panelRef.value?.querySelector('[aria-checked="true"]')
  const target = selected || menuItems()[0]
  if (target) {
    target.focus()
    return
  }
  if (attempt < 5) {
    requestAnimationFrame(() => focusInitialItem(generation, attempt + 1))
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
  dropdownOpen.value = true
  const generation = focusGeneration
  focusTimer = window.setTimeout(() => {
    focusTimer = null
    focusInitialItem(generation)
  }, 100)
}

/** 使用标准菜单按钮按键触发键盘打开路径。 */
function handleTriggerKeydown(event) {
  if (!['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) return
  event.preventDefault()
  event.stopPropagation()
  openWithKeyboard()
}

/** 处理菜单的方向键、首尾跳转与退出操作。 */
function handleMenuKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    closeMenu()
    return
  }

  const items = menuItems()
  const currentIndex = Math.max(items.indexOf(document.activeElement), 0)
  const targetIndex = {
    ArrowDown: currentIndex + 1,
    ArrowUp: currentIndex - 1,
    Home: 0,
    End: items.length - 1
  }[event.key]
  if (targetIndex === undefined || !items.length) return
  event.preventDefault()
  event.stopPropagation()
  items[(targetIndex + items.length) % items.length]?.focus()
}

/** 选择可用智能体，锁定时转交调用方处理。 */
function selectAgent(value) {
  if (value === props.modelValue) {
    closeMenu()
    return
  }
  if (props.locked) {
    emit('blocked', value)
    return
  }
  emit('update:modelValue', value)
  closeMenu()
}

/** 关闭菜单并触发管理操作。 */
function emitAction(name) {
  dropdownOpen.value = false
  emit(name)
}

useOutsidePointerdown(dropdownOpen, [triggerRef, panelRef])
watch(dropdownOpen, (isOpen) => {
  if (!isOpen) cancelPendingFocus()
})
onBeforeUnmount(cancelPendingFocus)
</script>

<style lang="less" scoped>
.agent-selection {
  display: block;
  width: 100%;
}

.agent-selection-trigger {
  display: inline-flex;
  height: 30px;
  padding: 0 8px;
  border: 0;
  border-radius: 8px;
  align-items: center;
  justify-content: flex-start;
  gap: 6px;
  width: 100%;
  min-width: 0;
  max-width: none;
  background: transparent;
  color: var(--gray-800);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
  transition:
    background-color 0.15s ease,
    color 0.15s ease;

  &:hover:not(:disabled) {
    background: var(--gray-50);
    color: var(--gray-900);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 1px;
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.6;
  }
}

.agent-selection-trigger-icon,
.agent-selection-chevron {
  flex-shrink: 0;
}

.agent-selection-trigger-label {
  min-width: 0;
  overflow: hidden;
  color: currentColor;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-selection-chevron {
  margin-left: auto;
  color: currentColor;
}

.agent-selection.compact {
  display: inline-flex;
  width: auto;

  .agent-selection-trigger {
    justify-content: center;
    max-width: min(240px, calc(100vw - 160px));
    gap: 4px;
  }

  .agent-selection-trigger-icon {
    display: none;
  }

  .agent-selection-chevron {
    margin-left: 0;
  }
}

@container (max-width: 640px) {
  .agent-selection.compact .agent-selection-trigger {
    width: 30px;
    padding-inline: 0;
  }

  .agent-selection.compact .agent-selection-trigger-icon {
    display: block;
  }

  .agent-selection.compact .agent-selection-trigger-label,
  .agent-selection.compact .agent-selection-chevron {
    display: none;
  }
}

@media (max-width: 520px) {
  .agent-selection.compact .agent-selection-trigger {
    max-width: calc(100vw - 112px);
  }
}
</style>

<style lang="less">
.agent-selection-overlay .agent-selection-panel {
  min-width: 188px;
  max-width: min(260px, calc(100vw - 24px));
  padding: 4px;
  background: var(--gray-0);
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  box-shadow: 0 8px 24px var(--shadow-4);
}

.agent-selection-overlay .agent-selection-option {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-width: 0;
  padding: 6px 8px;
  margin: 3px 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.agent-selection-overlay .agent-selection-option:first-child {
  margin-top: 0;
}

.agent-selection-overlay .agent-selection-option:last-child {
  margin-bottom: 0;
}

.agent-selection-overlay .agent-selection-option:hover,
.agent-selection-overlay .agent-selection-option.selected {
  background: var(--gray-50);
}

.agent-selection-overlay .agent-selection-option:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--main-color) 45%, transparent);
  outline-offset: -2px;
}

.agent-selection-overlay .agent-selection-option.disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.agent-selection-overlay .agent-selection-option-icon,
.agent-selection-overlay .agent-selection-option-action-icon,
.agent-selection-overlay .agent-selection-option-check {
  flex-shrink: 0;
}

.agent-selection-overlay .agent-selection-option-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--gray-800);
  font-size: 13px;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-selection-overlay .agent-selection-option-badge {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  line-height: 1.4;
}

.agent-selection-overlay .agent-selection-option-check {
  color: var(--main-600);
}

.agent-selection-overlay .agent-selection-option-action-icon {
  color: var(--gray-700);
}

.agent-selection-overlay .agent-selection-hint {
  padding: 6px 8px;
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.4;
}

.agent-selection-overlay .agent-selection-empty {
  padding: 16px 12px;
  color: var(--gray-500);
  font-size: 12px;
  text-align: center;
}

.agent-selection-overlay .agent-selection-divider {
  height: 1px;
  margin: 4px;
  background: var(--gray-100);
}

.agent-selection-overlay .agent-selection-actions {
  display: flex;
}

.agent-selection-overlay .agent-selection-actions .agent-selection-option {
  flex: 1;
  width: auto;
  margin: 0;
}
</style>
