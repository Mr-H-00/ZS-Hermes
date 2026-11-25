<script setup>
import { computed, ref, watch } from 'vue'
import { ArrowLeftRight, CircleHelp, Pencil, Plus, Trash2 } from '@lucide/vue'
import { defaultPrompt } from '../model/languages'
import TranslationLanguageSelect from './TranslationLanguageSelect.vue'
import TranslationReasoningSelect from './TranslationReasoningSelect.vue'
import TranslationSidePanel from './TranslationSidePanel.vue'

const props = defineProps({
  options: { type: Array, required: true },
  open: { type: Boolean, required: true }
})
const emit = defineEmits(['close'])
const preferences = defineModel({ type: Object, required: true })
const edited = ref('')
const name = ref('')
const code = ref('')
const bidirectionalError = ref(false)
const languageError = ref('')
const toggles = [
  ['markdown', 'Markdown 预览'],
  ['autoCopy', '翻译完成后自动复制'],
  ['scrollSync', '滚动同步设置']
]
const detectionOptions = [
  { value: 'auto', label: '自动' },
  { value: 'algorithm', label: '算法' },
  { value: 'llm', label: 'LLM' }
]
const samplingOptions = [
  { value: 'temperature', enabled: 'enableTemperature', title: '模型温度', hint: '启用后控制回复随机性', max: 2, step: 0.1, digits: 1 },
  { value: 'topP', enabled: 'enableTopP', title: 'Top-P', hint: '启用后限制采样范围', max: 1, step: 0.05, digits: 2 }
]

const editingExisting = computed(() => Boolean(edited.value && edited.value !== '+'))
watch(() => props.open, (open) => {
  if (!open) edited.value = ''
})

function setLanguage(index, value) {
  const key = index === 0 ? 'bidirectionalSource' : 'bidirectionalTarget'
  const opposite = index === 0 ? preferences.value.bidirectionalTarget : preferences.value.bidirectionalSource
  bidirectionalError.value = value === opposite
  if (!bidirectionalError.value) preferences.value[key] = value
}

function editLanguage(item) {
  edited.value = item?.code || '+'
  name.value = item?.name || ''
  code.value = item?.code || ''
  languageError.value = ''
}

function saveLanguage() {
  const nextName = name.value.trim()
  const nextCode = code.value.trim()
  if (!nextName || !/^[a-z]{2,8}(?:-[a-z0-9]{2,8})*$/i.test(nextCode)) {
    languageError.value = '填写语言名称及有效的语言代码，如 eo 或 pt-BR。'
    return
  }
  if (!editingExisting.value && ['auto', ...props.options.map((item) => item.code.toLowerCase())].includes(nextCode.toLowerCase())) {
    languageError.value = '该语言代码已被使用。'
    return
  }
  if (!editingExisting.value && props.options.length >= 100) {
    languageError.value = '语言列表最多支持 100 项。'
    return
  }
  const item = { code: nextCode, name: nextName, flag: '🏳️' }
  if (editingExisting.value) {
    preferences.value.customLanguages = preferences.value.customLanguages.map((language) =>
      language.code === nextCode ? item : language
    )
  } else {
    preferences.value.customLanguages = [...preferences.value.customLanguages, item]
  }
  edited.value = ''
}

function removeLanguage(item) {
  preferences.value.customLanguages = preferences.value.customLanguages.filter(({ code: key }) => key !== item.code)
  if (preferences.value.target === item.code) preferences.value.target = 'zh'
  if ([preferences.value.bidirectionalSource, preferences.value.bidirectionalTarget].includes(item.code)) {
    preferences.value.bidirectionalSource = 'en'
    preferences.value.bidirectionalTarget = 'zh'
  }
}
</script>

