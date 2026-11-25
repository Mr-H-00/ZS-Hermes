<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'
import {
  ArrowLeftRight, Check, CirclePause, Copy, History, Image, Languages,
  LoaderCircle, Mic, NotebookPen, SlidersHorizontal, Square, Volume2, X
} from '@lucide/vue'

import { resolveApiErrorMessage } from '@/apis/base'
import {
  detectTranslationLanguage,
  extractTranslationFile,
  getPdfTranslationCapability,
  translatePdf,
  translateText
} from '@/apis/translation_api'
import { uploadWorkspaceFiles } from '@/apis/workspace_api'
import { useUserStore } from '@/modules/identity'
import { loadModelMetadataCatalog, useConfigStore, useModelDirectory } from '@/modules/settings'
import { useVoiceStore } from '@/modules/voice'
import ActionDropdown from '@/shared/ui/ActionDropdown.vue'
import { copyTextToClipboard } from '@/shared/lib/clipboard'
import deepseekIcon from '../assets/cherry-studio/deepseek.svg'
import uploadExcel from '../assets/cherry-studio/upload-excel.svg'
import uploadImage from '../assets/cherry-studio/upload-image.svg'
import uploadPdf from '../assets/cherry-studio/upload-pdf.svg'
import uploadPpt from '../assets/cherry-studio/upload-ppt.svg'
import uploadText from '../assets/cherry-studio/upload-text.svg'
import uploadWord from '../assets/cherry-studio/upload-word.svg'
import { defaultPrompt, detectByAlgorithm, languages } from '../model/languages'
import { deletePdfHistory, readPdfHistory, savePdfHistory } from '../model/pdfHistory'
import TranslationHistory from './TranslationHistory.vue'
import TranslationLanguageSelect from './TranslationLanguageSelect.vue'
import TranslationPdfPane from './TranslationPdfPane.vue'
import TranslationReasoningSelect from './TranslationReasoningSelect.vue'
import TranslationSettings from './TranslationSettings.vue'

const user = useUserStore()
const config = useConfigStore()
const modelDirectory = useModelDirectory()
const voice = useVoiceStore()
const voiceOwner = Symbol('translation')
const uploadIcons = [uploadImage, uploadPdf, uploadWord, uploadPpt, uploadText, uploadExcel]
const acceptedFiles = '.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.pdf,.docx,.pptx,.xlsx,.xls,.txt,.md,.csv'
const maxCharacters = 10_000
const maxHistoryBytes = 2_000_000
const markdown = new MarkdownIt()
// 译文可能包含供应商生成的外部地址。用图片说明替代自动加载的远程资源。
markdown.renderer.rules.image = (tokens, index) => markdown.utils.escapeHtml(tokens[index].content)
const defaults = {
  markdown: true,
  autoCopy: false,
  scrollSync: false,
  detection: 'auto',
  bidirectional: false,
  bidirectionalSource: 'zh',
  bidirectionalTarget: 'en',
  reasoning: 'none',
  enableTemperature: false,
  temperature: 1,
  enableTopP: false,
  topP: 1,
  prompt: defaultPrompt,
  customLanguages: [],
  target: 'zh',
  modelSpec: ''
}
const preferences = ref({ ...defaults })
const history = ref([])
const source = ref('')
const output = ref('')
const sourceLanguage = ref('auto')
const sourceFile = ref(null)
const pdfSourceUrl = ref('')
const pdfResultUrl = ref('')
const pdfChecking = ref(false)
const pdfAvailable = ref(false)
const pdfTarget = ref(defaults.target)
const pdfSourceLanguage = ref('auto')
const pdfTextActive = ref(false)
const pdfTextOutput = ref('')
const error = ref('')
const pending = ref('')
const activeSide = ref('')
const modelOpen = ref(false)
const modelSearch = ref('')
const modelsLoading = ref(false)
const modelsError = ref(false)
const models = ref({})
const modelCatalog = ref({})
const filePicker = ref(null)
const sourceEditor = ref(null)
const sourceViewport = ref(null)
const outputViewport = ref(null)
const capture = ref(null)
const dragging = ref(false)
const copyingImage = ref(false)
const notesSaving = ref(false)
const sourceCopied = ref(false)
const targetCopied = ref(false)
let request
const copyTimers = {}
let scrollFrame
let scrollSource
let mounted = true

