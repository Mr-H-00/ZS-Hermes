<template>
  <a-modal
    :open="open"
    class="database-create-flow-modal"
    :width="840"
    :closable="!creating"
    :mask-closable="!creating"
    :keyboard="!creating"
    :footer="null"
    destroy-on-close
    @cancel="handleCancel"
  >
    <div class="create-flow-shell">
      <header class="create-flow-header">
        <div class="create-flow-icon"><DatabaseZap :size="19" /></div>
        <div>
          <h2>新建知识库</h2>
          <p>依次选择知识来源、填写配置并确认访问范围。</p>
        </div>
      </header>

      <ol class="create-flow-steps" aria-label="创建进度">
        <li
          v-for="(label, index) in stepLabels"
          :key="label"
          :class="{ active: currentStep === index, completed: currentStep > index }"
          :aria-current="currentStep === index ? 'step' : undefined"
        >
          <span class="step-dot">{{ currentStep > index ? '✓' : index + 1 }}</span>
          <span>{{ label }}</span>
        </li>
      </ol>

      <main class="create-flow-body">
        <section v-if="currentStep === 0" class="flow-section">
          <div class="section-heading">
            <strong>选择知识库类型</strong>
            <span>类型决定数据来源和后续可配置能力。</span>
          </div>
          <div class="type-options" role="radiogroup" aria-label="知识库类型">
            <button
              v-for="(typeInfo, typeKey) in supportedKbTypes"
              :key="typeKey"
              type="button"
              class="type-option"
              :class="{ selected: form.kb_type === typeKey }"
              role="radio"
              :aria-checked="form.kb_type === typeKey"
              @click="selectType(typeKey)"
            >
              <component :is="getKbTypeIcon(typeKey)" :size="22" class="type-icon" />
              <span class="type-copy">
                <strong>{{ typeInfo.name || getKbTypeLabel(typeKey) }}</strong>
                <small>{{ typeInfo.description || '连接并检索该类型的知识数据。' }}</small>
              </span>
              <span class="type-badge">
                {{ typeInfo.supports_documents === false ? '只读连接' : '支持文档' }}
              </span>
            </button>
          </div>
        </section>

        <section v-else-if="currentStep === 1" class="flow-section">
          <div class="section-heading">
            <strong>配置 {{ selectedTypeLabel }}</strong>
            <span>填写名称和当前类型需要的连接或索引参数。</span>
          </div>
          <div class="form-section">
            <label for="database-create-name">知识库名称 <b>*</b></label>
            <a-input id="database-create-name" v-model:value="form.name" placeholder="例如：产品资料库" />
          </div>
          <div v-if="selectedTypeInfo?.requires_embedding_model" class="form-grid">
            <div class="form-section">
              <label>嵌入模型 <b>*</b></label>
              <EmbeddingModelSelector
                v-model:value="form.embedding_model_spec"
                class="full-width"
                placeholder="请选择嵌入模型"
              />
            </div>
            <div class="form-section">
              <label>分块策略</label>
              <a-select
                v-model:value="form.chunk_preset_id"
                :options="chunkPresetOptions"
                :loading="chunkPresetLoading"
                class="full-width"
              />
              <small>{{ selectedPresetDescription }}</small>
            </div>
          </div>
          <div v-if="createParamOptions.length" class="form-grid">
            <div v-for="field in createParamOptions" :key="field.key" class="form-section">
              <label :for="`database-param-${field.key}`">
                {{ field.label || field.key }} <b v-if="field.required">*</b>
              </label>
              <a-input-password
                v-if="field.type === 'password'"
                :id="`database-param-${field.key}`"
                v-model:value="form.additional_params[field.key]"
                :placeholder="field.placeholder"
              />
              <a-input-number
                v-else-if="field.type === 'number'"
                :id="`database-param-${field.key}`"
                v-model:value="form.additional_params[field.key]"
                :min="field.min"
                :max="field.max"
                :step="field.step"
                class="full-width"
              />
              <a-switch
                v-else-if="field.type === 'boolean'"
                v-model:checked="form.additional_params[field.key]"
              />
              <a-select
                v-else-if="field.type === 'select'"
                :id="`database-param-${field.key}`"
                v-model:value="form.additional_params[field.key]"
                :options="field.options || []"
                class="full-width"
              />
              <a-input
                v-else
                :id="`database-param-${field.key}`"
                v-model:value="form.additional_params[field.key]"
                :placeholder="field.placeholder"
              />
              <small v-if="field.description">{{ field.description }}</small>
            </div>
          </div>
          <div class="form-section">
            <label>知识库描述</label>
            <small>描述会帮助智能体判断何时使用这个知识库。</small>
            <AiTextarea
              v-model="form.description"
              :name="form.name"
              placeholder="说明包含的内容、适用任务和使用限制"
              :auto-size="{ minRows: 3, maxRows: 8 }"
            />
          </div>
        </section>

        <section v-else class="flow-section">
          <div class="section-heading">
            <strong>设置访问范围并创建</strong>
            <span>确认基础信息后，配置哪些用户可以读取该知识库。</span>
          </div>
          <div class="review-card">
            <div><span>名称</span><strong>{{ form.name }}</strong></div>
            <div><span>类型</span><strong>{{ selectedTypeLabel }}</strong></div>
            <div v-if="selectedTypeInfo?.requires_embedding_model">
              <span>嵌入模型</span><strong>{{ form.embedding_model_spec }}</strong>
            </div>
            <div v-if="createParamOptions.length">
              <span>连接配置</span><strong>{{ configuredParamCount }}/{{ createParamOptions.length }} 项已填写</strong>
            </div>
          </div>
          <ShareConfigForm
            ref="shareConfigFormRef"
            v-model="shareConfig"
            :auto-select-user-dept="true"
            :require-read-scope="true"
          >
            <template #manage-description>
              知识库<strong>仅管理员</strong>可以管理；普通用户只能按读取范围使用。
            </template>
          </ShareConfigForm>
        </section>
      </main>

      <footer class="create-flow-footer">
        <span>{{ footerSummary }}</span>
        <div class="footer-actions">
          <a-button v-if="currentStep === 0" @click="handleCancel">取消</a-button>
          <a-button v-else :disabled="creating" @click="currentStep--">上一步</a-button>
          <a-button v-if="currentStep < 2" type="primary" @click="goNext">下一步</a-button>
          <a-button v-else type="primary" :loading="creating" @click="handleCreate">
            创建知识库
          </a-button>
        </div>
      </footer>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { DatabaseZap } from 'lucide-vue-next'
