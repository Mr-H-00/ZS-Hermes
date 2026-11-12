<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { BookOpen, Plus, RefreshCw } from '@lucide/vue'
import { agentApi } from '@/apis/agent_api'
import { skillApi } from '@/apis/skill_api'
import MarkdownPreview from '@/modules/workspace/ui/MarkdownPreview.vue'

const props = defineProps({ agentSlug: { type: String, required: true } })
const emit = defineEmits(['navigate', 'busy'])
const binding = ref(null)
const content = ref('')
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const fileInput = ref(null)

async function load() {
  loading.value = true
  error.value = ''
  try {
    binding.value = await agentApi.getAgentBoundSkill(props.agentSlug)
    await loadPreview()
  } catch (cause) {
    error.value = cause.message || '加载专属 Skill 失败'
  } finally {
    loading.value = false
  }
}

async function loadPreview() {
  content.value = ''
  if (binding.value?.skill) {
    const result = await skillApi.getSkillFile(binding.value.skill.slug, 'SKILL.md')
    content.value = result.data.content
  }
}

async function create() {
  if (saving.value) return
  saving.value = true
  emit('busy', true)
  try {
    binding.value = await agentApi.createAgentBoundSkill(props.agentSlug)
    await load()
    message.success('已创建操作指南')
  } catch (cause) {
    message.error(cause.message || '创建失败')
  } finally {
    saving.value = false
    emit('busy', false)
  }
}

async function upload(file) {
  if (saving.value) return
  saving.value = true
  emit('busy', true)
  try {
    binding.value = await agentApi.uploadAgentBoundSkill(
      props.agentSlug,
      file
    )
    await load()
    message.success('专属 Skill 已导入')
  } catch (cause) {
    message.error(cause.message || '上传失败；请刷新后重试')
  } finally {
    saving.value = false
    emit('busy', false)
  }
}

function selectPackage(event) {
  if (saving.value) return
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.zip') || file.size > 10 * 1024 * 1024) {
    message.error('请选择不超过 10 MiB 的 ZIP 文件')
    return
  }
  upload(file)
}

function openDetail() {
  if (saving.value || loading.value || !binding.value?.skill) return
  emit('navigate', {
    name: 'ExtensionSkillDetail',
    params: { slug: binding.value.skill.slug },
    query: { agent: props.agentSlug }
  })
}

onMounted(load)
</script>

<template>
  <section class="bound-skill-panel">
    <header class="bound-skill-header">
      <div>
        <h3>专属 Skill</h3>
        <p>操作流程与资源，运行时自动加载。</p>
      </div>
      <div v-if="binding?.skill && !error" class="header-actions">
        <a-button type="text" :disabled="saving || loading" @click="load">
          <template #icon><RefreshCw :size="14" /></template>
          刷新
        </a-button>
        <a-button :disabled="saving || loading" @click="openDetail">{{ binding.can_manage ? '编辑 Skill' : '查看文件' }}</a-button>
      </div>
    </header>
    <div v-if="loading" class="skill-loading"><a-spin aria-label="加载专属 Skill" /></div>
    <a-alert v-else-if="error" type="error" :message="error" show-icon>
      <template #action><a-button size="small" @click="load">重试</a-button></template>
    </a-alert>
    <template v-else>
      <div v-if="binding?.skill" class="bound-skill-preview" aria-label="专属 Skill 内容预览">
        <div class="preview-content"><MarkdownPreview :content="content" compact /></div>
      </div>
      <div v-else class="skill-empty">
        <BookOpen :size="28" :stroke-width="1.4" class="empty-icon" aria-hidden="true" />
        <p>尚未添加专属 Skill</p>
        <div v-if="binding?.can_manage" class="empty-actions">
          <a-button :loading="saving" @click="create">
            <template #icon><Plus :size="14" /></template>
            创建 Skill
          </a-button>
          <a-button type="text" :disabled="saving" @click="fileInput.click()">导入 ZIP</a-button>
        </div>
      </div>
      <input v-if="binding?.can_manage && !binding.skill" ref="fileInput" type="file" accept=".zip" hidden @change="selectPackage" />
    </template>
  </section>
</template>

<style scoped lang="less">
.bound-skill-panel {
  display: flex;
  flex-direction: column;
  gap: 24px;
}
.bound-skill-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
h3 {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
}
p {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}
.header-actions,
.empty-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
:deep(.ant-btn) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 13px;
  box-shadow: none;
}
.skill-loading {
  padding: 64px 0;
  text-align: center;
}
.skill-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 56px 16px 64px;
  border-top: 1px solid var(--gray-100);

  .empty-icon {
    margin-bottom: 16px;
    color: var(--gray-400);
  }
  .empty-actions {
    margin-top: 20px;
  }
}
.bound-skill-preview {
  overflow-wrap: anywhere;
}
@media (max-width: 600px) {
  .bound-skill-header {
    align-items: flex-start;
    flex-wrap: wrap;
  }
}
</style>
