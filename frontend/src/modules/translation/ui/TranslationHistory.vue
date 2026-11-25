<script setup>
import { computed, ref, watch } from 'vue'
import { ArrowLeft, ArrowRight, Clock3, Copy, Repeat, Search, Star, Trash2 } from '@lucide/vue'
import TranslationSidePanel from './TranslationSidePanel.vue'

const props = defineProps({
  open: { type: Boolean, required: true },
  entries: { type: Array, required: true },
  languages: { type: Array, required: true }
})
const emit = defineEmits(['close', 'reuse', 'copy', 'star', 'remove', 'clear'])
const keyword = ref('')
const starredOnly = ref(false)
const selectedId = ref('')
const selected = computed(() => props.entries.find(({ id }) => id === selectedId.value))
const results = computed(() => {
  const search = keyword.value.trim().toLowerCase()
  return props.entries.filter(({ source, target, starred }) =>
    (!starredOnly.value || starred) && (!search || [source, target].some((text) => text.toLowerCase().includes(search)))
  )
})

watch(() => props.open, (open) => {
  if (!open) selectedId.value = ''
})

function languageName(code) {
  return props.languages.find((language) => language.code === code)?.name || '自动检测'
}
</script>

<template>
  <TranslationSidePanel :open="open" :title="'翻译历史 (' + entries.length + ')'" @close="emit('close')">
    <template v-if="entries.length && !selected" #actions>
      <button
        type="button"
        class="translation-icon"
        title="仅显示收藏"
        aria-label="仅显示收藏"
        :aria-pressed="starredOnly"
        @click="starredOnly = !starredOnly"
      ><Star :size="15" :fill="starredOnly ? 'currentColor' : 'none'" /></button>
      <a-popconfirm
        title="确认清空翻译历史？"
        ok-text="清空"
        cancel-text="取消"
        @confirm="emit('clear')"
      >
        <button type="button" title="清空历史" aria-label="清空历史" class="translation-icon">
          <Trash2 :size="15" />
        </button>
      </a-popconfirm>
    </template>

    <div v-if="selected" class="history-detail">
      <button type="button" class="translation-button" @click="selectedId = ''">
        <ArrowLeft :size="14" /> 返回列表
      </button>
      <div class="history-languages">
        <span>{{ languageName(selected.sourceLanguage) }}</span>
        <ArrowRight :size="13" />
        <span>{{ languageName(selected.targetLanguage) }}</span>
        <span class="spacer" />
        <button
          type="button"
          class="translation-icon"
          title="收藏"
          aria-label="收藏"
          :aria-pressed="selected.starred"
          @click="emit('star', selected.id)"
        ><Star :size="14" :fill="selected.starred ? 'currentColor' : 'none'" /></button>
        <a-popconfirm title="删除这条翻译历史？" ok-text="删除" cancel-text="取消" @confirm="emit('remove', selected.id)">
          <button type="button" class="translation-icon" aria-label="删除翻译历史" title="删除翻译历史">
            <Trash2 :size="14" />
          </button>
        </a-popconfirm>
      </div>
      <section v-for="[title, text] in [['原文', selected.source], ['译文', selected.target]]" :key="title" class="history-text">
        <div>
          <span>{{ title }}</span>
          <button v-if="!selected.pdf" type="button" class="translation-icon" :aria-label="'复制' + title" @click="emit('copy', text)">
            <Copy :size="13" />
          </button>
        </div>
        <p>{{ text }}</p>
      </section>
      <div class="history-actions">
        <button type="button" class="translation-button" @click="emit('reuse', selected)">
          <Repeat :size="14" /> {{ selected.pdf ? '查看 PDF 翻译' : '使用此翻译' }}
        </button>
        <button v-if="!selected.pdf" type="button" class="translation-button primary-button" @click="emit('copy', selected.target)">
          <Copy :size="14" /> 复制译文
        </button>
      </div>
    </div>
    <div v-else-if="entries.length" class="history-detail">
      <a-input v-model:value="keyword" placeholder="搜索翻译历史" aria-label="搜索翻译历史" allow-clear>
        <template #prefix><Search :size="14" /></template>
      </a-input>
      <div v-if="!results.length" class="history-empty">暂无翻译历史</div>
      <div v-for="item in results" :key="item.id" class="history-item">
        <button type="button" class="history-item-open" @click="selectedId = item.id">
          <span>{{ item.source }}</span>
          <small>{{ item.target }}</small>
        </button>
        <button
          type="button"
          class="translation-icon"
          title="收藏"
          aria-label="收藏"
          :aria-pressed="item.starred"
          @click="emit('star', item.id)"
        ><Star :size="14" :fill="item.starred ? 'currentColor' : 'none'" /></button>
      </div>
    </div>
    <div v-else class="history-empty"><Clock3 :size="22" /> 暂无翻译历史</div>
  </TranslationSidePanel>
</template>

<style scoped lang="less">
.history-detail {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.history-detail > button {
  align-self: flex-start;
}

.history-languages,
.history-actions,
.history-item,
.history-text > div {
  display: flex;
  align-items: center;
  gap: 6px;
}

.history-languages {
  font-size: 13px;
  color: var(--gray-600);
}

.history-actions > button {
  flex: 1;
  justify-content: center;
  background: var(--gray-50);
}

.history-actions > .primary-button {
  background: #16a34a;
  color: #fff;
}

.history-empty {
  display: flex;
  height: 100%;
  min-height: 110px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: 9px;
  color: var(--gray-600);
}

.history-text,
.history-item {
  border-radius: 8px;
  background: var(--gray-50);
}

.history-text {
  padding: 10px;
}

.history-text > div {
  justify-content: space-between;
  color: var(--gray-600);
}

.history-text p {
  max-height: 200px;
  overflow: auto;
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.history-item {
  padding: 4px 6px;
}

.history-item-open {
  display: grid;
  flex: 1;
  min-width: 0;
  gap: 3px;
  padding: 5px;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  font: inherit;
  cursor: pointer;
}

.history-item-open span,
.history-item-open small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-item-open small {
  color: var(--gray-600);
}
</style>
