<script setup>
import {
  ArrowUp, BookOpen, ChevronDown, ChevronUp, Info, Mic, Paperclip, Plus, Scan, Shuffle,
  SlidersHorizontal, Square, Trash2, X
} from '@lucide/vue'
import { message } from 'ant-design-vue'
import { computed, nextTick, onBeforeUnmount, onDeactivated, onMounted, ref, shallowRef, watch } from 'vue'

import { resolveApiErrorMessage } from '@/apis/base'
import { paintingsApi } from '@/apis/paintings_api'
import { modelProviderApi } from '@/apis/system_api'
import ModelSelectorComponent from '@/modules/agents/ui/ModelSelectorComponent.vue'
import { useUserStore } from '@/modules/identity'
import { useVoiceStore } from '@/modules/voice'
import { deletePainting, readPaintings, savePainting } from '../model/history'
import { loadPaintingTemplates } from '../model/templates'
import PaintingArtboard from './PaintingArtboard.vue'
import PaintingPromptLibrary from './PaintingPromptLibrary.vue'
import PaintingTemplateShowcase from './PaintingTemplateShowcase.vue'

const props = defineProps({ owner: { type: String, required: true } })
const owner = props.owner
const userStore = useUserStore()
const voice = useVoiceStore()
const voiceOwner = Symbol('painting')
const lifecycle = new AbortController()
const fileControl = ref()
const promptInput = ref()
const library = ref(false)
const expandedEditor = ref(false)
const paramsOpen = ref(false)
const toolsOpen = ref(false)
const templates = ref([])
const history = shallowRef([])
const models = shallowRef([])
const modelSpec = ref('')
const templateId = ref('')
const prompt = ref('')
const size = ref('')
const count = ref(1)
const seed = ref(null)
const negativePrompt = ref('')
const guidanceScale = ref(null)
const referenceImages = shallowRef([])
const selectedPainting = shallowRef(null)
const pending = ref(false)
const error = ref('')
const historyError = ref('')
const templateError = ref('')
const dockCollapsed = ref(false)
const showArtboard = ref(false)
const initialHistoryLoaded = ref(false)
const pendingPainting = shallowRef(null)
let activeRequest = null
const objectUrls = new Map()

const allowedTypes = ['image/png', 'image/jpeg', 'image/webp']
const imageExtensions = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp' }
const proportions = ['自动', '9:16', '2:3', '3:4', '1:1', '4:3', '3:2', '16:9']
const model = computed(() => models.value.find((item) => item.spec === modelSpec.value))
const isQwen = computed(() => /Qwen[ /:-]/i.test(model.value?.model_id || model.value?.display_name || ''))
const qwenIcon = `${import.meta.env.BASE_URL}cherry-studio/qwen.svg`
const sizeOptions = computed(() => model.value?.image_sizes || [])
const ratioSizes = computed(() => model.value?.image_aspect_ratios || {})
const resolutionOptions = computed(() => sizeOptions.value.filter((option) => option.endsWith('K')))
const validSizes = computed(() => [...sizeOptions.value, ...Object.values(ratioSizes.value)])
const automaticSize = computed(() => ratioSizes.value.auto
  ? size.value === ratioSizes.value.auto
  : resolutionOptions.value.includes(size.value))