const allLanguages = computed(() => [...languages, ...preferences.value.customLanguages])
const pdfMode = computed(() => Boolean(pdfSourceUrl.value))
const builtInSource = computed(() => [{ code: 'auto', name: '自动检测' }, ...languages])
const targetLanguage = computed({
  get: () => pdfMode.value ? pdfTarget.value : preferences.value.target,
  set: (value) => {
    if (pdfMode.value) pdfTarget.value = value
    else preferences.value.target = value
  }
})
const bidirectional = computed(() => !pdfMode.value && preferences.value.bidirectional)
const activeModels = computed(() =>
  Object.values(models.value).flatMap(({ models: items }) => items || [])
)
const selectedModel = computed(() => activeModels.value.find(({ spec }) => spec === preferences.value.modelSpec))
const modelName = computed(() => selectedModel.value?.display_name || preferences.value.modelSpec || '选择翻译模型')
const supportsReasoning = computed(() => {
  if (!selectedModel.value) return false
  const { spec, model_id: modelId } = selectedModel.value
  const providerId = spec.split(':', 1)[0]
  const configured = modelCatalog.value[providerId]?.models?.[modelId]
  return configured?.reasoning ?? Object.values(modelCatalog.value).some(({ models: catalog }) => catalog?.[modelId]?.reasoning)
})
const filteredModels = computed(() => {
  const query = modelSearch.value.trim().toLowerCase()
  return Object.values(models.value).map((provider) => ({
    ...provider,
    models: (provider.models || []).filter((model) =>
      [provider.provider_display_name, model.display_name, model.spec].some((value) => value?.toLowerCase().includes(query))
    )
  })).filter(({ models: items }) => items.length)
})
const recognitionActive = computed(() => voice.dictationOwner === voiceOwner)
const playing = computed(() => voice.playback?.owner === voiceOwner)
const canTranslate = computed(() => Boolean(
  !pending.value && preferences.value.modelSpec &&
  (pdfMode.value ? !pdfChecking.value : source.value.trim())
))
const visibleOutput = computed(() => pdfMode.value ? pdfTextOutput.value : output.value)
const canUseOutput = computed(() => Boolean(!pending.value && visibleOutput.value.trim()))
const renderedOutput = computed(() => DOMPurify.sanitize(markdown.render(visibleOutput.value)))

watch(() => user.uid, (uid) => {
  abort()
  releasePdf()
  voice.release(voiceOwner)
  preferences.value = readStored(uid, 'preferences', defaults)
  history.value = readStored(uid, 'history', [])
  const draft = readStored(uid, 'draft', { source: '', output: '', sourceLanguage: 'auto' })
  source.value = draft.source
  output.value = draft.output
  sourceLanguage.value = draft.sourceLanguage
  sourceFile.value = null
  error.value = ''
  activeSide.value = ''
  modelOpen.value = false
  modelSearch.value = ''
  if (uid) void loadModels()
}, { immediate: true, flush: 'sync' })
watch(preferences, (value) => persist('preferences', value), { deep: true })
watch(history, (value) => persist('history', value), { deep: true })
watch([source, output, sourceLanguage], ([nextSource, nextOutput, nextSourceLanguage]) => {
  persist('draft', { source: nextSource, output: nextOutput, sourceLanguage: nextSourceLanguage })
})
watch(visibleOutput, () => {
  targetCopied.value = false
  voice.stopPlayback(voiceOwner)
})
watch(source, () => {
  sourceCopied.value = false
  resizeInput()
}, { immediate: true })

onBeforeUnmount(() => {
  mounted = false
  abort()
  releasePdf()
  voice.release(voiceOwner)
  Object.values(copyTimers).forEach(clearTimeout)
  if (scrollFrame) cancelAnimationFrame(scrollFrame)
})

function readStored(uid, section, fallback) {
  if (!uid) return Array.isArray(fallback) ? [] : { ...fallback }
  try {
    const stored = JSON.parse(localStorage.getItem(`yuxi:translation:${uid}:${section}`))
    return Array.isArray(fallback) ? (Array.isArray(stored) ? stored : []) : { ...fallback, ...stored }
  } catch {
    return Array.isArray(fallback) ? [] : { ...fallback }
  }
}

function persist(section, value) {
  if (!user.uid) return
  try {
    localStorage.setItem(`yuxi:translation:${user.uid}:${section}`, JSON.stringify(value))
  } catch {
    message.warning('浏览器存储不可用，本次内容仅保留在当前页面')
  }
}

async function loadModels() {
  if (modelsLoading.value) return
  modelsLoading.value = true
  modelsError.value = false
  const uid = user.uid
  try {
    const [directory, metadata] = await Promise.all([
      modelDirectory.load(), loadModelMetadataCatalog().catch(() => ({}))
    ])
    if (uid !== user.uid || !mounted) return
    models.value = directory
    modelCatalog.value = metadata.providers || {}
    if (!activeModels.value.some(({ spec }) => spec === preferences.value.modelSpec)) {
      const preferred = [config.config?.fast_model, config.config?.default_model]
        .find((candidate) => activeModels.value.some(({ spec }) => spec === candidate))
      preferences.value.modelSpec = preferred || activeModels.value[0]?.spec || ''
    }
  } catch {
    modelsError.value = true
  } finally {
    modelsLoading.value = false
    if (mounted && user.uid && uid !== user.uid) void loadModels()
  }
}

function openModels(open) {
  modelOpen.value = open
  if (open) void loadModels()
  else modelSearch.value = ''
}

function chooseModel(spec) {
  preferences.value.modelSpec = spec
  openModels(false)
}

