<template>
  <div class="message-debug-panel">
    <!-- 顶部工具栏 -->
    <div class="debug-panel-toolbar">
      <div class="toolbar-top-row">
        <div class="toolbar-title-group">
          <Bug :size="15" class="title-icon" />
          <span class="toolbar-title">消息时序调试</span>
          <span class="item-count-badge">{{ filteredTimelineItems.length }} 条</span>
        </div>
        <div class="toolbar-actions">
          <button
            type="button"
            class="action-btn"
            :disabled="isAuditLoading || !threadId"
            :aria-busy="isAuditLoading"
            title="刷新 Model/Tool 审计"
            @click="refreshMessageAudits"
          >
            <LoaderCircle v-if="isAuditLoading" :size="13" class="audit-loading-icon" />
            <RefreshCw v-else :size="13" />
            <span>{{ isAuditLoading ? '读取中' : '刷新审计' }}</span>
          </button>
          <button
            type="button"
            class="action-btn"
            :title="isAllExpanded ? '全部折叠' : '全部展开'"
            @click="toggleExpandAll"
          >
            <FoldVertical v-if="isAllExpanded" :size="13" />
            <UnfoldVertical v-else :size="13" />
            <span>{{ isAllExpanded ? '全部折叠' : '全部展开' }}</span>
          </button>
          <button
            type="button"
            class="action-btn"
            title="复制全部消息数据"
            @click="copyAllTimelineJson"
          >
            <Check v-if="isAllCopied" :size="13" class="copied-icon" />
            <Copy v-else :size="13" />
            <span>{{ isAllCopied ? '已复制' : '复制数据' }}</span>
          </button>
        </div>
      </div>

      <!-- 角色筛选与搜索栏 -->
      <div class="toolbar-bottom-row">
        <div class="role-filter-chips">
          <button
            v-for="tab in filterTabs"
            :key="tab.key"
            type="button"
            class="filter-chip"
            :class="{ active: currentFilter === tab.key }"
            @click="currentFilter = tab.key"
          >
            {{ tab.label }}
            <span v-if="tab.count !== undefined" class="chip-count">{{ tab.count }}</span>
          </button>
        </div>
        <div class="search-box">
          <Search :size="13" class="search-icon" />
          <input
            v-model="searchQuery"
            type="text"
            placeholder="搜索消息或字段..."
            class="search-input"
          />
        </div>
      </div>
    </div>

    <div v-if="auditLoadError" class="audit-error" role="status">
      <TriangleAlert :size="14" aria-hidden="true" />
      <span>Model/Tool 审计读取失败，当前仍展示已有消息。</span>
      <button type="button" @click="refreshMessageAudits">重试</button>
    </div>

    <!-- 消息列表主体 -->
    <div class="timeline-container">
      <div v-if="filteredTimelineItems.length === 0" class="empty-timeline">
        <LoaderCircle v-if="isAuditLoading" :size="24" class="empty-icon audit-loading-icon" />
        <Clock v-else :size="24" class="empty-icon" />
        <span class="empty-text">
          {{
            isAuditLoading
              ? '正在读取 Model/Tool 审计...'
              : searchQuery
                ? '未找到匹配的消息'
                : '当前会话暂无消息数据'
          }}
        </span>
      </div>

      <div v-else class="timeline-list">
        <section
          v-for="group in filteredTimelineGroups"
          :key="group.key"
          class="run-group"
          :class="{ 'run-group-unassigned': !group.runId }"
          :aria-label="group.runId ? `Run ${group.runId}` : '未关联 Run'"
        >
          <header class="run-group-header">
            <Workflow :size="14" class="run-group-icon" />
            <span class="run-group-label">{{ group.runId ? 'Run' : '未关联 Run' }}</span>
            <code v-if="group.runId" class="run-group-id" :title="group.runId">
              {{ formatRunId(group.runId) }}
            </code>
            <div class="run-group-actions">
              <button
                v-if="group.runId"
                type="button"
                class="run-langfuse-btn"
                :disabled="isLangfuseRunOpening(group.runId)"
                :aria-busy="isLangfuseRunOpening(group.runId)"
                :title="
                  isLangfuseRunOpening(group.runId)
                    ? '正在打开 Langfuse'
                    : '在 Langfuse 中查看此 Run'
                "
                @click.stop="openRunInLangfuse(group.runId)"
              >
                <LoaderCircle
                  v-if="isLangfuseRunOpening(group.runId)"
                  :size="12"
                  class="run-langfuse-spinner"
                  aria-hidden="true"
                />
                <ExternalLink v-else :size="12" aria-hidden="true" />
                <span>Langfuse</span>
              </button>
              <span class="run-group-count">{{ group.items.length }} 条</span>
            </div>
          </header>

          <div
            v-for="item in group.items"
            :key="item.id"
            class="timeline-item"
            :class="`role-${item.role}`"
          >
            <!-- 单行：Icon + Role + 摘要，点击整行展开/折叠 -->
            <div class="item-header" @click="toggleItemExpand(item.id)">
              <component :is="item.icon" :size="15" class="role-icon" />
              <span :class="['role-pill', `pill-${item.role}`]">{{ item.roleLabel }}</span>
              <span
                v-if="item.executionStatus"
                :class="['status-badge', `status-${item.executionStatus}`]"
              >
                {{ formatExecutionStatus(item.executionStatus) }}
              </span>
              <span class="header-summary" :title="item.summary">{{ item.summary }}</span>

              <div class="header-right">
                <span
                  v-if="item.sequence !== null"
                  class="sequence-badge"
                  :title="`ProtocolEvent 起始序号 ${item.sequence}`"
                >
                  #{{ item.sequence }}
                </span>
                <span
                  v-if="item.startedAt || item.finishedAt"
                  class="time-badge"
                  :title="formatAuditTimeTooltip(item)"
                >
                  {{ formatAuditTimeLabel(item) }}
                </span>
                <span
                  v-if="item.durationMs !== null"
                  class="duration-badge"
                  title="后端 monotonic clock 记录的 Model/Tool 耗时"
                >
                  {{ formatAuditDuration(item.durationMs) }}
                </span>
                <span v-if="item.tokenSummary" class="token-badge" :title="item.tokenTooltip">
                  {{ item.tokenSummary }}
                </span>
                <button
                  type="button"
                  class="item-icon-btn"
                  title="复制此消息 JSON"
                  @click.stop="copyItemJson(item)"
                >
                  <Check v-if="copiedItemId === item.id" :size="13" class="copied-icon" />
                  <Copy v-else :size="13" />
                </button>
                <button
                  type="button"
                  class="item-icon-btn expand-btn"
                  :title="expandedItemIds.has(item.id) ? '折叠' : '展开'"
                >
                  <ChevronDown v-if="expandedItemIds.has(item.id)" :size="15" />
                  <ChevronRight v-else :size="15" />
                </button>
              </div>
            </div>

            <!-- 展开后的折叠式 JSON 树 -->
            <div v-if="expandedItemIds.has(item.id)" class="item-body">
              <JsonTreeViewer :data="item.raw" :default-expanded-depth="1" :show-toolbar="false" />
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  Bot,
  Bug,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Copy,
  ExternalLink,
  FoldVertical,
  LoaderCircle,
  RefreshCw,
  Search,
  Settings2,
  TriangleAlert,
  UnfoldVertical,
  User,
  Workflow,
  Wrench
} from '@lucide/vue'
import { message } from 'ant-design-vue'
import JsonTreeViewer from '@/components/common/JsonTreeViewer.vue'
import { agentApi } from '@/apis/agent_api'
import { copyTextToClipboard } from '@/utils/clipboard'
import { formatDateTime } from '@/utils/time'
import {
  buildMessageDebugEntries,
  formatAuditDuration,
  groupMessageDebugEntries,
  mergeMessageDebugAudits,
  resolveLangfuseRunUrl,
  shouldPollMessageAudits
} from '@/utils/messageDebug'