const selectedRatio = computed(() => Object.keys(ratioSizes.value).find((ratio) => (
  ratio !== 'auto' && ratioSizes.value[ratio] === size.value
)))
const sizeLabel = computed(() => {
  if (automaticSize.value) return `自动 ${size.value}`
  return selectedRatio.value || size.value.replace('x', '×')
})
const maxImages = computed(() => model.value?.max_images || 1)
const parameterOptions = computed(() => model.value?.image_parameters || [])
const maxReferences = computed(() => model.value?.max_reference_images || 0)
const canUploadReference = computed(() => referenceImages.value.length < maxReferences.value)
const isDictating = computed(() => voice.dictationOwner === voiceOwner)
const ownsWorkspace = computed(() => userStore.isLoggedIn && userStore.uid === owner)
const draftSettings = computed(() => ({
  modelSpec: modelSpec.value,
  prompt: prompt.value,
  size: size.value,
  count: count.value,
  seed: parameterOptions.value.includes('seed') ? seed.value : null,
  negativePrompt: parameterOptions.value.includes('negative_prompt') ? negativePrompt.value : '',
  guidanceScale: parameterOptions.value.includes('guidance_scale') ? guidanceScale.value : null
}))
const canSend = computed(() => Boolean(
  model.value && validSizes.value.includes(size.value) && Number.isInteger(count.value) &&
  count.value >= 1 && count.value <= maxImages.value &&
  initialHistoryLoaded.value && !pending.value && prompt.value.trim() &&
  (model.value.accepts_prompt_only !== false || referenceImages.value.length > 0)
))
const selectedImages = computed(() => selectedPainting.value?.images.map((blob, index) => ({
  url: imageUrl(blob),
  filename: `yuxi-painting-${index + 1}.${imageExtensions[blob.type]}`
})) || [])

function imageUrl(blob) {
  if (!objectUrls.has(blob)) objectUrls.set(blob, URL.createObjectURL(blob))
  return objectUrls.get(blob)
}

function cleanupImages(blobs) {
  for (const blob of blobs) {
    if (!objectUrls.has(blob)) continue
    URL.revokeObjectURL(objectUrls.get(blob))
    objectUrls.delete(blob)
  }
}

async function loadModels() {
  try {
    const response = await modelProviderApi.getV2Models('image')
    if (lifecycle.signal.aborted) return
    if (!response.success) throw new Error('图片模型加载失败')
    const catalog = Object.values(response.data || {}).flatMap((provider) => provider.models || [])
    // 首次请求可能晚于下拉列表的刷新，保留用户刚从列表选中的记录。
    const selected = model.value
    models.value = selected ? [selected, ...catalog.filter((item) => item.spec !== selected.spec)] : catalog
    if (!selected) {
      const available = catalog.filter((item) => item.image_sizes?.length)
      const defaultModel = available.find((item) => /:Qwen\/Qwen-Image$/i.test(item.spec)) || available[0] || catalog[0]
      if (defaultModel) selectModel(defaultModel.spec)
    }
  } catch (reason) {
    if (!lifecycle.signal.aborted && !model.value) error.value = resolveApiErrorMessage(reason, '图片模型加载失败')
  }
}

onMounted(() => {
  void loadModels()
  readPaintings(props.owner).then((records) => {
    if (!lifecycle.signal.aborted) history.value = records
  }).catch(() => {
    if (!lifecycle.signal.aborted) historyError.value = '绘画历史未能读取'
  }).finally(() => {
    if (!lifecycle.signal.aborted) initialHistoryLoaded.value = true
  })
  loadPaintingTemplates(lifecycle.signal).then((records) => {
    if (!lifecycle.signal.aborted) templates.value = records
  }).catch(() => {
    if (!lifecycle.signal.aborted) templateError.value = '绘画模板未能读取'
  })
})

function selectModel(spec, selected) {
  if (selected) {
    models.value = [selected, ...models.value.filter((item) => item.spec !== spec)]
    error.value = ''
  }
  modelSpec.value = spec
  size.value = sizeOptions.value[0] || ''
  count.value = 1
  seed.value = null
  negativePrompt.value = ''
  guidanceScale.value = null
  cleanupImages(referenceImages.value.slice(maxReferences.value))
  referenceImages.value = referenceImages.value.slice(0, maxReferences.value)
}

function selectRatio(ratio) {
  const next = ratioSizes.value[ratio] || (ratio === '自动' ? resolutionOptions.value[0] : '')
  if (next) size.value = next
}

function applyTemplate(template) {
  templateId.value = template.id
  prompt.value = template.prompt
}

async function applyPrompt(text) {
  const control = promptInput.value
  const start = control?.selectionStart ?? prompt.value.length
  const end = control?.selectionEnd ?? start
  prompt.value = (prompt.value.slice(0, start) + text + prompt.value.slice(end)).slice(0, 4000)
  await nextTick()
  if (control) {
    control.focus()
    control.setSelectionRange(start + text.length, start + text.length)
  }
}

