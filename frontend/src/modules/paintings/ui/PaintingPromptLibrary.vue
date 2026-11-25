<script setup>
import { ArrowDown, ArrowUp, Pencil, Plus, Trash2 } from '@lucide/vue'
import { computed, ref } from 'vue'

const props = defineProps({
  owner: { type: String, required: true },
  open: { type: Boolean, default: false }
})
const emit = defineEmits(['update:open', 'apply'])
const key = `yuxi:painting-prompts:${props.owner}`
const prompts = ref([])
const editor = ref(null)
const error = ref('')
const valid = computed(() => Boolean(editor.value?.title.trim() && editor.value?.content.trim()))

try {
  const stored = JSON.parse(localStorage.getItem(key) || '[]')
  if (Array.isArray(stored)) prompts.value = stored
} catch {
  error.value = '提示词未能读取'
}

function write(next) {
  try {
    localStorage.setItem(key, JSON.stringify(next))
    prompts.value = next
    error.value = ''
    return true
  } catch {
    error.value = '提示词未能保存'
    return false
  }
}

function edit(prompt) {
  editor.value = prompt ? { ...prompt } : { id: crypto.randomUUID(), title: '', content: '' }
}

function save() {
  if (!valid.value) return
  const item = { ...editor.value, title: editor.value.title.trim(), content: editor.value.content.trim() }
  const exists = prompts.value.some((prompt) => prompt.id === item.id)
  const next = exists
    ? prompts.value.map((prompt) => prompt.id === item.id ? item : prompt)
    : [...prompts.value, item]
  if (write(next)) editor.value = null
}

function move(index, offset) {
  const next = [...prompts.value]
  const destination = index + offset
  if (destination < 0 || destination >= next.length) return
  const [item] = next.splice(index, 1)
  next.splice(destination, 0, item)
  write(next)
}

function close() {
  editor.value = null
  emit('update:open', false)
}

function apply(prompt) {
  emit('apply', prompt.content)
  close()
}
</script>

<template>
  <a-modal :open="open" title="管理全局提示词" :footer="null" width="520px" @cancel="close">
    <p v-if="error" role="alert" class="prompt-library-error">{{ error }}</p>
    <form v-if="editor" class="edit-prompt" @submit.prevent="save">
      <label for="preset-title">标题</label>
      <a-input id="preset-title" v-model:value="editor.title" :maxlength="100" />
      <label for="preset-content">内容</label>
      <a-textarea id="preset-content" v-model:value="editor.content" :rows="5" :maxlength="4000" />
      <div class="edit-actions">
        <a-button @click="editor = null">取消</a-button>
        <a-button type="primary" html-type="submit" :disabled="!valid">保存</a-button>
      </div>
    </form>
    <template v-else>
      <a-button aria-label="添加提示词" @click="edit()">
        <template #icon><Plus :size="15" /></template>
        添加提示词
      </a-button>
      <div class="prompt-library">
        <a-empty v-if="!prompts.length" description="暂无提示词" />
        <div v-for="(prompt, index) in prompts" :key="prompt.id" class="prompt-row">
          <button class="prompt-select" type="button" :title="prompt.title" @click="apply(prompt)">
            {{ prompt.title }}
          </button>
          <a-button type="text" size="small" aria-label="上移提示词" :disabled="index === 0" @click="move(index, -1)">
            <ArrowUp :size="14" />
          </a-button>
          <a-button type="text" size="small" aria-label="下移提示词" :disabled="index === prompts.length - 1" @click="move(index, 1)">
            <ArrowDown :size="14" />
          </a-button>
          <a-button type="text" size="small" aria-label="编辑提示词" @click="edit(prompt)">
            <Pencil :size="14" />
          </a-button>
          <a-popconfirm title="删除此提示词？" ok-text="删除" cancel-text="取消" @confirm="write(prompts.filter((item) => item.id !== prompt.id))">
            <a-button type="text" size="small" aria-label="删除提示词"><Trash2 :size="14" /></a-button>
          </a-popconfirm>
        </div>
      </div>
    </template>
  </a-modal>
</template>

<style scoped lang="less">
.edit-prompt,
.prompt-library { display: grid; gap: 8px; }
.edit-actions { display: flex; justify-content: flex-end; gap: 8px; }
.prompt-library { max-height: 60vh; overflow: auto; margin-top: 12px; }
.prompt-row {
  display: flex;
  align-items: center;
  min-width: 0;
  padding: 6px;
  border: 1px solid var(--gray-100);
  border-radius: 7px;
}
.prompt-select {
  flex: 1;
  overflow: hidden;
  padding: 4px 8px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--gray-1000);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
  &:hover { background: var(--gray-100); }
}
.prompt-library-error { color: var(--color-error-700); }
</style>
