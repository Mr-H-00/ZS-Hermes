<template>
  <div class="agent-view">
    <div class="agent-view-body">
      <!-- 中间内容区域 -->
      <div class="content">
        <AgentChatComponent
          ref="chatComponentRef"
          :single-mode="false"
          @thread-change="handleThreadChange"
        >
          <template #input-actions-left="{ hasActiveThread, isCreatingThread }">
            <AgentSelectionSection
              v-if="selectedAgentId"
              :model-value="selectedAgentId"
              :agents="chatAgents"
              :disabled="isLoadingConfig || isCreatingThread"
              :locked="hasActiveThread"
              :loading="isLoadingConfig"
              :hint="hasActiveThread ? '当前对话已绑定智能体，新对话可切换。' : ''"
              compact
              show-actions
              placement="topLeft"
              aria-label="切换智能体"
              @update:model-value="
                (agentId) => handleAgentSwitch(agentId, hasActiveThread, isCreatingThread)
              "
              @blocked="(agentId) => handleAgentSwitch(agentId, hasActiveThread, isCreatingThread)"
              @edit="openAgentManagement"
              @create="openCreateAgent"
            />
          </template>
        </AgentChatComponent>
      </div>
    </div>
    <AgentEditModal
      ref="agentEditModalRef"
      :backend-options="agentBackendOptions"
      @saved="handleAgentSaved"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useRoute, useRouter } from 'vue-router'
import { agentApi } from '@/apis/agent_api'
import AgentChatComponent from '@/components/AgentChatComponent.vue'
import AgentSelectionSection from '@/components/AgentSelectionSection.vue'
import AgentEditModal from '@/components/model-management/AgentEditModal.vue'
import { useAgentStore } from '@/stores/agent'
import { handleChatError } from '@/utils/errorHandler'

import { storeToRefs } from 'pinia'

// 组件引用
const chatComponentRef = ref(null)
const agentEditModalRef = ref(null)

// Stores
const agentStore = useAgentStore()
const route = useRoute()
const router = useRouter()

// 从 agentStore 中获取响应式状态
const { agents, selectedAgentId, isLoadingConfig } = storeToRefs(agentStore)
const chatAgents = computed(() => agents.value.filter((agent) => !agent.is_subagent))

const syncingRouteThread = ref(false)

const getRouteThreadId = () => {
  const value = route.params.thread_id
  return typeof value === 'string' ? value : ''
}

const getRouteAgentId = () => {
  const value = route.query.agent_id
  return typeof value === 'string' ? value : ''
}

const syncSelectedThreadFromRoute = async () => {
  const chatComponent = chatComponentRef.value
  if (!chatComponent?.selectThreadFromRoute) return

  const threadId = getRouteThreadId()
  syncingRouteThread.value = true
  try {
    if (!threadId && !agentStore.isInitialized) {
      await agentStore.initialize()
    }

    const ok = await chatComponent.selectThreadFromRoute(threadId)
    if (ok === null) return
    if (threadId && !ok) {
      await router.replace({ name: 'AgentComp' })
    }
  } catch (error) {
    handleChatError(error, 'load')
  } finally {
    syncingRouteThread.value = false
  }
}

const consumeRouteAgentSelection = async () => {
  const targetAgentId = getRouteAgentId()
  if (!targetAgentId || getRouteThreadId()) return

  try {
    if (!agentStore.isInitialized) {
      await agentStore.initialize()
    }

    await nextTick()
    const canSwitch = await chatComponentRef.value?.selectThreadFromRoute?.('')
    if (canSwitch === null) return
    await agentStore.selectAgent(targetAgentId)
  } catch (error) {
    handleChatError(error, 'load')
  } finally {
    const nextQuery = { ...route.query }
    delete nextQuery.agent_id
    await router.replace({ name: 'AgentComp', query: nextQuery })
  }
}

watch(
  () => route.params.thread_id,
  () => {
    syncSelectedThreadFromRoute()
  },
  { immediate: true }
)

watch(
  () => route.query.agent_id,
  () => {
    consumeRouteAgentSelection()
  },
  { immediate: true }
)

watch(chatComponentRef, (instance) => {
  if (!instance) return
  syncSelectedThreadFromRoute()
})

const handleThreadChange = (threadId) => {
  if (syncingRouteThread.value) return
  const currentRouteThreadId = getRouteThreadId()
  const nextThreadId = threadId || ''
  if (currentRouteThreadId === nextThreadId) return

  if (nextThreadId) {
    router.replace({ name: 'AgentCompWithThreadId', params: { thread_id: nextThreadId } })
  } else {
    router.replace({ name: 'AgentComp' })
  }
}

const agentBackendOptions = ref([])
const agentBackendsLoaded = ref(false)

const loadAgentBackends = async () => {
  if (agentBackendsLoaded.value) return
  const response = await agentApi.getAgentBackends()
  agentBackendOptions.value = (response.backends || []).map((backend) => ({
    label: backend.name || backend.backend_id,
    value: backend.backend_id
  }))
  agentBackendsLoaded.value = true
}

const handleAgentSwitch = async (agentId, hasActiveThread, isCreatingThread) => {
  if (!agentId || agentId === selectedAgentId.value) return
  if (isCreatingThread) {
    message.info('正在创建新对话，请稍候')
    return
  }
  if (hasActiveThread) {
    message.info('当前对话已绑定智能体，请新建对话后切换')
    return
  }
  try {
    await agentStore.selectAgent(agentId)
  } catch (error) {
    console.error('切换智能体出错:', error)
    message.error('切换智能体失败')
  }
}

const handleAgentSaved = async ({ mode, agent } = {}) => {
  if (mode === 'create' && !agent?.is_subagent) {
    await chatComponentRef.value?.selectThreadFromRoute?.('')
  }

  await agentStore.fetchAgents()
  if (selectedAgentId.value) {
    await agentStore.fetchAgentDetail(selectedAgentId.value, true)
  }
}

const openCreateAgent = async () => {
  try {
    await loadAgentBackends()
    agentEditModalRef.value?.openCreate()
  } catch (error) {
    message.error(error.message || '打开新建智能体弹窗失败')
  }
}

const openAgentManagement = async () => {
  if (!selectedAgentId.value) {
    message.warning('请先选择智能体')
    return
  }
  try {
    await loadAgentBackends()
    await agentEditModalRef.value?.openEdit(selectedAgentId.value)
  } catch (error) {
    message.error(error.message || '打开智能体配置失败')
  }
}
</script>

<style lang="less" scoped>
.agent-view {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100vh;
  overflow: hidden;
}

.agent-view-body {
  --gap-radius: 6px;
  display: flex;
  flex-direction: row;
  width: 100%;
  flex: 1;
  height: 100%;
  overflow: hidden;
  position: relative;

  .content {
    flex: 1;
    display: flex;
    flex-direction: column;
  }
}

.content {
  flex: 1;
  overflow: hidden;
}
</style>