function language(code) {
  return allLanguages.value.find((item) => item.code === code)
}

function toggleSide(side) {
  activeSide.value = activeSide.value === side ? '' : side
}

function abort() {
  request?.abort()
  request = undefined
  pending.value = ''
  pdfChecking.value = false
}

function releasePdf() {
  for (const url of [pdfSourceUrl.value, pdfResultUrl.value]) {
    if (url) URL.revokeObjectURL(url)
  }
  pdfSourceUrl.value = ''
  pdfResultUrl.value = ''
  pdfAvailable.value = false
  pdfTextActive.value = false
  pdfTextOutput.value = ''
}

function closePdf() {
  abort()
  releasePdf()
  sourceFile.value = null
  pdfSourceLanguage.value = 'auto'
  error.value = ''
}

function clear() {
  closePdf()
  source.value = ''
  output.value = ''
  sourceLanguage.value = 'auto'
  sourceEditor.value?.focus()
}

async function resizeInput() {
  await nextTick()
  const editor = sourceEditor.value
  if (editor) {
    editor.style.height = 'auto'
    editor.style.height = `${editor.scrollHeight}px`
  }
}

function setSource(text) {
  source.value = text
  sourceLanguage.value = 'auto'
  if (!text) output.value = ''
  error.value = ''
}

function syncScroll(event, target) {
  if (!preferences.value.scrollSync || !target) return
  const element = event.currentTarget
  if (scrollSource === target) return
  const maximum = element.scrollHeight - element.clientHeight
  scrollSource = element
  target.scrollTop = maximum > 0 ? (element.scrollTop / maximum) * (target.scrollHeight - target.clientHeight) : 0
  if (scrollFrame) cancelAnimationFrame(scrollFrame)
  scrollFrame = requestAnimationFrame(() => {
    scrollSource = undefined
    scrollFrame = undefined
  })
}

async function detect(text, settings, signal) {
  if (settings.detection === 'algorithm' || (settings.detection === 'auto' && text.length >= 300)) {
    const result = detectByAlgorithm(text)
    if (result || settings.detection === 'algorithm') return result
  }
  try {
    const result = await detectTranslationLanguage({
      text,
      model_spec: settings.modelSpec,
      languages: allLanguages.value.map(({ code, name }) => ({ code, name }))
    }, { signal })
    return allLanguages.value.some(({ code }) => code === result.source_language)
      ? result.source_language : undefined
  } catch (reason) {
    if (signal.aborted || settings.detection === 'llm') throw reason
    return undefined
  }
}

function addHistory(entry) {
  const entries = [entry, ...history.value]
  const next = entries.slice(0, 200)
  while (next.length > 1 && new Blob([JSON.stringify(next)]).size > maxHistoryBytes) next.pop()
  history.value = next
  if (entries.length > next.length) void removeHistoryFiles(user.uid, entries.slice(next.length))
}

async function removeHistoryFiles(owner, entries) {
  const pdfs = entries.filter(({ pdf }) => pdf).map(({ id }) => id)
  if (!pdfs.length) return true
  try {
    await deletePdfHistory(owner, pdfs)
    return true
  } catch {
    message.error('无法清理本地 PDF 历史文件')
    return false
  }
}

async function removeHistory(id) {
  const owner = user.uid
  const entries = id ? history.value.filter((entry) => entry.id === id) : history.value
  if (!(await removeHistoryFiles(owner, entries)) || owner !== user.uid) return
  const removed = new Set(entries.map((entry) => entry.id))
  history.value = history.value.filter((entry) => !removed.has(entry.id))
}

function star(id) {
  history.value = history.value.map((entry) => entry.id === id ? { ...entry, starred: !entry.starred } : entry)
}

async function reuse(entry) {
  if (entry.pdf) {
    const owner = user.uid
    try {
      const file = await readPdfHistory(owner, entry.id)
      if (owner !== user.uid || activeSide.value !== 'history' || !mounted) return
      if (!file) throw new Error()
      const preview = openPdf(file.source)
      pdfSourceLanguage.value = entry.sourceLanguage
      pdfTarget.value = entry.targetLanguage
      pdfResultUrl.value = URL.createObjectURL(file.result)
      activeSide.value = ''
      await preview
    } catch {
      if (owner === user.uid && mounted) message.error('无法读取本地 PDF 历史文件')
    }
    return
  }
  closePdf()
  source.value = entry.source
  output.value = entry.target
  sourceLanguage.value = language(entry.sourceLanguage)?.code ?? 'auto'
  if (language(entry.targetLanguage)) preferences.value.target = entry.targetLanguage
  activeSide.value = ''
}