const props = defineProps({
  messages: {
    type: Array,
    default: () => []
  },
  threadId: {
    type: String,
    default: null
  },
  active: {
    type: Boolean,
    default: false
  },
  activeRunId: {
    type: String,
    default: null
  },
  runActive: {
    type: Boolean,
    default: false
  }
})
const currentFilter = ref('all')
const searchQuery = ref('')
const expandedItemIds = ref(new Set())
const isAllCopied = ref(false)
const copiedItemId = ref('')
const openingLangfuseRunIds = ref(new Set())
const messageAudits = ref([])
const isAuditLoading = ref(false)
const auditLoadError = ref(false)
const pageVisible = ref(document.visibilityState === 'visible')
let auditPollTimer = null
let auditRequestGeneration = 0
let auditRequestInFlight = false

const loadMessageAudits = async (silent = false) => {
  if (!props.threadId || auditRequestInFlight) return
  const generation = ++auditRequestGeneration
  auditRequestInFlight = true
  if (!silent) isAuditLoading.value = true
  try {
    const result = await agentApi.getThreadMessageAudits(props.threadId)
    if (generation !== auditRequestGeneration) return
    messageAudits.value = Array.isArray(result?.audits) ? result.audits : []
    auditLoadError.value = false
  } catch {
    if (generation === auditRequestGeneration) auditLoadError.value = true
  } finally {
    if (generation === auditRequestGeneration) {
      auditRequestInFlight = false
      if (!silent) isAuditLoading.value = false
    }
  }
}