function selectFiles(files) {
  if (pending.value) return
  const slots = maxReferences.value - referenceImages.value.length
  if (slots <= 0) return
  const matches = files.filter((file) => allowedTypes.includes(file.type) && file.size <= 3 * 1024 * 1024)
  if (matches.length !== files.length) message.warning('参考图支持 PNG、JPEG 和 WebP，每张不超过 3 MB')
  referenceImages.value = [...referenceImages.value, ...matches.slice(0, slots)]
}

function removeReference(index) {
  cleanupImages([referenceImages.value[index]])
  referenceImages.value = referenceImages.value.filter((_file, fileIndex) => fileIndex !== index)
}

function uploadReferences() {
  if (!canUploadReference.value || pending.value) return
  toolsOpen.value = false
  fileControl.value?.click()
}

function openPromptLibrary() {
  toolsOpen.value = false
  library.value = true
}

function toggleDictation() {
  if (isDictating.value) return voice.stopDictation(voiceOwner)
  if (!voice.preferences.dictationEnabled) {
    message.info('请先在「设置 → 语音」启用听写')
    return
  }
  voice.startDictation(voiceOwner, {
    onText: (text) => void applyPrompt(text),
    onError: (reason) => message.warning(reason)
  })
}

function saveActivePainting() {
  const current = selectedPainting.value
  if (!current || pending.value) return
  const settings = draftSettings.value
  const currentReferences = current.references || []
  const nextReferences = referenceImages.value
  if (
    Object.keys(settings).every((key) => settings[key] === current[key]) &&
    currentReferences.length === nextReferences.length &&
    currentReferences.every((file, index) => file === nextReferences[index])
  ) return
  const next = {
    ...current,
    ...settings,
    references: nextReferences
  }
  history.value = history.value.map((painting) => painting.id === next.id ? next : painting)
  selectedPainting.value = next
  void savePainting(props.owner, next).catch(() => {
    if (!lifecycle.signal.aborted) historyError.value = '绘画历史未能保存'
  })
}

function setPainting(painting = null) {
  if (painting && selectedPainting.value?.id === painting.id) return
  saveActivePainting()
  voice.release(voiceOwner)
  cleanupImages(referenceImages.value)
  selectedPainting.value = painting
  templateId.value = ''
  prompt.value = painting?.prompt || ''
  if (painting && painting.modelSpec !== modelSpec.value && models.value.some((item) => item.spec === painting.modelSpec)) {
    selectModel(painting.modelSpec)
  }
  if (painting && validSizes.value.includes(painting.size)) size.value = painting.size
  if (painting) count.value = Math.min(painting.count || 1, maxImages.value)
  seed.value = painting?.seed ?? null
  negativePrompt.value = painting?.negativePrompt ?? ''
  guidanceScale.value = painting?.guidanceScale ?? null
  referenceImages.value = (painting?.references || []).slice(0, maxReferences.value)
  dockCollapsed.value = Boolean(painting)
  showArtboard.value = Boolean(painting)
  error.value = ''
}

async function removePainting(painting) {
  try {
    await deletePainting(props.owner, painting.id)
    if (lifecycle.signal.aborted) return
    const index = history.value.indexOf(painting)
    history.value = history.value.filter((item) => item.id !== painting.id)
    if (selectedPainting.value?.id === painting.id) {
      selectedPainting.value = null
      setPainting(history.value[index] || history.value[index - 1])
    }
    cleanupImages(painting.images)
  } catch {
    historyError.value = '图片记录未能删除'
  }
}

function asDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