async function translate() {
  if (!canTranslate.value) return
  if (pdfMode.value && pdfAvailable.value) {
    await generatePdf()
    return
  }
  const pdf = pdfMode.value
  if (pdf) pdfTextActive.value = true
  const automatic = bidirectional.value || pdf
  const current = new AbortController()
  request = current
  pending.value = pdf ? '读取 PDF 正文中...' : automatic ? '检测中...' : '翻译中...'
  error.value = ''
  const resultText = pdf ? pdfTextOutput : output
  resultText.value = ''
  const settings = { ...preferences.value, target: targetLanguage.value }
  const sourceCode = pdf ? pdfSourceLanguage.value : sourceLanguage.value
  try {
    const text = pdf
      ? (await extractTranslationFile(sourceFile.value, { signal: current.signal })).text
      : source.value
    if (request !== current) return
    if ([...text].length > maxCharacters) {
      error.value = `原文不能超过 ${maxCharacters} 个字符`
      return
    }
    pending.value = automatic ? '检测中...' : '翻译中...'
    const detected = sourceCode === 'auto' && automatic
      ? await detect(text, settings, current.signal) : undefined
    if (request !== current) return
    const from = detected || sourceCode
    let to = settings.target
    if (bidirectional.value) {
      const { bidirectionalSource, bidirectionalTarget } = settings
      if (from === bidirectionalSource) to = bidirectionalTarget
      else if (from === bidirectionalTarget) to = bidirectionalSource
      else {
        error.value = '源语言与设置的语言不同'
        return
      }
    }
    if (from === to) {
      error.value = '源语言和目标语言相同'
      return
    }
    pending.value = '翻译中...'
    const result = await translateText({
      text,
      source_language: from,
      target_language: to,
      model_spec: settings.modelSpec,
      ...(settings.prompt.trim() && { prompt: settings.prompt }),
      ...(language(from) && { source_language_name: language(from).name }),
      ...(language(to) && { target_language_name: language(to).name }),
      ...(settings.enableTemperature && { temperature: settings.temperature }),
      ...(settings.enableTopP && { top_p: settings.topP }),
      ...((supportsReasoning.value || settings.reasoning !== 'none') && { reasoning_effort: settings.reasoning })
    }, { signal: current.signal })
    if (request !== current) return
    resultText.value = result.translated_text
    const id = crypto.randomUUID()
    addHistory({
      id, source: text, target: result.translated_text, sourceLanguage: from,
      targetLanguage: to, created: Date.now(), starred: false
    })
    if (settings.autoCopy) void copy(result.translated_text, 'output')
    if (!automatic && from === 'auto') {
      void detect(text, settings, current.signal).then((code) => {
        if (code && mounted && !current.signal.aborted) {
          history.value = history.value.map((entry) => entry.id === id ? { ...entry, sourceLanguage: code } : entry)
        }
      }).catch(() => {})
    }
  } catch (reason) {
    if (request === current) error.value = resolveApiErrorMessage(reason, '翻译失败，请重试')
  } finally {
    if (request === current) {
      request = undefined
      pending.value = ''
    }
  }
}

async function importFile(files) {
  if (pending.value || !files.length) return
  if (files.length !== 1) {
    error.value = '不允许上传多个文件'
    return
  }
  const [file] = files
  const pdf = file.name.toLowerCase().endsWith('.pdf')
  const maxSize = pdf ? 20 : 10
  if (file.size > maxSize * 1024 * 1024) {
    error.value = `文件过大，最大支持 ${maxSize} MB`
    return
  }
  if (pdf) {
    await openPdf(file)
    return
  }
  await extractFile(file)
}

async function openPdf(file) {
  const target = targetLanguage.value
  abort()
  releasePdf()
  voice.release(voiceOwner)
  pdfTarget.value = languages.some(({ code }) => code === target) ? target : defaults.target
  sourceFile.value = file
  pdfSourceLanguage.value = 'auto'
  error.value = ''
  pdfSourceUrl.value = URL.createObjectURL(file)
  pdfChecking.value = true
  const current = new AbortController()
  request = current
  try {
    const response = await getPdfTranslationCapability({ signal: current.signal })
    if (request === current) pdfAvailable.value = response.available === true
  } catch (reason) {
    if (request === current) error.value = resolveApiErrorMessage(reason, '无法获取 PDF 翻译能力')
  } finally {
    if (request === current) {
      request = undefined
      pdfChecking.value = false
    }
  }
}

async function generatePdf() {
  if (pdfSourceLanguage.value === pdfTarget.value) {
    error.value = '源语言和目标语言相同'
    return
  }
  abort()
  pdfTextActive.value = false
  pdfTextOutput.value = ''
  if (pdfResultUrl.value) URL.revokeObjectURL(pdfResultUrl.value)
  pdfResultUrl.value = ''
  error.value = ''
  pending.value = '正在翻译 PDF...'
  const current = new AbortController()
  request = current
  const owner = user.uid
  const file = sourceFile.value
  const sourceCode = pdfSourceLanguage.value
  const targetCode = pdfTarget.value
  try {
    const result = await translatePdf(file, {
      source_language: sourceCode,
      target_language: targetCode,
      model_spec: preferences.value.modelSpec
    }, { signal: current.signal })
    if (request !== current) return
    if (result.type !== 'application/pdf') throw new Error('服务未返回译文 PDF')
    pdfResultUrl.value = URL.createObjectURL(result)
    const id = crypto.randomUUID()
    try {
      await savePdfHistory(owner, id, file, result)
      if (owner !== user.uid || !mounted) {
        await deletePdfHistory(owner, [id])
        return
      }
      addHistory({
        id, pdf: true, source: file.name, target: file.name.replace(/\.pdf$/i, '') + '-译文.pdf',
        sourceLanguage: sourceCode, targetLanguage: targetCode, created: Date.now(), starred: false
      })
    } catch {
      if (owner === user.uid && mounted) message.warning('PDF 已翻译，未能保存到本地历史')
    }
  } catch (reason) {
    if (request === current) error.value = resolveApiErrorMessage(reason, 'PDF 翻译失败，请重试')
  } finally {
    if (request === current) {
      request = undefined
      pending.value = ''
    }
  }
}