const refreshMessageAudits = () => loadMessageAudits(false)

const stopAuditPolling = () => {
  if (auditPollTimer) window.clearInterval(auditPollTimer)
  auditPollTimer = null
}

const handlePageVisibilityChange = () => {
  pageVisible.value = document.visibilityState === 'visible'
}
document.addEventListener('visibilitychange', handlePageVisibilityChange)

watch(
  () => [props.threadId, props.active, props.activeRunId, props.runActive, pageVisible.value],
  ([threadId, active, activeRunId, runActive, isPageVisible], previous = []) => {
    stopAuditPolling()
    auditRequestGeneration += 1
    auditRequestInFlight = false
    isAuditLoading.value = false
    if (threadId !== previous[0]) {
      messageAudits.value = []
      auditLoadError.value = false
    }
    if (!active || !threadId || !isPageVisible) return
    refreshMessageAudits()
    if (
      shouldPollMessageAudits({
        panelActive: active,
        pageVisible: isPageVisible,
        runActive,
        activeRunId
      })
    ) {
      auditPollTimer = window.setInterval(() => loadMessageAudits(true), 2000)
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  auditRequestGeneration += 1
  stopAuditPolling()
  document.removeEventListener('visibilitychange', handlePageVisibilityChange)
})

const executionStatusLabels = {
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  interrupted: '已中断',
  abandoned: '已放弃'
}

const formatExecutionStatus = (status) => executionStatusLabels[status] || status

const formatAuditTimeLabel = (item) => {
  const value = item.finishedAt || item.startedAt
  if (!value) return ''
  let prefix = '开始'
  if (item.finishedAt) prefix = item.executionStatus === 'completed' ? '完成' : '关闭'
  return `${prefix} ${formatDateTime(value, 'HH:mm:ss')}`
}

const formatAuditTimeTooltip = (item) =>
  [
    item.startedAt ? `开始：${formatDateTime(item.startedAt, 'YYYY-MM-DD HH:mm:ss')}` : '',
    item.finishedAt ? `结束：${formatDateTime(item.finishedAt, 'YYYY-MM-DD HH:mm:ss')}` : '',
    item.operationId ? `Operation：${item.operationId}` : ''
  ]
    .filter(Boolean)
    .join('\n')

const usageTokenCounts = (usage) => {
  const prompt = usage?.prompt_tokens ?? usage?.input_tokens ?? usage?.prompt ?? usage?.input ?? 0
  const completion =
    usage?.completion_tokens ?? usage?.output_tokens ?? usage?.completion ?? usage?.output ?? 0
  return {
    prompt,
    completion,
    total: usage?.total_tokens ?? usage?.total ?? prompt + completion
  }
}

// 格式化 Tokens 显示
const formatTokens = (usage) => {
  if (!usage) return ''
  const { total, prompt, completion } = usageTokenCounts(usage)
  if (!total && !prompt && !completion) return ''
  if (total >= 1000) return `${(total / 1000).toFixed(1)}k tokens`
  return `${total} tokens`
}

const roleIcons = {
  human: User,
  ai: Bot,
  tool: Wrench,
  error: TriangleAlert,
  system: Settings2,
  other: Clock
}

const formatRunId = (runId) => {
  if (runId.length <= 18) return runId
  return `${runId.slice(0, 8)}…${runId.slice(-6)}`
}

// 普通历史保持原契约；调试视图按稳定 operation 合并专用 PG 审计。
const timelineItems = computed(() =>
  buildMessageDebugEntries(mergeMessageDebugAudits(props.messages, messageAudits.value)).map((item) => {
    const usage = item.usage
    const tokenCounts = usageTokenCounts(usage)
    return {
      ...item,
      icon: roleIcons[item.role] || Clock,
      tokenSummary: formatTokens(usage),
      tokenTooltip: usage
        ? `输入: ${tokenCounts.prompt}, 输出: ${tokenCounts.completion}, 总计: ${tokenCounts.total}`
        : ''
    }
  })
)

// 筛选标签
const filterTabs = computed(() => {
  const all = timelineItems.value
  return [
    { key: 'all', label: '全部', count: all.length },
    { key: 'human', label: '用户', count: all.filter((i) => i.role === 'human').length },
    { key: 'ai', label: 'AI', count: all.filter((i) => i.role === 'ai').length },
    { key: 'tool', label: '工具', count: all.filter((i) => i.role === 'tool').length },
    {
      key: 'error',
      label: '错误',
      count: all.filter((i) => i.role === 'error' || i.executionStatus === 'failed').length
    },
    { key: 'system', label: '系统', count: all.filter((i) => i.role === 'system').length }
  ]
})

// 过滤后的消息列表
const filteredTimelineItems = computed(() => {
  let list = timelineItems.value

  if (currentFilter.value === 'error') {
    list = list.filter((item) => item.role === 'error' || item.executionStatus === 'failed')
  } else if (currentFilter.value !== 'all') {
    list = list.filter((item) => item.role === currentFilter.value)
  }

  const q = searchQuery.value.trim().toLowerCase()
  if (q) {
    list = list.filter((item) => {
      return (
        item.roleLabel.toLowerCase().includes(q) ||
        item.summary.toLowerCase().includes(q) ||
        String(item.model || '')
          .toLowerCase()
          .includes(q) ||
        String(JSON.stringify(item.raw) || '')
          .toLowerCase()
          .includes(q)
      )
    })
  }

  return list
})

const filteredTimelineGroups = computed(() => groupMessageDebugEntries(filteredTimelineItems.value))

const isAllExpanded = computed(() => {
  if (filteredTimelineItems.value.length === 0) return false
  return filteredTimelineItems.value.every((item) => expandedItemIds.value.has(item.id))
})

const toggleExpandAll = () => {
  if (isAllExpanded.value) {
    expandedItemIds.value.clear()
  } else {
    filteredTimelineItems.value.forEach((item) => {
      expandedItemIds.value.add(item.id)
    })
  }
}

const toggleItemExpand = (id) => {
  if (expandedItemIds.value.has(id)) {
    expandedItemIds.value.delete(id)
  } else {
    expandedItemIds.value.add(id)
  }
}

const isLangfuseRunOpening = (runId) => openingLangfuseRunIds.value.has(runId)

const setLangfuseRunOpening = (runId, opening) => {
  const next = new Set(openingLangfuseRunIds.value)
  if (opening) next.add(runId)
  else next.delete(runId)
  openingLangfuseRunIds.value = next
}

const openRunInLangfuse = async (runId) => {
  if (!runId || isLangfuseRunOpening(runId)) return

  const targetWindow = window.open('about:blank', '_blank')
  if (!targetWindow) {
    message.warning('浏览器阻止了新标签页，请允许弹窗后重试')
    return
  }
  targetWindow.opener = null
  setLangfuseRunOpening(runId, true)

  try {
    const result = await agentApi.getAgentRunLangfuseLink(runId)
    const traceUrl = resolveLangfuseRunUrl(result)
    if (!traceUrl) {
      targetWindow.close()
      if (result?.reason === 'trace_not_available') {
        message.warning('该 Run 暂无可用的 Langfuse Trace')
      } else {
        message.error('Langfuse 当前不可用，请检查配置或稍后重试')
      }
      return
    }
    targetWindow.location.replace(traceUrl)
  } catch {
    targetWindow.close()
    message.error('获取 Langfuse 跳转地址失败，请稍后重试')
  } finally {
    setLangfuseRunOpening(runId, false)
  }
}

const copyItemJson = async (item) => {
  try {
    await copyTextToClipboard(JSON.stringify(item.raw, null, 2))
    copiedItemId.value = item.id
    message.success('已复制消息 JSON')
    setTimeout(() => {
      if (copiedItemId.value === item.id) copiedItemId.value = ''
    }, 1500)
  } catch {
    message.error('复制失败')
  }
}

const copyAllTimelineJson = async () => {
  try {
    const allData = timelineItems.value.map((i) => ({
      role: i.roleLabel,
      model: i.model,
      raw: i.raw
    }))
    await copyTextToClipboard(JSON.stringify(allData, null, 2))
    isAllCopied.value = true
    message.success('已复制全部消息数据')
    setTimeout(() => {
      isAllCopied.value = false
    }, 1500)
  } catch {
    message.error('复制失败')
  }
}
</script>

<style scoped lang="less">
.message-debug-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--gray-0);
  overflow: hidden;
  container-type: inline-size;
  font-family:
    -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