<template>
  <TranslationSidePanel :open="open" title="翻译设置" @close="emit('close')">
    <div class="translation-settings">
      <section class="settings-rows" aria-label="翻译选项">
        <label v-for="[key, title] in toggles" :key="key" class="settings-row">
          <span>{{ title }}</span>
          <a-switch v-model:checked="preferences[key]" size="small" :aria-label="title" />
        </label>
        <div class="settings-row">
          <span class="with-help">
            自动检测方法
            <a-tooltip title="自动检测输入语言时使用的方法">
              <CircleHelp :size="13" aria-label="自动检测方法说明" />
            </a-tooltip>
          </span>
          <a-segmented v-model:value="preferences.detection" :options="detectionOptions" aria-label="自动检测方法" />
        </div>
        <label class="settings-row">
          <span class="with-help">
            双向翻译设置
            <a-tooltip title="开启后，在选择的两种语言之间自动互译">
              <CircleHelp :size="13" aria-label="双向翻译设置说明" />
            </a-tooltip>
          </span>
          <a-switch v-model:checked="preferences.bidirectional" size="small" aria-label="双向翻译设置" />
        </label>
        <template v-if="preferences.bidirectional">
          <div class="pair-row">
            <TranslationLanguageSelect
              :model-value="preferences.bidirectionalSource"
              :options="options"
              label="双向翻译源语言"
              @update:model-value="setLanguage(0, $event)"
            />
            <ArrowLeftRight :size="14" aria-hidden="true" />
            <TranslationLanguageSelect
              :model-value="preferences.bidirectionalTarget"
              :options="options"
              label="双向翻译目标语言"
              @update:model-value="setLanguage(1, $event)"
            />
          </div>
          <div v-if="bidirectionalError" class="settings-error" role="alert">源语言和目标语言相同</div>
        </template>
      </section>

      <section class="settings-rows" aria-labelledby="translate-parameters-title">
        <h3 id="translate-parameters-title">模型参数</h3>
        <div class="settings-row">
          <span>思考强度</span>
          <TranslationReasoningSelect v-model="preferences.reasoning" />
        </div>
        <div v-for="option in samplingOptions" :key="option.value" class="sampling-option">
          <label class="settings-row">
            <span>
              {{ option.title }}
              <small>{{ option.hint }}</small>
            </span>
            <span class="sampling-control">
              <small>{{ preferences[option.enabled] ? preferences[option.value].toFixed(option.digits) : '模型默认' }}</small>
              <a-switch v-model:checked="preferences[option.enabled]" size="small" :aria-label="option.title" />
            </span>
          </label>
          <a-slider
            v-if="preferences[option.enabled]"
            v-model:value="preferences[option.value]"
            :min="0"
            :max="option.max"
            :step="option.step"
            :aria-label="option.title"
          />
        </div>
      </section>

      <section class="settings-section" aria-labelledby="translate-prompt-title">
        <div class="settings-row">
          <h3 id="translate-prompt-title">翻译提示词</h3>
          <button
            v-if="preferences.prompt !== defaultPrompt"
            type="button"
            class="translation-button prompt-reset"
            @click="preferences.prompt = defaultPrompt"
          >重置</button>
        </div>
        <textarea
          v-model="preferences.prompt"
          aria-label="翻译提示词"
          :maxlength="4000"
          rows="5"
          spellcheck="false"
        />
      </section>

      <section class="settings-section" aria-labelledby="translate-custom-title">
        <h3 id="translate-custom-title">自定义语言</h3>
        <div v-if="!preferences.customLanguages.length" class="custom-empty">无结果</div>
        <div v-for="item in preferences.customLanguages" :key="item.code" class="custom-language-row">
          <span>{{ item.name }}</span>
          <code>{{ item.code }}</code>
          <button type="button" class="translation-icon" :aria-label="'编辑 ' + item.name" @click="editLanguage(item)">
            <Pencil :size="13" aria-hidden="true" />
          </button>
          <a-popconfirm
            title="删除此自定义语言？"
            ok-text="删除"
            cancel-text="取消"
            @confirm="removeLanguage(item)"
          >
            <button type="button" class="translation-icon" :aria-label="'删除 ' + item.name">
              <Trash2 :size="13" aria-hidden="true" />
            </button>
          </a-popconfirm>
        </div>
        <form v-if="edited" class="custom-form" @submit.prevent="saveLanguage">
          <label>语言名称 <a-input v-model:value="name" :maxlength="80" aria-label="语言名称" /></label>
          <label>语言代码 <a-input v-model:value="code" :maxlength="32" :disabled="editingExisting" aria-label="语言代码" /></label>
          <div v-if="languageError" class="settings-error" role="alert">{{ languageError }}</div>
          <div class="custom-actions">
            <a-button @click="edited = ''">取消</a-button>
            <a-button type="primary" html-type="submit">保存</a-button>
          </div>
        </form>
        <button v-else type="button" class="add-language" @click="editLanguage()">
          <Plus :size="15" aria-hidden="true" /> 添加语言
        </button>
      </section>
    </div>
  </TranslationSidePanel>
</template>

<style scoped lang="less">
.translation-settings {
  display: flex;
  flex-direction: column;
  gap: 32px;
  font-size: 14px;

  h3 {
    margin: 0;
    color: var(--gray-1000);
    font-size: 14px;
    font-weight: 650;
  }

  small {
    display: block;
    margin-top: 2px;
    color: var(--gray-600);
    font-size: 12px;
  }

  :deep(.ant-switch) {
    min-width: 36px;
    background-color: var(--gray-200);
  }

  :deep(.ant-switch-checked) {
    background-color: #20b446;
  }

  :deep(.ant-segmented) {
    padding: 2px;
    border: 1px solid var(--gray-150);
    border-radius: 999px;
  }

  :deep(.ant-segmented-item),
  :deep(.ant-segmented-thumb) {
    border-radius: 999px;
  }
}

.settings-rows,
.settings-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.settings-rows {
  gap: 20px;
}

.settings-row {
  display: flex;
  min-height: 26px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.with-help,
.sampling-control,
.pair-row {
  display: flex;
  align-items: center;
  gap: 5px;
}

.with-help svg {
  color: var(--gray-600);
}

.sampling-control {
  flex-shrink: 0;
  gap: 8px;
}

.sampling-control small {
  margin: 0;
}

.pair-row :deep(.ant-select) {
  flex: 1;
  min-width: 0;
}

.settings-section textarea {
  min-height: 120px;
  width: 100%;
  resize: vertical;
  padding: 12px;
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  background: var(--gray-50);
  color: var(--gray-600);
  font: inherit;
  line-height: 1.6;
}

.settings-error {
  color: var(--color-error, #c43a30);
  font-size: 12px;
}

.prompt-reset {
  padding: 0 5px;
  color: var(--gray-600);
  font-size: 12px;
}

.custom-empty {
  padding: 7px;
  border-radius: 8px;
  background: var(--gray-50);
  color: var(--gray-600);
  text-align: center;
}

.custom-language-row {
  display: flex;
  align-items: center;
  min-width: 0;
  padding-inline-start: 5px;

  span {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
  }

  code {
    color: var(--gray-600);
  }
}

.custom-form,
.custom-form label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.custom-form {
  padding: 10px;
  background: var(--gray-50);
  border-radius: 8px;
}

.custom-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.add-language {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 6px;
  min-height: 34px;
  border: 1px solid var(--gray-150, var(--gray-200));
  border-radius: 8px;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
</style>