async function extractFile(file) {
  abort()
  const current = new AbortController()
  request = current
  pending.value = '读取文件内容中...'
  error.value = ''
  try {
    const result = await extractTranslationFile(file, { signal: current.signal })
    if (request !== current) return
    releasePdf()
    source.value += result.text
    output.value = ''
    sourceFile.value = file
    sourceLanguage.value = 'auto'
  } catch (reason) {
    if (request === current) error.value = resolveApiErrorMessage(reason, '读取文件内容失败')
  } finally {
    if (request === current) {
      request = undefined
      pending.value = ''
    }
  }
}

function onDrop(event) {
  dragging.value = false
  if (event.dataTransfer?.files.length) void importFile(event.dataTransfer.files)
  else if (!pending.value && !pdfMode.value) setSource(source.value + (event.dataTransfer?.getData('text/plain') || ''))
}

function onPaste(event) {
  if (!event.clipboardData?.files.length) return
  event.preventDefault()
  void importFile(event.clipboardData.files)
}

async function copy(text, kind) {
  if (!text.trim()) return
  try {
    await copyTextToClipboard(text)
    if (!kind || !mounted) return
    const copied = kind === 'input' ? sourceCopied : targetCopied
    copied.value = true
    clearTimeout(copyTimers[kind])
    copyTimers[kind] = setTimeout(() => { copied.value = false }, 1500)
  } catch {
    message.error('复制失败')
  }
}

function dictation() {
  if (recognitionActive.value) {
    voice.stopDictation(voiceOwner)
    return
  }
  if (!voice.preferences.dictationEnabled) {
    message.info('请先在设置 → 语音中启用听写')
    return
  }
  voice.startDictation(voiceOwner, {
    onText: (text) => {
      setSource(source.value + (source.value && !/\s$/.test(source.value) ? ` ${text}` : text))
      sourceEditor.value?.focus()
    },
    onError: (description) => message.warning(description)
  })
}

function readAloud() {
  if (playing.value) voice.stopPlayback(voiceOwner)
  else voice.speak(voiceOwner, 'translation-result', visibleOutput.value, {
    onError: (description) => message.warning(description)
  })
}

async function copyAsImage() {
  if (copyingImage.value || !capture.value || !canUseOutput.value) return
  copyingImage.value = true
  const previous = visibleOutput.value
  try {
    if (!navigator.clipboard?.write || !globalThis.ClipboardItem) throw new Error()
    const node = capture.value
    const blob = import('html-to-image').then(({ toBlob }) => toBlob(node, {
      backgroundColor: getComputedStyle(node).getPropertyValue('--gray-0').trim(),
      skipFonts: true,
      pixelRatio: 2
    })).then((image) => {
      if (!image || previous !== visibleOutput.value || !mounted || pending.value) throw new Error()
      return image
    })
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
    message.success('已复制图片')
  } catch {
    if (mounted && previous === visibleOutput.value) message.error('复制图片失败')
  } finally {
    copyingImage.value = false
  }
}

async function saveNote() {
  if (notesSaving.value || !canUseOutput.value) return
  notesSaving.value = true
  try {
    const filename = `译文-${new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 17)}.md`
    const file = new File([visibleOutput.value], filename, { type: 'text/markdown' })
    await uploadWorkspaceFiles('/', [file])
    message.success('译文已保存到网盘')
  } catch (reason) {
    message.error(resolveApiErrorMessage(reason, '保存译文失败'))
  } finally {
    notesSaving.value = false
  }
}
</script>