async function generate() {
  if (!canSend.value) return
  saveActivePainting()
  voice.release(voiceOwner)
  const controller = new AbortController()
  const requested = draftSettings.value
  activeRequest = controller
  pending.value = true
  pendingPainting.value = { ...requested, id: crypto.randomUUID() }
  error.value = ''
  showArtboard.value = true
  dockCollapsed.value = false
  paramsOpen.value = false
  toolsOpen.value = false
  try {
    const inputFiles = referenceImages.value
    const references = await Promise.all(inputFiles.map(asDataUrl))
    if (activeRequest !== controller) return
    const images = await paintingsApi.render({ ...requested, referenceImages: references }, {
      signal: controller.signal
    })
    if (activeRequest !== controller || !ownsWorkspace.value) return
    const painting = { ...pendingPainting.value, updatedAt: Date.now(), references: inputFiles, images }
    pendingPainting.value = null
    history.value = [painting, ...history.value]
    selectedPainting.value = painting
    dockCollapsed.value = true
    try {
      await savePainting(props.owner, painting)
    } catch {
      if (!lifecycle.signal.aborted) historyError.value = '图片已生成，绘画历史未能保存'
    }
  } catch (reason) {
    if (!controller.signal.aborted) error.value = resolveApiErrorMessage(reason, '图片生成失败，请重试')
  } finally {
    if (activeRequest === controller) {
      pending.value = false
      pendingPainting.value = null
      activeRequest = null
    }
  }
}

function cancelGeneration() {
  if (!activeRequest) return
  activeRequest.abort()
  activeRequest = null
  pending.value = false
  pendingPainting.value = null
  showArtboard.value = Boolean(selectedPainting.value)
}

onBeforeUnmount(() => {
  saveActivePainting()
  lifecycle.abort()
  cancelGeneration()
  voice.release(voiceOwner)
  cleanupImages(objectUrls.keys())
})

onDeactivated(() => {
  voice.release(voiceOwner)
  library.value = false
  expandedEditor.value = false
  paramsOpen.value = false
  toolsOpen.value = false
})

watch(ownsWorkspace, (owned) => {
  if (!owned) cancelGeneration()
}, { flush: 'sync' })
</script>