import AiTextarea from '@/components/AiTextarea.vue'
import EmbeddingModelSelector from '@/components/EmbeddingModelSelector.vue'
import ShareConfigForm from '@/components/ShareConfigForm.vue'
import { useChunkPresetOptions } from '@/composables/useChunkPresetOptions'
import { useConfigStore } from '@/stores/config'
import { useDatabaseStore } from '@/stores/database'
import { getKbTypeIcon, getKbTypeLabel } from '@/utils/kb_utils'
import {
  buildDatabaseRequest,
  createDefaultShareConfig,
  createEmptyDatabaseForm,
  selectDatabaseType,
  validateDatabaseConfig
} from '@/utils/databaseCreateForm'

const props = defineProps({
  open: { type: Boolean, default: false },
  supportedKbTypes: { type: Object, default: () => ({}) }
})
const emit = defineEmits(['update:open', 'completed'])
const configStore = useConfigStore()
const databaseStore = useDatabaseStore()
const { chunkPresetSelectOptions: chunkPresetOptions, chunkPresetLoading, loadChunkPresetOptions, getChunkPresetDescription } = useChunkPresetOptions()

const stepLabels = ['类型', '配置', '权限']
const currentStep = ref(0)
const form = reactive(createEmptyDatabaseForm(configStore.config?.embed_model))
const shareConfig = ref(createDefaultShareConfig())
const shareConfigFormRef = ref(null)
const creating = computed(() => databaseStore.state.creating)
const selectedTypeInfo = computed(() => props.supportedKbTypes[form.kb_type] || null)
const selectedTypeLabel = computed(() => selectedTypeInfo.value?.name || getKbTypeLabel(form.kb_type))
const createParamOptions = computed(() => selectedTypeInfo.value?.create_params?.options || [])
const selectedPresetDescription = computed(() => getChunkPresetDescription(form.chunk_preset_id))
const configuredParamCount = computed(() =>
  createParamOptions.value.filter((field) => {
    const value = form.additional_params[field.key]
    return value !== undefined && value !== null && String(value).trim() !== ''
  }).length
)
const footerSummary = computed(() => {
  if (currentStep.value === 0) return form.kb_type ? `已选择 ${selectedTypeLabel.value}` : '请选择知识库类型'
  if (currentStep.value === 1) return form.name.trim() || '填写知识库配置'
  return `${selectedTypeLabel.value} · ${form.name.trim()}`
})

const reset = () => {
  Object.assign(form, createEmptyDatabaseForm(configStore.config?.embed_model))
  const firstType = Object.keys(props.supportedKbTypes)[0] || ''
  Object.assign(form, selectDatabaseType(form, firstType, props.supportedKbTypes[firstType]))
  shareConfig.value = createDefaultShareConfig()
  currentStep.value = 0
}

const selectType = (type) => Object.assign(form, selectDatabaseType(form, type, props.supportedKbTypes[type]))
const handleCancel = () => {
  if (creating.value) return
  emit('update:open', false)
}
const goNext = () => {
  if (currentStep.value === 0 && !selectedTypeInfo.value) {
    message.warning('请选择知识库类型')
    return
  }
  if (currentStep.value === 1) {
    const error = validateDatabaseConfig(form, selectedTypeInfo.value)
    if (error) {
      message.warning(error)
      return
    }
  }
  currentStep.value++
}
const handleCreate = async () => {
  const error = validateDatabaseConfig(form, selectedTypeInfo.value)
  if (error) {
    currentStep.value = 1
    message.warning(error)
    return
  }
  const shareValidation = shareConfigFormRef.value?.validate()
  if (shareValidation && !shareValidation.valid) {
    message.warning(shareValidation.message)
    return
  }
  const request = buildDatabaseRequest(
    form,
    selectedTypeInfo.value,
    shareConfig.value,
    configStore.config?.embed_model
  )
  try {
    const result = await databaseStore.createDatabase(request)
    if (!result) return
    emit('completed', result)
    emit('update:open', false)
  } catch {
    // Store 已展示错误，保留当前步骤和输入供用户修正。
  }
}