<template>
  <section
    class="cherry-translation"
    aria-label="翻译"
    data-ui="translate.view"
    @dragover.prevent
    @drop.prevent="onDrop"
  >
    <div class="translation-toolbar">
      <TranslationLanguageSelect
        v-if="pdfMode"
        v-model="pdfSourceLanguage"
        :options="builtInSource"
        :disabled="Boolean(pending)"
        label="源语言"
        class="toolbar-language"
      />
      <template v-if="bidirectional">
        <button type="button" class="translation-button pair-label" @click="toggleSide('settings')">
          <span>{{ language(preferences.bidirectionalSource)?.name }}</span>
          <ArrowLeftRight :size="14" />
          <span>{{ language(preferences.bidirectionalTarget)?.name }}</span>
        </button>
      </template>
      <template v-else>
        <span v-if="!pdfMode" class="translate-to-label" aria-hidden="true">译为</span>
        <TranslationLanguageSelect
          v-model="targetLanguage"
          :options="pdfMode ? languages : allLanguages"
          :disabled="Boolean(pending)"
          label="目标语言"
          class="toolbar-language"
        />
      </template>
      <button v-if="pending" type="button" class="translation-button stop-button" @click="abort">
        <CirclePause :size="15" aria-hidden="true" /> 停止
      </button>
      <button v-else type="button" class="translation-button translate-button" :disabled="!canTranslate" @click="translate">
        <Languages :size="15" aria-hidden="true" /> 翻译
      </button>
      <span class="spacer" />
      <div class="toolbar-actions">
        <ActionDropdown
          :open="modelOpen"
          v-model:search="modelSearch"
          search-placeholder="搜索模型"
          :width="280"
          @update:open="openModels"
        >
          <template #trigger="{ open }">
            <button type="button" class="translation-icon model-trigger" :title="modelName" :aria-label="modelName" :aria-expanded="open">
              <img v-if="/deepseek/i.test(modelName)" :src="deepseekIcon" alt="" />
              <span v-else aria-hidden="true">{{ selectedModel?.display_name?.[0]?.toUpperCase() || 'M' }}</span>
            </button>
          </template>
          <div v-if="modelsLoading" class="model-empty">正在加载模型…</div>
          <button v-else-if="modelsError" type="button" class="translation-button model-empty" @click="loadModels">模型目录加载失败，重试</button>
          <div v-else-if="!filteredModels.length" class="model-empty">暂无可用模型</div>
          <template v-else>
            <div v-for="provider in filteredModels" :key="provider.provider_id" class="cherry-translation-model-group">
              <small>{{ provider.provider_display_name }}</small>
              <button
                v-for="model in provider.models"
                :key="model.spec"
                type="button"
                class="config-dropdown-item"
                @click="chooseModel(model.spec)"
              >
                <span class="config-dropdown-item-label">{{ model.display_name }}</span>
                <Check v-if="model.spec === preferences.modelSpec" :size="14" />
              </button>
            </div>
          </template>
        </ActionDropdown>
        <TranslationReasoningSelect v-model="preferences.reasoning" />
        <button
          type="button"
          class="translation-icon"
          title="翻译历史"
          aria-label="翻译历史"
          :aria-pressed="activeSide === 'history'"
          @click="toggleSide('history')"
        ><History :size="16" aria-hidden="true" /></button>
        <button
          type="button"
          class="translation-icon"
          title="翻译设置"
          aria-label="翻译设置"
          :aria-pressed="activeSide === 'settings'"
          @click="toggleSide('settings')"
        ><SlidersHorizontal :size="16" aria-hidden="true" /></button>
      </div>
    </div>

    <div class="translation-panes" :class="{ 'pdf-mode': pdfMode }" :inert="Boolean(activeSide)">
      <TranslationPdfPane
        v-if="pdfMode"
        :name="sourceFile.name"
        :source="pdfSourceUrl"
        :result="pdfResultUrl"
        :available="pdfAvailable"
        :checking="pdfChecking"
        :busy="Boolean(pending)"
        :error="error"
        :text-active="pdfTextActive"
        @close="closePdf"
      />
      <section
        v-else
        aria-label="原文"
        class="translation-pane source-pane"
        @dragenter.prevent="dragging = true"
        @dragleave.self="dragging = false"
      >
        <div class="pane-actions">
          <button
            type="button"
            class="translation-icon"
            :title="recognitionActive ? '结束听写' : '开始听写'"
            :aria-label="recognitionActive ? '结束听写' : '开始听写'"
            :aria-pressed="recognitionActive"
            :disabled="Boolean(pending) || !voice.canRecognize || (Boolean(voice.dictationOwner) && !recognitionActive)"
            @click="dictation"
          >
            <Square v-if="recognitionActive" :size="15" aria-hidden="true" />
            <Mic v-else :size="16" aria-hidden="true" />
          </button>
          <button type="button" class="translation-icon" title="复制原文" aria-label="复制原文" :disabled="!source" @click="copy(source, 'input')">
            <Check v-if="sourceCopied" :size="15" aria-hidden="true" /><Copy v-else :size="15" aria-hidden="true" />
          </button>
        </div>
        <div ref="sourceViewport" class="source-viewport" @scroll="syncScroll($event, outputViewport)">
          <textarea
            ref="sourceEditor"
            :value="source"
            aria-label="原文"
            placeholder="请输入文本..."
            spellcheck="false"
            :readonly="Boolean(pending)"
            @input="setSource($event.target.value)"
            @keydown.ctrl.enter.prevent="translate"
            @keydown.meta.enter.prevent="translate"
            @paste="onPaste"
          />
        </div>
        <input ref="filePicker" :accept="acceptedFiles" type="file" hidden @change="importFile($event.target.files); $event.target.value = ''" />
        <button
          v-if="!source"
          type="button"
          class="upload-zone"
          :disabled="Boolean(pending)"
          @click="filePicker.click()"
        >
          <span>拖入或点击上传图片/文档</span>
          <span class="upload-icons">
            <img v-for="icon in uploadIcons" :key="icon" :src="icon" alt="" aria-hidden="true" />
          </span>
        </button>
        <div v-else class="pane-footer">
          <button type="button" class="translation-button clear-source" :disabled="Boolean(pending)" @click="clear">
            <X :size="14" aria-hidden="true" /> 清空
          </button>
          <small v-if="sourceFile" :title="sourceFile.name">{{ sourceFile.name }}</small>
        </div>
        <div v-if="dragging" class="drag-layer" @dragleave="dragging = false">
          <div>拖放到此处</div>
        </div>
      </section>
      <section v-if="!pdfMode || pdfTextActive" class="translation-pane output-pane" aria-label="译文">
        <header v-if="pdfMode" class="pdf-text-header">译文</header>
        <div class="pane-actions">
          <button type="button" class="translation-icon" title="复制译文" aria-label="复制译文" :disabled="!canUseOutput" @click="copy(visibleOutput, 'output')">
            <Check v-if="targetCopied" :size="15" aria-hidden="true" /><Copy v-else :size="15" aria-hidden="true" />
          </button>
        </div>
        <div ref="outputViewport" class="output-viewport" @scroll="syncScroll($event, sourceViewport)">
          <div ref="capture" class="translation-capture">
            <div v-if="pending" class="translation-status" role="status"><LoaderCircle :size="15" /> {{ pending }}</div>
            <div v-else-if="error" class="translation-error" role="alert">{{ error }}</div>
            <div v-else-if="visibleOutput && preferences.markdown" class="translation-markdown" v-html="renderedOutput" />
            <div v-else-if="visibleOutput" class="plain-output">{{ visibleOutput }}</div>
          </div>
        </div>
        <div class="pane-footer output-footer">
          <small v-if="visibleOutput">{{ visibleOutput.length }}</small>
          <span class="spacer" />
          <button
            type="button"
            class="translation-icon"
            :title="playing ? '停止朗读' : '朗读'"
            :aria-label="playing ? '停止朗读' : '朗读'"
            :disabled="!canUseOutput || !voice.canSpeak || Boolean(voice.dictationOwner)"
            @click="readAloud"
          >
            <Square v-if="playing" :size="15" aria-hidden="true" /><Volume2 v-else :size="16" aria-hidden="true" />
          </button>
          <button
            type="button"
            class="translation-icon"
            title="复制为图片"
            aria-label="复制为图片"
            :disabled="!canUseOutput || copyingImage"
            @click="copyAsImage"
          ><Image :size="15" aria-hidden="true" /></button>
          <button
            type="button"
            class="translation-icon"
            title="保存译文到网盘"
            aria-label="保存译文到网盘"
            :disabled="!canUseOutput || notesSaving"
            @click="saveNote"
          ><NotebookPen :size="16" aria-hidden="true" /></button>
        </div>
      </section>
    </div>

    <TranslationSettings
      v-model="preferences"
      :options="allLanguages"
      :open="activeSide === 'settings'"
      @close="activeSide = ''"
    />
    <TranslationHistory
      :open="activeSide === 'history'"
      :entries="history"
      :languages="allLanguages"
      @close="activeSide = ''"
      @reuse="reuse"
      @copy="copy"
      @star="star"
      @remove="removeHistory"
      @clear="removeHistory()"
    />
  </section>