<template>
  <div class="paintings-page" data-ui="paintings.view">
    <nav class="painting-history" aria-label="绘画历史">
      <a-tooltip title="新建图片" placement="right" :mouse-enter-delay="0.45">
        <button class="history-add" type="button" aria-label="新建图片" :disabled="pending" @click="setPainting()">
          <Plus :size="18" />
        </button>
      </a-tooltip>
      <div v-if="pendingPainting" class="history-pending" role="status" aria-label="正在生成图片"></div>
      <div v-for="painting in history" :key="painting.id" class="history-entry">
        <button
          type="button"
          class="history-thumbnail"
          :aria-current="selectedPainting?.id === painting.id ? 'true' : undefined"
          :aria-label="`选择图片 ${painting.prompt.slice(0, 45)}`"
          :disabled="pending"
          @click="setPainting(painting)"
        ><img :src="imageUrl(painting.images[0])" alt="" /></button>
        <a-popconfirm title="删除图片？" ok-text="删除" cancel-text="取消" @confirm="removePainting(painting)">
          <button type="button" class="history-delete" aria-label="删除图片" :disabled="pending">
            <Trash2 :size="12" />
          </button>
        </a-popconfirm>
      </div>
    </nav>

    <div class="painting-main" data-composer-stage>
      <section v-if="!showArtboard" class="painting-showcase" data-testid="painting-template-stage">
        <h1>给你的下一幅杰作，留一个位置。</h1>
        <PaintingTemplateShowcase
          :templates="templates"
          :selected="templateId"
          @select="applyTemplate"
        />
        <p>{{ templateError || '选择一个模板作为起点，再在下方把提示词改成你的表达。' }}</p>
      </section>
      <PaintingArtboard v-else :images="pending ? [] : selectedImages" :pending="pending" :error="error" />
      <p v-if="historyError" class="history-error" role="status">{{ historyError }}</p>
      <p v-if="error && !showArtboard" class="history-error" role="alert">{{ error }}</p>

      <div v-if="dockCollapsed" class="collapsed-dock">
        <button type="button" class="dock-toggle" @click="dockCollapsed = false"><ChevronUp :size="15" />展开提示词</button>
      </div>
      <div v-else class="painting-dock" data-composer-dock-surface>
        <button v-if="selectedImages.length && !pending" type="button" class="dock-toggle" @click="dockCollapsed = true">
          <ChevronDown :size="15" />收起提示词
        </button>
        <form
          class="painting-composer"
          aria-label="图片生成"
          @submit.prevent="generate"
          @dragover.prevent
          @drop.prevent="selectFiles(Array.from($event.dataTransfer.files))"
        >
          <div v-if="referenceImages.length" class="reference-images">
            <div v-for="(reference, index) in referenceImages" :key="reference" class="reference-image">
              <img :src="imageUrl(reference)" :alt="reference.name" />
              <button type="button" aria-label="移除参考图" :disabled="pending" @click="removeReference(index)"><X :size="12" /></button>
            </div>
          </div>
          <div class="painting-prompt">
            <textarea
              ref="promptInput"
              v-model="prompt"
              aria-label="提示词"
              placeholder="描述你想创建的图片，例如：一个宁静的湖泊，夕阳西下，远处是群山"
              maxlength="4000"
              :disabled="pending"
              @keydown.ctrl.enter.prevent="generate"
            />
            <button type="button" class="expand-prompt" aria-label="展开编辑框" title="展开编辑框" @click="expandedEditor = true"></button>
          </div>

          <div class="composer-toolbar">
            <div class="composer-controls">
              <div class="painting-model" :class="{ 'has-icon': isQwen }">
                <img v-if="isQwen" :src="qwenIcon" alt="" />
                <ModelSelectorComponent
                  model-type="image"
                  size="nano"
                  display-name="mini"
                  upward
                  :model_spec="modelSpec"
                  placeholder="请选择生图模型"
                  :disabled="pending"
                  @select-model="selectModel"
                />
              </div>
              <a-popover v-model:open="paramsOpen" trigger="click" placement="topLeft">
                <template #content>
                  <div class="painting-params">
                    <div class="painting-count">
                      <label for="painting-count" class="param-title">
                        生成数量
                        <Info :size="16" :aria-label="`当前模型最多生成 ${maxImages} 张`">
                          <title>当前模型最多生成 {{ maxImages }} 张</title>
                        </Info>
                      </label>
                      <a-input-number
                        id="painting-count"
                        v-model:value="count"
                        aria-label="生成数量"
                        :min="1"
                        :max="maxImages"
                        :step="1"
                        :precision="0"
                        controls
                        class="painting-count-input"
                        :disabled="!model"
                      />
                    </div>
                    <span class="param-title">比例</span>
                    <div v-if="validSizes.length" class="ratio-options" role="group" aria-label="图片比例">
                      <button
                        v-for="ratio in proportions"
                        :key="ratio"
                        type="button"
                        :aria-pressed="ratio === '自动' ? automaticSize : size === ratioSizes[ratio]"
                        :disabled="ratio === '自动'
                          ? !ratioSizes.auto && !resolutionOptions.length
                          : !ratioSizes[ratio]"
                        :title="ratio === '自动' ? undefined : ratioSizes[ratio]?.replace('x', '×')"
                        @click="selectRatio(ratio)"
                      >
                        <Scan v-if="ratio === '自动'" :size="18" aria-hidden="true" />
                        <span
                          v-else
                          class="ratio-outline"
                          :style="{ aspectRatio: ratio.replace(':', ' / ') }"
                          aria-hidden="true"
                        ></span>
                        <span>{{ ratio }}</span>
                      </button>
                    </div>
                    <span v-else>{{ modelSpec ? '当前模型未提供可用尺寸' : '请先选择生图模型' }}</span>
                    <label v-if="automaticSize && resolutionOptions.length > 1" class="painting-resolution">
                      <span>分辨率</span>
                      <a-select
                        v-model:value="size"
                        aria-label="分辨率"
                        :options="resolutionOptions.map((option) => ({ label: option, value: option }))"
                        placement="topLeft"
                        class="painting-resolution-select"
                      />
                    </label>
                    <template v-if="parameterOptions.includes('seed')">
                      <label for="painting-seed">随机种子</label>
                      <div class="seed-input">
                        <a-input-number id="painting-seed" v-model:value="seed" :min="0" :max="2147483647" :precision="0" />
                        <a-button aria-label="生成随机种子" @click="seed = Math.floor(Math.random() * 1000000)"><Shuffle :size="15" /></a-button>
                      </div>
                    </template>
                    <template v-if="parameterOptions.includes('negative_prompt')">
                      <label for="painting-negative">负面提示词</label>
                      <a-textarea id="painting-negative" v-model:value="negativePrompt" :rows="2" :maxlength="1000" />
                    </template>
                    <template v-if="parameterOptions.includes('guidance_scale')">
                      <label for="painting-guidance">提示词相关性</label>
                      <a-input-number id="painting-guidance" v-model:value="guidanceScale" :min="1" :max="20" :step="0.5" />
                    </template>
                  </div>
                </template>
                <button
                  type="button"
                  class="composer-control parameters"
                  aria-label="绘画参数"
                  :aria-expanded="paramsOpen"
                  :title="sizeLabel ? `图片尺寸 ${sizeLabel}` : '绘画参数'"
                  :disabled="pending"
                >
                  <SlidersHorizontal :size="16" /><span v-if="sizeLabel">{{ count }} · {{ sizeLabel }}</span>
                </button>
              </a-popover>
              <button type="button" class="composer-control prompts" aria-label="提示词" :disabled="pending" @click="openPromptLibrary">
                <BookOpen :size="17" /><span>提示词</span>
              </button>
              <a-popover v-model:open="toolsOpen" trigger="click" placement="topLeft">
                <template #content>
                  <div class="tool-popover" role="group" aria-label="输入快捷操作">
                    <button type="button" :disabled="!canUploadReference" @click="uploadReferences">
                      <Paperclip :size="15" />上传参考图
                    </button>
                    <small v-if="!maxReferences">{{ model ? '当前模型不支持参考图' : '请先选择生图模型' }}</small>
                    <small v-else-if="!canUploadReference">已达到参考图数量上限</small>
                    <button type="button" @click="openPromptLibrary">
                      <BookOpen :size="15" />提示词
                    </button>
                  </div>
                </template>
                <button
                  type="button"
                  class="composer-control tool-add"
                  aria-label="输入快捷面板"
                  title="输入快捷面板"
                  :aria-expanded="toolsOpen"
                  :disabled="pending"
                ><Plus :size="19" /></button>
              </a-popover>
              <input
                ref="fileControl"
                hidden
                type="file"
                accept="image/png,image/jpeg,image/webp"
                multiple
                @change="selectFiles(Array.from($event.target.files)); $event.target.value = ''"
              />
            </div>
            <div class="composer-submit">
              <button
                type="button"
                class="composer-control mic"
                :aria-label="isDictating ? '结束听写' : '开始听写'"
                :title="isDictating ? '结束听写' : '开始听写'"
                :disabled="pending || !voice.canRecognize || (voice.dictationOwner && !isDictating)"
                @click="toggleDictation"
              ><Square v-if="isDictating" :size="16" /><Mic v-else :size="18" /></button>
              <button v-if="pending" type="button" class="send-button" aria-label="停止生成" @click="cancelGeneration"><Square :size="15" /></button>
              <button v-else type="submit" class="send-button" aria-label="生成图片" title="生成图片" :disabled="!canSend"><ArrowUp :size="17" /></button>
            </div>
          </div>
        </form>
      </div>
    </div>
    <a-modal v-model:open="expandedEditor" title="提示词" :footer="null" width="700px">
      <a-textarea v-model:value="prompt" aria-label="提示词" :rows="12" :maxlength="4000" />
    </a-modal>
    <PaintingPromptLibrary v-model:open="library" :owner="owner" @apply="applyPrompt" />
  </div>