watch(
  () => props.open,
  (open) => {
    if (!open) return
    reset()
    loadChunkPresetOptions()
  }
)
watch(
  () => props.supportedKbTypes,
  () => {
    if (props.open && !selectedTypeInfo.value) reset()
  },
  { deep: true }
)
</script>

<style scoped lang="less">
.create-flow-shell { display: flex; flex-direction: column; max-height: min(82vh, 760px); }
.create-flow-header { display: flex; align-items: flex-start; gap: 11px; padding-right: 28px; }
.create-flow-header h2 { margin: 0; color: var(--gray-900); font-size: 18px; line-height: 25px; }
.create-flow-header p { margin: 3px 0 0; color: var(--gray-500); font-size: 12px; }
.create-flow-icon { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 36px; height: 36px; border-radius: 8px; background: var(--gray-100); color: var(--gray-700); }
.create-flow-steps { display: grid; grid-template-columns: repeat(3, 1fr); margin: 20px 0 16px; padding: 0; list-style: none; }
.create-flow-steps li { position: relative; display: grid; grid-template-rows: 22px 18px; justify-items: center; gap: 4px; color: var(--gray-400); font-size: 12px; }
.create-flow-steps li::before { position: absolute; top: 11px; right: 50%; left: -50%; height: 1px; background: var(--gray-150); content: ''; }
.create-flow-steps li:first-child::before { display: none; }
.step-dot { z-index: 1; display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border: 1px solid var(--gray-200); border-radius: 50%; background: var(--gray-0); }
.create-flow-steps li.active, .create-flow-steps li.completed { color: var(--gray-800); font-weight: 600; }
.create-flow-steps li.active .step-dot, .create-flow-steps li.completed .step-dot { border-color: var(--gray-500); background: var(--gray-50); }
.create-flow-steps li.completed::before { background: var(--gray-300); }
.create-flow-body { min-height: 320px; max-height: min(55vh, 500px); overflow-y: auto; padding: 16px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-25); }
.flow-section { display: flex; flex-direction: column; gap: 14px; }
.section-heading { display: flex; flex-direction: column; gap: 2px; }
.section-heading strong { color: var(--gray-900); font-size: 15px; }
.section-heading span, .form-section small { color: var(--gray-500); font-size: 12px; line-height: 18px; }
.type-options { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.type-option { display: flex; min-width: 0; min-height: 132px; padding: 14px; flex-direction: column; align-items: flex-start; gap: 8px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); color: var(--gray-600); cursor: pointer; text-align: left; }
.type-option:hover { border-color: var(--gray-300); background: var(--gray-25); }
.type-option.selected { border-color: var(--main-500); background: var(--main-30); }
.type-option:focus-visible { outline: 2px solid var(--main-400); outline-offset: 2px; }
.type-icon { color: var(--main-color); }
.type-copy { display: flex; min-width: 0; flex-direction: column; gap: 3px; }
.type-copy strong { color: var(--gray-900); font-size: 14px; }
.type-copy small { color: var(--gray-500); font-size: 12px; line-height: 18px; }
.type-badge { margin-top: auto; padding: 2px 7px; border-radius: 999px; background: var(--gray-100); color: var(--gray-600); font-size: 11px; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.form-section { display: flex; min-width: 0; flex-direction: column; gap: 6px; }
.form-section label { color: var(--gray-800); font-size: 13px; font-weight: 600; }
.form-section label b { color: var(--color-error-500); }
.full-width { width: 100%; }
.review-card { overflow: hidden; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.review-card > div { display: flex; justify-content: space-between; gap: 12px; padding: 9px 12px; border-bottom: 1px solid var(--gray-100); font-size: 13px; }
.review-card > div:last-child { border-bottom: 0; }
.review-card span { color: var(--gray-500); }
.review-card strong { min-width: 0; overflow: hidden; color: var(--gray-800); text-overflow: ellipsis; white-space: nowrap; }
.create-flow-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding-top: 14px; color: var(--gray-500); font-size: 12px; }
.footer-actions { display: flex; gap: 8px; }
@media (max-width: 700px) { .type-options, .form-grid { grid-template-columns: 1fr; } .create-flow-footer { align-items: stretch; flex-direction: column; } .footer-actions { justify-content: flex-end; flex-wrap: wrap; } }
</style>

<style lang="less">
@media (max-width: 600px) {
  .database-create-flow-modal { top: 0; width: 100% !important; max-width: none; margin: 0; padding: 0; }
  .database-create-flow-modal .ant-modal-content { min-height: 100vh; border-radius: 0; }
}
</style>
