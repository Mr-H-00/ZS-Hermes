<script setup>
import { onMounted, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { skillApi } from '@/apis/skill_api'

const props = defineProps({
  slug: { type: String, required: true },
  canManage: Boolean,
  canRelease: { type: Boolean, default: true },
  canRestore: { type: Boolean, default: true }
})
const emit = defineEmits(['restored', 'restoring'])
const versions = ref([])
const revision = ref('')
const loading = ref(false)
const busy = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await skillApi.listSkillVersions(props.slug)
    versions.value = result.data.versions
    revision.value = result.data.revision
  } catch (cause) {
    error.value = cause.message || '加载历史版本失败'
  } finally {
    loading.value = false
  }
}

async function release() {
  if (!props.canManage || !props.canRelease || busy.value || loading.value || error.value) return
  busy.value = true
  try {
    await skillApi.releaseSkillVersion(props.slug, revision.value)
    message.success('已发版')
    await load()
  } catch (cause) {
    message.error(cause.message || '发版失败，请刷新后重试')
  } finally {
    busy.value = false
  }
}

function confirmRestore(version) {
  Modal.confirm({
    title: `恢复 ${version}？`,
    content: '将覆盖当前内容的全部文件和依赖配置，尚未发版的修改会丢失。需要保留时请先发版。',
    okText: '确认恢复',
    cancelText: '取消',
    async onOk() {
      busy.value = true
      emit('restoring', true)
      try {
        await skillApi.restoreSkillVersion(props.slug, version, revision.value)
        message.success('已恢复历史版本')
        emit('restored')
        await load()
      } catch (cause) {
        message.error(cause.message || '恢复失败，请刷新后重试')
        throw cause
      } finally {
        busy.value = false
        emit('restoring', false)
      }
    }
  })
}

function confirmDelete(version) {
  Modal.confirm({
    title: `删除 ${version}？`,
    content: '历史快照将永久删除，当前内容保持不变。',
    okText: '确认删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      busy.value = true
      try {
        await skillApi.deleteSkillVersion(props.slug, version)
        message.success('已删除历史版本')
        await load()
      } catch (cause) {
        message.error(cause.message || '删除失败')
        throw cause
      } finally {
        busy.value = false
      }
    }
  })
}

onMounted(load)
</script>

<template>
  <section class="skill-versions">
    <div class="version-actions">
      <div>
        <h3>历史版本</h3>
        <p>发版保留当前已保存的内容，恢复将覆盖现有内容。</p>
      </div>
      <a-space :size="8">
        <a-button
          v-if="canManage"
          type="primary"
          :loading="busy"
          :disabled="!canRelease || loading || Boolean(error)"
          :title="!canRelease ? '请先保存或放弃当前修改' : '发布当前已保存内容'"
          @click="release"
          >发版</a-button
        >
        <a-button :loading="loading" :disabled="busy" @click="load">刷新</a-button>
      </a-space>
    </div>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-spin v-else-if="loading" />
    <a-empty v-else-if="!versions.length" description="尚未发版" />
    <a-list v-else :data-source="versions">
      <template #renderItem="{ item }">
        <a-list-item>
          <a-list-item-meta
            :title="item.version"
            :description="new Date(item.created_at).toLocaleString('zh-CN', { hour12: false })"
          />
          <template v-if="canManage" #actions>
            <a-button
              :disabled="busy || !canRestore"
              :title="!canRestore ? '请先保存或放弃当前修改' : ''"
              @click="confirmRestore(item.version)"
              type="text"
              >恢复</a-button
            >
            <a-button type="text" danger :disabled="busy" @click="confirmDelete(item.version)"
              >删除</a-button
            >
          </template>
        </a-list-item>
      </template>
    </a-list>
  </section>
</template>

<style scoped>
.skill-versions {
  max-width: 960px;
  margin: 0 auto;
  padding: 28px 32px;
}
.version-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 20px;
}
h3 {
  color: var(--gray-900);
  margin: 0 0 6px;
  font-size: 16px;
}
p {
  color: var(--gray-600);
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
}
.skill-versions :deep(.ant-list-item) {
  padding: 18px 0;
}
.skill-versions :deep(.ant-list-item-meta-title) {
  font-size: 13px;
  font-family: monospace;
  margin-bottom: 4px;
}
.skill-versions :deep(.ant-list-item-meta-description) {
  font-size: 12px;
}
.skill-versions :deep(.ant-list-item-action) {
  margin-left: 16px;
}
@media (max-width: 600px) {
  .skill-versions :deep(.ant-list-item) {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }
  .skill-versions :deep(.ant-list-item-action) {
    margin: 0;
    align-self: flex-end;
  }
  .skill-versions :deep(.ant-list-item-meta) {
    width: 100%;
  }
  .skill-versions {
    padding: 20px 16px;
  }
  .version-actions {
    gap: 12px;
  }
}
</style>