</template>

<style scoped lang="less">
.paintings-page {
  display: flex;
  overflow: hidden;
  height: calc(100% - 8px);
  min-height: 0;
  margin: 4px;
  padding: 7px 7px 4px;
  border: 1px solid var(--gray-150);
  border-radius: 13px;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.painting-history {
  display: flex;
  flex: 0 0 68px;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
  min-height: 0;
  overflow-y: auto;
  padding: 8px;
  border-right: 1px solid var(--gray-100);
  scrollbar-width: none;
}

.history-add,
.history-thumbnail {
  position: relative;
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  padding: 0;
  border: 1px dashed var(--gray-150);
  border-radius: 12px;
  background: var(--gray-200);
  color: var(--gray-1000);
  cursor: pointer;
  &:hover { background: var(--gray-150); }
}

.history-add { position: sticky; top: 0; z-index: 2; margin-bottom: 3px; }
.history-entry { position: relative; }
.history-pending {
  flex: 0 0 44px;
  width: 44px;
  border-radius: 12px;
  background: linear-gradient(100deg, var(--gray-50), var(--gray-150), var(--gray-50));
}
.history-thumbnail {
  overflow: hidden;
  border: 1px solid var(--gray-100);
  &[aria-current] { outline: 1px solid var(--gray-400); outline-offset: 2px; }
  img { width: 100%; height: 100%; object-fit: cover; }
}

.history-delete {
  position: absolute;
  top: -3px;
  right: -3px;
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 1px solid var(--gray-150);
  border-radius: 50%;
  background: var(--gray-0);
  color: var(--color-error-700);
  opacity: 0;
  cursor: pointer;
}
.history-entry:hover .history-delete, .history-delete:focus-visible { opacity: 1; }

.painting-main {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  container-type: size;
}

.painting-showcase {
  display: flex;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: space-between;
  gap: 5px;
  min-height: 200px;
  padding: clamp(16px, 8cqh, 50px) 12px clamp(12px, 14cqh, 100px);
  overflow: hidden;
  text-align: center;

  h1 {
    margin: 0;
    font-size: clamp(20px, 4cqw, 28px);
    line-height: 1.12;
    font-weight: 750;
  }
  p { margin: 0; color: var(--gray-600); font-size: 13px; line-height: 1.5; }
}

.painting-dock,
.collapsed-dock {
  display: flex;
  flex-shrink: 0;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 4px 14px clamp(10px, 4cqh, 23px);
}

.collapsed-dock { padding-bottom: 8px; }
.dock-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border: 1px solid var(--gray-150);
  border-radius: 99px;
  background: var(--gray-0);
  color: var(--gray-700);
  cursor: pointer;
}