</template>

<style scoped lang="less">
.cherry-translation {
  position: relative;
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--gray-150, var(--gray-200));
  border-radius: 13px;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.translation-toolbar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  min-height: 56px;
  gap: 12px;
  padding: 12px;
  border-bottom: 1px solid var(--gray-100);
}

.translate-to-label {
  flex-shrink: 0;
  color: var(--gray-600);
  font-size: 14px;
}

.toolbar-language {
  width: 150px;
  max-width: 29%;
}

.toolbar-language :deep(.ant-select-selector) {
  padding-inline: 11px;
  border-color: var(--gray-150, var(--gray-200));
  border-radius: 9px;
  background: var(--gray-25, var(--gray-0));
}

.toolbar-actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 4px;
}

.cherry-translation :deep(.spacer) {
  flex: 1;
}

.cherry-translation :deep(.translation-icon),
.cherry-translation :deep(.translation-button) {
  display: inline-flex;
  min-width: 0;
  align-items: center;
  gap: 5px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--gray-600);
  font: inherit;
  cursor: pointer;

  &:hover:not(:disabled) {
    background: var(--gray-50);
    color: var(--gray-1000);
  }

  &:focus-visible {
    outline: 2px solid var(--main-400);
    outline-offset: 1px;
  }

  &:disabled {
    cursor: default;
    opacity: 0.5;
  }
}