/* 顶部工具栏 */
.debug-panel-toolbar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  background: var(--gray-0);
  border-bottom: 1px solid var(--gray-200);
  flex-shrink: 0;
}

.toolbar-top-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.toolbar-title-group {
  display: flex;
  align-items: center;
  gap: 6px;

  .title-icon {
    color: var(--gray-800);
  }

  .toolbar-title {
    font-size: 13.5px;
    font-weight: 600;
    color: var(--gray-1000);
  }

  .item-count-badge {
    font-size: 11.5px;
    padding: 2px 6px;
    border-radius: 4px;
    background: var(--gray-150);
    color: var(--gray-700);
    font-family: 'Consolas', 'Monaco', monospace;
  }
}

.toolbar-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.action-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 8px;
  font-size: 12px;
  border-radius: 4px;
  border: 1px solid var(--gray-300);
  background: var(--gray-0);
  color: var(--gray-800);
  cursor: pointer;
  transition: all 0.12s ease;

  &:hover:not(:disabled) {
    background: var(--gray-50);
    border-color: var(--gray-400);
    color: var(--gray-1000);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 1px;
  }

  &:disabled {
    color: var(--gray-400);
    cursor: wait;
  }

  .copied-icon {
    color: var(--color-success-700);
  }
}

.toolbar-bottom-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.role-filter-chips {
  display: flex;
  gap: 2px;
}