.painting-composer {
  display: grid;
  gap: 4px;
  width: min(95%, 1024px);
  padding: 8px;
  border: 1px solid var(--gray-150);
  border-radius: 23px;
  background-color: var(--gray-0);
  box-shadow: 0 1px 4px #0002;
}

.painting-prompt {
  position: relative;
  textarea {
    display: block;
    box-sizing: border-box;
    width: 100%;
    height: 48px;
    min-height: 48px;
    max-height: 32vh;
    padding: 6px 22px 6px 8px;
    resize: none;
    border: 0;
    outline: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    font-size: 15px;
    &::placeholder { color: var(--gray-400); }
  }
}

.expand-prompt {
  position: absolute;
  top: -3px;
  right: -3px;
  width: 13px;
  height: 13px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 0;
  border-top: 1.5px solid currentColor;
  border-right: 1.5px solid currentColor;
  border-radius: 0 100% 0 0;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
  opacity: 0.65;
}

.composer-toolbar,
.composer-controls,
.composer-submit {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.composer-toolbar { justify-content: space-between; }
.composer-controls { flex: 1; }
.composer-submit { flex-shrink: 0; }

.painting-model {
  position: relative;
  min-width: 75px;
  max-width: min(200px, 32cqw);
  padding: 0 3px;
  border: 1px solid var(--gray-150);
  border-radius: 99px;
  background: var(--gray-100);
  :deep(.action-trigger-shell) { color: var(--gray-1000); }
  img {
    position: absolute;
    top: 50%;
    left: 7px;
    width: 18px;
    height: 18px;
    padding: 3px;
    transform: translateY(-50%);
    border-radius: 50%;
    background: white;
    pointer-events: none;
  }
  :deep(.config-dropdown-trigger) {
    min-width: 0;
    max-width: 100%;
    height: 26px;
    padding: 0 6px;
    font-size: 13px;
  }
  &.has-icon :deep(.config-dropdown-trigger) { padding-left: 25px; }
}

.composer-control,
.send-button,
.reference-image button {
  display: inline-flex;
  justify-content: center;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  height: 28px;
  padding: 0 6px;
  white-space: nowrap;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  &:hover { background: var(--gray-100); }
  &:disabled { cursor: not-allowed; }
}
.parameters, .tool-add, .mic { color: var(--gray-500); }
.send-button {
  width: 28px;
  padding: 0;
  border-radius: 50%;
  background: var(--gray-1000);
  color: var(--gray-0);
  &:hover { background: var(--gray-700); }
  &:disabled { background: var(--gray-400); color: var(--gray-0); }
}

.reference-images { display: flex; gap: 6px; padding: 4px; }
.reference-image {
  position: relative;
  width: 48px;
  height: 48px;
  img { width: 100%; height: 100%; object-fit: cover; border-radius: 6px; }
  button { position: absolute; top: 2px; right: 2px; height: 18px; padding: 0 3px; background: var(--gray-0); }
}

.history-error {
  max-width: 80%;
  margin: 0 auto 6px;
  color: var(--color-error-700);
  text-align: center;
}
.painting-params {
  display: grid;
  gap: 12px;
  width: min(500px, calc(100vw - 40px));
  max-height: min(62vh, 440px);
  overflow-y: auto;
}
.param-title { font-size: 13px; font-weight: 500; color: var(--gray-700); }
.painting-count { display: grid; gap: 9px; justify-items: start; margin-bottom: 8px; }
.painting-count label { display: flex; align-items: center; gap: 7px; }
.painting-count-input {
  box-sizing: border-box;
  width: 158px;
  height: 72px;
  border: 1px solid var(--gray-200);
  border-radius: 16px;
  background: var(--gray-0);
  color: var(--gray-1000);
  font-size: 25px;
  &:disabled { color: var(--gray-700); background: var(--gray-50); }
  :deep(.ant-input-number-input) { height: 68px; padding: 0 17px; font-size: 25px; }
  :deep(.ant-input-number-handler-wrap) { opacity: 1; width: 28px; border-radius: 0 16px 16px 0; }
}
.painting-resolution { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.painting-resolution-select { width: 112px; }
.ratio-options {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.ratio-options button {
  display: grid;
  grid-template-rows: 20px auto;
  place-content: center;
  justify-items: center;
  gap: 7px;
  min-width: 0;
  min-height: 82px;
  padding: 8px;
  border: 1px solid transparent;
  border-radius: 16px;
  background: var(--gray-50);
  color: var(--gray-600);
  font: inherit;
  line-height: 1.15;
  cursor: pointer;
  &:hover:not(:disabled) { background: var(--gray-100); }
  &[aria-pressed='true'] {
    background: var(--gray-150);
    border-color: var(--gray-500);
    color: var(--gray-1000);
  }
  &:focus-visible {
    border-color: var(--color-primary-500);
    outline: 2px solid color-mix(in srgb, var(--color-primary-500) 25%, transparent);
  }
  &:disabled { opacity: 0.42; cursor: default; }
}
.ratio-outline {
  display: block;
  align-self: center;
  width: auto;
  height: 17px;
  max-width: 28px;
  box-sizing: border-box;
  border: 2px solid currentColor;
  border-radius: 4px;
}
.seed-input { display: flex; flex-wrap: wrap; gap: 5px; }
.tool-popover button {
  padding: 5px 8px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-0);
  color: var(--gray-1000);
  cursor: pointer;
  &[aria-pressed='true'] { background: var(--gray-100); border-color: var(--gray-400); }
}
.tool-popover {
  display: grid;
  gap: 6px;
  max-width: 185px;
  button { display: flex; align-items: center; gap: 6px; }
  button:disabled, small { color: var(--gray-500); cursor: default; }
}

@container (max-width: 500px) {
  .painting-composer { width: 100%; }
  .painting-model { max-width: 40cqw; }
  .prompts span { display: none; }
}
</style>