.cherry-translation :deep(.translation-icon) {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  justify-content: center;
  padding: 0;
}

.cherry-translation :deep(.translation-button) {
  min-height: 30px;
  flex-shrink: 0;
  padding: 0 8px;
  font-size: 14px;
}

.cherry-translation :deep(.translate-button) {
  padding: 0 12px;
  background: #16a34a;
  color: #fff;

  &:hover:not(:disabled) {
    background: #15803d;
    color: #fff;
  }

  &:disabled {
    background: var(--gray-100);
    color: var(--gray-500);
    opacity: 1;
  }
}

.cherry-translation :deep(.stop-button),
.cherry-translation :deep(.pair-label) {
  background: var(--gray-100);
}

.model-trigger img {
  width: 20px;
  height: 20px;
}

.model-trigger span {
  display: grid;
  width: 21px;
  height: 21px;
  place-items: center;
  border-radius: 50%;
  background: var(--gray-100);
  color: var(--gray-800);
  font-size: 12px;
  font-weight: 650;
}

.model-empty {
  padding: 10px;
  color: var(--gray-600);
  font-size: 13px;
}

.cherry-translation-model-group small {
  display: block;
  padding: 3px 7px;
  color: var(--gray-600);
}

.translation-panes {
  display: grid;
  flex: 1;
  min-width: 0;
  min-height: 0;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.translation-pane {
  position: relative;
  display: flex;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
}

.output-pane {
  border-left: 1px solid var(--gray-100);
}

.pdf-text-header {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  min-height: 40px;
  padding: 5px 12px;
  border-bottom: 1px solid var(--gray-100);
  color: var(--gray-600);
  font-size: 13px;
}

.pdf-mode .pane-actions {
  top: 54px;
}

.pane-actions {
  position: absolute;
  z-index: 1;
  inset: 14px 12px auto auto;
  display: flex;
  align-items: center;
  gap: 2px;
}

.source-viewport,
.output-viewport {
  flex: 1;
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
}

.source-viewport textarea,
.translation-capture {
  min-height: 100%;
  box-sizing: border-box;
  padding: 16px 48px 16px 16px;
  color: inherit;
  font: inherit;
  font-size: 16px;
  line-height: 1.65;
}

.source-viewport textarea {
  display: block;
  width: 100%;
  max-width: 100%;
  overflow: hidden;
  resize: none;
  padding-right: 80px;
  border: 0;
  outline: none;
  background: transparent;
}

.source-viewport textarea::placeholder {
  color: var(--gray-600);
  font-weight: 400;
}

.translation-capture {
  overflow-wrap: anywhere;
}

.plain-output {
  white-space: pre-wrap;
}

.translation-markdown :deep(p:first-child) {
  margin-top: 0;
}

.translation-markdown :deep(pre) {
  overflow: auto;
  padding: 8px;
  border-radius: 6px;
  background: var(--gray-50);
}

.translation-status {
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--gray-600);

  svg {
    animation: translate-spin 0.85s linear infinite;
  }
}

.translation-error {
  color: var(--color-error, #bf3932);
  font-size: 14px;
}

.upload-zone {
  display: grid;
  justify-items: center;
  align-content: center;
  flex-shrink: 0;
  min-height: 94px;
  margin: 0 12px 16px;
  padding: 13px 8px;
  gap: 12px;
  border: 1px dashed var(--gray-100);
  border-radius: 9px;
  background: transparent;
  color: var(--gray-600);
  font: inherit;
  font-size: 14px;
  text-align: center;
  cursor: pointer;

  &:hover:not(:disabled),
  &:focus-visible {
    border-color: var(--gray-300);
    background: var(--gray-50);
  }

  &:disabled {
    opacity: 0.6;
  }
}

.upload-icons {
  display: flex;
  width: min(100%, 285px);
  justify-content: space-between;
  gap: 4px;

  img {
    width: 28px;
    height: 28px;
    min-width: 0;
  }
}

.pane-footer {
  display: flex;
  min-height: 48px;
  flex-shrink: 0;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  color: var(--gray-600);
}

.pane-footer small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.drag-layer {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: grid;
  padding: 12px;
  background: var(--gray-0);
  color: var(--gray-600);

  div {
    display: grid;
    place-items: center;
    border: 1px dashed var(--gray-200);
    border-radius: 8px;
    pointer-events: none;
  }
}

@keyframes translate-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1023px) {
  .pdf-mode {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: repeat(2, minmax(0, 1fr));
  }

  .pdf-mode .output-pane {
    border-top: 1px solid var(--gray-100);
    border-left: 0;
  }
}

@media (max-width: 950px) {
  .translation-toolbar {
    gap: 6px;
  }

  .toolbar-language {
    max-width: 24%;
  }

  .toolbar-actions {
    gap: 1px;
  }

  .source-viewport textarea {
    padding-right: 67px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .translation-status svg {
    animation-duration: 2s;
  }
}
</style>