.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 6px;
  font-size: 12px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: var(--gray-600);
  cursor: pointer;
  transition: all 0.12s ease;

  &:hover {
    background: var(--gray-100);
    color: var(--gray-900);
  }

  &.active {
    background: var(--gray-150);
    border-color: var(--gray-300);
    color: var(--gray-1000);
    font-weight: 600;
  }

  .chip-count {
    font-size: 10.5px;
    opacity: 0.75;
  }
}

.search-box {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 1;
  max-width: 170px;
  height: 24px;
  padding: 0 8px;
  border-radius: 4px;
  border: 1px solid var(--gray-300);
  background: var(--gray-0);

  &:focus-within {
    border-color: var(--main-color);
    box-shadow: 0 0 0 2px var(--main-10);
  }

  .search-icon {
    color: var(--gray-400);
    flex-shrink: 0;
  }

  .search-input {
    width: 100%;
    border: none;
    background: transparent;
    outline: none;
    font-size: 12px;
    color: var(--gray-900);

    &::placeholder {
      color: var(--gray-400);
    }
  }
}

.audit-error {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--color-warning-100);
  background: var(--color-warning-50);
  color: var(--color-warning-900);
  font-size: 12px;

  span {
    min-width: 0;
    flex: 1;
  }

  button {
    flex-shrink: 0;
    padding: 2px 6px;
    border: 1px solid var(--color-warning-100);
    border-radius: 4px;
    background: transparent;
    color: inherit;
    cursor: pointer;
  }
}

.audit-loading-icon {
  animation: audit-loading-spin 0.8s linear infinite;
}

@keyframes audit-loading-spin {
  to {
    transform: rotate(360deg);
  }
}

/* 消息列表 */
.timeline-container {
  flex: 1;
  overflow-y: auto;
  background: var(--gray-0);
}

.empty-timeline {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 200px;
  color: var(--gray-400);
  gap: 8px;

  .empty-icon {
    color: var(--gray-300);
  }

  .empty-text {
    font-size: 12px;
  }
}

.timeline-list {
  display: flex;
  flex-direction: column;
}

.run-group + .run-group {
  border-top: 4px solid var(--gray-50);
}

.run-group-header {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding: 5px 12px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-25);
  color: var(--gray-700);
}

.run-group-icon {
  flex-shrink: 0;
  color: var(--main-color);
}

.run-group-label {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--gray-900);
}

.run-group-id {
  min-width: 0;
  overflow: hidden;
  color: var(--gray-700);
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.run-group-actions {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.run-langfuse-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  height: 22px;
  padding: 0 6px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: var(--gray-600);
  font-size: 11.5px;
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    border-color 0.12s ease,
    color 0.12s ease;

  &:hover:not(:disabled) {
    border-color: var(--gray-200);
    background: var(--gray-100);
    color: var(--main-color);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 1px;
  }

  &:disabled {
    color: var(--gray-400);
    cursor: wait;
  }
}

.run-langfuse-spinner {
  animation: run-langfuse-spin 0.8s linear infinite;
}

@keyframes run-langfuse-spin {
  to {
    transform: rotate(360deg);
  }
}

.run-group-count {
  flex-shrink: 0;
  color: var(--gray-500);
  font-size: 11px;
}

.run-group-unassigned {
  .run-group-icon {
    color: var(--gray-500);
  }

  .run-group-label {
    color: var(--gray-700);
  }
}

.timeline-item {
  background: var(--gray-0);
  border-bottom: 1px solid var(--gray-150);
  transition: background-color 0.15s ease;

  &:hover {
    background: var(--gray-50);
  }

  &:last-child {
    border-bottom: none;
  }

  &.role-error {
    background: var(--color-error-50);

    &:hover {
      background: var(--color-error-100);
    }

    .role-icon {
      color: var(--color-error-500);
    }
  }
}

.item-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
}

.role-icon {
  color: var(--gray-700);
  flex-shrink: 0;
}

.role-pill {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--gray-800);
  white-space: nowrap;
  flex-shrink: 0;

  &.pill-human {
    color: var(--gray-900);
  }

  &.pill-ai {
    color: var(--gray-900);
  }

  &.pill-error {
    color: var(--color-error-700);
  }

  &.pill-system {
    color: var(--gray-600);
  }
}

.status-badge {
  flex-shrink: 0;
  padding: 2px 5px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-700);
  font-size: 11px;
  font-weight: 500;
  line-height: 1.25;
  white-space: nowrap;

  &.status-running {
    background: var(--color-info-50);
    color: var(--color-info-700);
  }

  &.status-completed {
    background: var(--color-success-50);
    color: var(--color-success-700);
  }

  &.status-failed {
    background: var(--color-error-50);
    color: var(--color-error-700);
  }

  &.status-interrupted,
  &.status-abandoned {
    background: var(--color-warning-50);
    color: var(--color-warning-900);
  }
}

.header-summary {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  color: var(--gray-700);
  line-height: 1.5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.sequence-badge,
.time-badge,
.duration-badge,
.token-badge {
  padding: 2px 5px;
  border-radius: 4px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  white-space: nowrap;
}

.sequence-badge,
.duration-badge,
.token-badge {
  font-family: 'Consolas', 'Monaco', monospace;
}

.duration-badge {
  color: var(--gray-800);
}

.token-badge {
  background: var(--gray-150);
  color: var(--gray-700);
}

.item-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--gray-500);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.12s ease;

  &:hover {
    background: var(--gray-150);
    color: var(--gray-1000);
  }

  .copied-icon {
    color: var(--color-success-700);
  }
}

.item-body {
  padding: 0 12px 10px 35px;
}

@container (max-width: 420px) {
  .debug-panel-toolbar {
    gap: 6px;
  }

  .toolbar-top-row,
  .toolbar-bottom-row {
    align-items: stretch;
    flex-direction: column;
  }

  .toolbar-actions {
    width: 100%;
  }

  .action-btn {
    flex: 1;
    justify-content: center;
    min-width: 0;
    padding: 0 5px;

    span {
      display: none;
    }
  }

  .role-filter-chips {
    width: 100%;
    overflow-x: auto;
  }

  .filter-chip {
    flex-shrink: 0;
  }

  .search-box {
    box-sizing: border-box;
    width: 100%;
    max-width: none;
  }

  .run-group-header {
    padding-inline: 8px;
  }

  .run-langfuse-btn span {
    display: none;
  }

  .item-header {
    display: grid;
    grid-template-columns: 15px minmax(0, 1fr) auto;
    gap: 4px 6px;
    padding-inline: 8px;
  }

  .role-icon {
    grid-column: 1;
    grid-row: 1;
  }

  .role-pill {
    grid-column: 2;
    grid-row: 1;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .status-badge {
    grid-column: 3;
    grid-row: 1;
  }

  .header-summary {
    grid-column: 2 / 4;
    grid-row: 2;
  }

  .header-right {
    grid-column: 2 / 4;
    grid-row: 3;
    min-width: 0;
    flex-wrap: wrap;
  }

  .item-body {
    padding-left: 29px;
  }
}
</style>
