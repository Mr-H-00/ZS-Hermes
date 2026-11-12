<template>
  <div class="graph-section" v-if="isGraphSupported">
    <div class="graph-container-compact">
      <div v-if="!isGraphSupported" class="graph-disabled">
        <div class="disabled-content">
          <h4>知识图谱不可用</h4>
          <p>当前知识库类型 "{{ kbTypeLabel }}" 不支持知识图谱功能。</p>
          <p>只有 Yuxi 类型的知识库支持知识图谱。</p>
        </div>
      </div>
      <div v-else class="graph-wrapper">
        <GraphCanvas
          ref="graphRef"
          :graph-data="graph.graphData"
          @node-click="handleGraphNodeClick"
          @edge-click="handleGraphEdgeClick"
          @canvas-click="graph.handleCanvasClick"
        >
          <template #top>
            <div class="compact-actions">
              <div class="actions-left">
                <a-button
                  class="action-btn search-action-btn"
                  @click="onSearch"
                  title="搜索"
                  aria-label="搜索"
                >
                  <component
                    :is="graph.fetching ? Loader2 : Search"
                    :size="16"
                    :class="{ spin: graph.fetching }"
                  />
                </a-button>
                <a-input
                  v-model:value="searchInput"
                  placeholder="搜索实体"
                  class="graph-search-input"
                  @keydown.enter="onSearch"
                  allow-clear
                />
                <a-button
                  class="action-btn settings-action-btn"
                  @click="toggleSettingsPanel"
                  title="图谱展示设置"
                  aria-label="图谱展示设置"
                >
                  <Settings :size="16" />
                </a-button>
              </div>
              <div class="file-filter">
                <a-button class="file-filter-button" @click="showFileSearch = true" :title="selectedFileLabel" :aria-label="fileId ? `选择文件，当前：${selectedFileLabel}` : '按文件查看'">
                  <FileText :size="16" />
                  <span v-if="fileId">{{ selectedFileLabel }}</span>
                </a-button>
                <a-button
                  v-if="fileId"
                  class="file-filter-clear"
                  title="清除文件筛选"
                  aria-label="清除文件筛选"
                  @click="clearFileFilter"
                >
                  <X :size="16" />
                </a-button>
              </div>
              <a-button
                v-if="fileId && isMilvus && !readonly && filePendingChunks > 0"
                class="lucide-icon-btn file-build-action"
                :disabled="!graphBuildStatus || graphBuildStatusError || isBuildActive || awaitingBuildStatus"
                :loading="graphBuildSubmitting"
                @click="graphBuildStatus?.locked ? startGraphBuild(fileId) : openGraphConfig()"
              >
                <Database :size="16" />
                {{ isBuildActive ? '知识库图谱构建中' : awaitingBuildStatus ? '正在确认构建状态' : graphBuildStatus && !graphBuildStatus.locked ? '配置抽取器' : '构建此文件图谱' }}
              </a-button>
              <a-button
                v-if="isMilvus && !readonly && graphBuildStatusError"
                class="lucide-icon-btn build-status-retry"
                :loading="graphBuildLoading"
                @click="loadGraphBuildStatus"
              >
                <RefreshCw :size="16" />
                图谱构建状态加载失败，重试
              </a-button>
              <a-button
                v-if="fileId && !readonly && fileStatusError"
                class="lucide-icon-btn file-status-retry"
                @click="loadFileBuildStatus"
              >
                <RefreshCw :size="16" />
                文件构建状态加载失败，重试
              </a-button>
              <div v-if="isMilvus && !readonly" class="actions-right">
                <a-button
                  v-if="isMilvus && !readonly"
                  class="action-btn index-action-btn"
                  :class="{ 'has-index-label': hasPendingGraphChunks }"
                  @click="toggleBuildPanel"
                  :title="graphIndexButtonTitle"
                  :aria-label="graphIndexButtonTitle"
                >
                  <Database :size="16" />
                  <span v-if="hasPendingGraphChunks" class="index-status-label"
                    >{{ pendingGraphChunks }} 待索引</span
                  >
                  <span
                    v-if="graphIndexDotStatus"
                    class="status-dot"
                    :class="`status-dot--${graphIndexDotStatus}`"
                  ></span>
                </a-button>
              </div>
            </div>
          </template>
        </GraphCanvas>
        <ResourceEmptyState
          v-if="graphError && !graph.fetching"
          class="graph-empty-state"
          title="加载图谱失败"
          description="请检查网络或稍后重试。"
          :icon="Network"
          full-height
        >
          <template #actions>
            <a-button class="lucide-icon-btn" @click="loadGraph">
              <RefreshCw :size="16" />
              重试
            </a-button>
          </template>
        </ResourceEmptyState>
        <ResourceEmptyState
          v-else-if="showGraphConfigEmpty"
          class="graph-empty-state"
          title="暂无知识图谱"
          :description="
            readonly
              ? '图谱尚未构建，请等待知识库管理员配置抽取器并索引后查看。'
              : '请先配置抽取器，再构建全库图谱以生成实体与关系。'
          "
          :icon="Network"
          full-height
        >
          <template v-if="!readonly" #actions>
            <a-button type="primary" class="lucide-icon-btn" @click="openGraphConfig">
              <Settings :size="16" />
              配置抽取器
            </a-button>
          </template>
        </ResourceEmptyState>
        <ResourceEmptyState
          v-else-if="showGraphDataEmpty"
          class="graph-empty-state"
          :title="graphDataEmptyTitle"
          :description="graphDataEmptyDescription"
          :icon="Network"
          full-height
        >
          <template #actions>
            <a-button v-if="searchInput.trim()" class="lucide-icon-btn" @click="clearGraphSearch">
              <Search :size="16" />
              清空搜索
            </a-button>
            <a-button
              v-else-if="!fileId && !readonly && hasPendingGraphChunks && !isBuildActive"
              type="primary"
              class="lucide-icon-btn"
              :loading="graphBuildSubmitting"
              @click="startGraphBuild()"
            >
              <Database :size="16" />
              构建全库图谱
            </a-button>
            <a-button v-else class="lucide-icon-btn" @click="loadGraph">
              <RefreshCw :size="16" :class="{ spin: graph.fetching }" />
              刷新图谱
            </a-button>
          </template>
        </ResourceEmptyState>

        <!-- 详情浮动卡片 -->
        <GraphDetailPanel
          :visible="graph.showDetailDrawer"
          :item="graph.selectedItem"
          :type="graph.selectedItemType"
          @close="graph.handleCanvasClick"
        />

        <!-- 设置浮动面板 -->
        <transition name="settings-expand">
          <div v-if="showSettings" class="floating-panel settings-panel">
            <div class="panel-header">
              <span class="panel-title">图谱展示设置</span>
            </div>
            <div class="panel-body">
              <a-form layout="vertical">
                <a-form-item label="最大节点数 (limit)">
                  <a-input-number
                    v-model:value="subgraphParams.maxNodes"
                    :min="10"
                    :max="1000"
                    :step="10"
                    style="width: 100%"
                  />
                </a-form-item>
                <a-form-item label="搜索深度 (depth)">
                  <a-input-number
                    v-model:value="subgraphParams.maxDepth"
                    :min="1"
                    :max="5"
                    :step="1"
                    style="width: 100%"
                  />
                </a-form-item>
                <div class="settings-exclude-row">
                  <span class="settings-field-label">排除 Chunk 节点</span>
                  <a-switch v-model:checked="subgraphParams.excludeChunk" />
                </div>
                <div class="settings-actions">
                  <a-button @click="loadGraph" :disabled="graph.fetching">
                    <RefreshCw :size="14" :class="{ spin: graph.fetching }" />
                    刷新
                  </a-button>
                  <a-button type="primary" @click="applySettings">应用</a-button>
                </div>
              </a-form>
            </div>
          </div>
        </transition>

        <!-- 索引管理浮动面板 -->
        <transition name="slide-fade">
          <div v-if="isMilvus && !readonly && showBuildPanel" class="floating-panel build-panel">
            <div class="panel-header">
              <span class="panel-title">全库索引管理</span>
              <a-button
                v-if="graphBuildStatus?.locked && !graphBuildStatusError && !isBuildActive"
                size="small"
                type="text"
                title="修改配置"
                aria-label="修改配置"
                class="panel-config-btn"
                @click="openGraphConfig"
              >
                <Settings :size="14" />
              </a-button>
            </div>
            <div v-if="graphBuildStatusError" class="panel-body">
              <p>图谱构建状态加载失败。</p>
              <a-button :loading="graphBuildLoading" @click="loadGraphBuildStatus">重试</a-button>
            </div>
            <div v-else-if="graphBuildStatus" class="panel-body">
              <a-progress
                v-if="isBuildActive"
                :percent="graphBuildStatus?.build_job_progress ?? 0"
                :stroke-color="{ '0%': 'var(--main-400)', '100%': 'var(--main-700)' }"
                size="small"
                style="margin-bottom: 10px"
              />
              <div class="stats-grid">
                <div class="stat-item">
                  <span class="stat-value">{{ graphBuildStatus?.total_chunks ?? '-' }}</span>
                  <span class="stat-label">总 Chunk</span>
                </div>
                <div class="stat-item">
                  <span class="stat-value">{{ graphBuildStatus?.pending_chunks ?? '-' }}</span>
                  <span class="stat-label">待构建</span>
                </div>
                <div class="stat-item">
                  <span class="stat-value">{{ graphBuildStatus?.indexed_chunks ?? '-' }}</span>
                  <span class="stat-label">已构建</span>
                </div>
                <div class="stat-item">
                  <span class="stat-value">{{ graphBuildStatus?.structured_chunks ?? '-' }}</span>
                  <span class="stat-label">结构完成</span>
                </div>
                <div
                  class="stat-item"
                  :class="{ 'is-clickable': extractionFailedCount > 0 }"
                  :role="extractionFailedCount > 0 ? 'button' : undefined"
                  :tabindex="extractionFailedCount > 0 ? 0 : undefined"
                  @click="openFailedChunkSamples"
                  @keydown.enter.prevent="openFailedChunkSamples"
                  @keydown.space.prevent="openFailedChunkSamples"
                >
                  <span class="stat-value">{{ extractionFailedCount }}</span>
                  <span class="stat-label">抽取失败</span>
                </div>
                <div class="stat-item">
                  <span class="stat-value">{{ vectorPendingCount }}</span>
                  <span class="stat-label">向量待处理</span>
                </div>
                <div class="stat-item">
                  <span class="stat-value">{{ vectorFailedCount }}</span>
                  <span class="stat-label">向量失败</span>
                </div>
                <div class="stat-item">
                  <span class="stat-value">{{ graphBuildStatus?.entity_count ?? '-' }}</span>
                  <span class="stat-label">实体</span>
                </div>
                <div class="stat-item">
                  <span class="stat-value">{{ graphBuildStatus?.relationship_count ?? '-' }}</span>
                  <span class="stat-label">关系</span>
                </div>
              </div>
              <div class="build-actions">
                <a-button
                  v-if="!graphBuildStatus?.locked"
                  type="primary"
                  block
                  @click="openGraphConfig"
                >
                  配置抽取器
                </a-button>
                <a-button v-else-if="isBuildActive" type="primary" block disabled>
                  构建中 {{ graphBuildStatus?.build_job_progress ?? 0 }}%
                </a-button>
                <a-button
                  v-else-if="isBuildFailed"
                  type="primary"
                  block
                  :disabled="!graphBuildStatus?.pending_chunks"
                  @click="vectorFailedCount ? retryGraphVectors() : startGraphBuild()"
                >
                  {{ vectorFailedCount ? '重试全库失败向量' : '重试全库索引' }}
                </a-button>
                <a-button
                  v-else
                  type="primary"
                  block
                  :disabled="!graphBuildStatus?.pending_chunks"
                  @click="startGraphBuild()"
                >
                  构建全库图谱
                </a-button>
              </div>
            </div>
            <div v-else class="panel-body">正在加载图谱构建状态…</div>
          </div>
        </transition>
      </div>
    </div>

    <FileSearchModal v-model:open="showFileSearch" :kb-id="kbId" @select="selectFile" />

    <a-modal
      v-model:open="showGraphConfig"
      :title="graphConfigTitle"
      width="640px"
      @cancel="showGraphConfig = false"
    >
      <a-form layout="vertical">
        <div class="form-grid model-config-grid">
          <a-form-item label="模型">
            <ModelSelectorComponent
              :model_spec="graphConfigForm.model_spec"
              placeholder="选择抽取模型"
              @select-model="(spec) => (graphConfigForm.model_spec = spec)"
            />
          </a-form-item>
          <a-form-item label="LLM 抽取并发数">
            <a-input-number
              v-model:value="graphConfigForm.concurrency_count"
              :min="1"
              :max="1000"
              :step="1"
              style="width: 100%"
            />
          </a-form-item>
        </div>
        <a-form-item label="Schema">
          <div v-if="isEditingGraphConfig" class="schema-description">
            修改配置仅影响后续构建；已构建的图谱不会自动重算，如需一致请重置后重新抽取。抽取器类型创建后不可修改。
          </div>
          <a-textarea
            v-model:value="graphConfigForm.schema"
            :rows="6"
            placeholder="描述实体类型、关系类型和属性约束。后端会把 Schema 拼接到固定抽取 Prompt 中。"
          />
        </a-form-item>
        <a-form-item label="单次抽取超时（秒）">
          <a-input-number
            v-model:value="graphConfigForm.timeout_seconds"
            :min="1"
            :max="600"
            :step="10"
            style="width: 100%"
          />
          <div class="form-item-hint">
            默认 60 秒。推理型抽取模型单块耗时可达数十秒，若日志出现反复超时重试，可调大至 180–300。
          </div>
        </a-form-item>
        <a-form-item label="模型参数 JSON">
          <a-input
            v-model:value="graphConfigForm.model_params_text"
            placeholder='例如 {"temperature":0.1}'
          />
          <div class="form-item-hint">
            输入的 JSON 对象会作为 model_params 传给抽取模型调用；如需设置超时，请使用上方字段。关闭百炼等模型的思考模式需写在
            extra_body 中，例如 {"extra_body":{"enable_thinking":false}}。
          </div>
        </a-form-item>
      </a-form>
      <template #footer>
        <div class="graph-config-footer">
          <a-button v-if="isEditingGraphConfig" danger @click="confirmResetGraph">重置</a-button>
          <div class="graph-config-footer-actions">
            <a-button @click="showGraphConfig = false">取消</a-button>
            <a-button type="primary" @click="configureGraphBuild">确认</a-button>
          </div>
        </div>
      </template>
    </a-modal>

    <a-modal v-model:open="showFailedChunkSamples" title="样例 Chunk" width="760px" :footer="null">
      <div v-if="failedChunkSamplesLoading" class="failed-chunk-loading">
        <Loader2 :size="18" class="spin" />
        正在加载失败 Chunk
      </div>
      <a-empty v-else-if="!failedChunkSamples.length" description="暂无抽取失败的 Chunk" />
      <a-tabs v-else v-model:activeKey="activeFailedChunkKey" class="failed-chunk-tabs">
        <a-tab-pane
          v-for="(chunk, index) in failedChunkSamples"
          :key="chunk.chunk_id"
          :tab="`样例 ${index + 1}`"
        >
          <div class="failed-chunk-meta">
            <span>Chunk {{ Number(chunk.chunk_index) + 1 }}</span>
            <span>{{ chunk.file_id }}</span>
            <span>尝试 {{ chunk.details?.attempt_count ?? '-' }} 次</span>
          </div>
          <a-alert
            type="error"
            :message="chunk.details?.last_error || '未记录失败原因'"
            show-icon
          />
          <pre class="failed-chunk-content">{{ chunk.content }}</pre>
        </a-tab-pane>
      </a-tabs>
    </a-modal>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onUnmounted, reactive } from 'vue'
import { useKnowledgeBaseStore } from '@/modules/knowledge/model/knowledgeBase'
import { useBackgroundJobsStore } from '@/modules/background-jobs'
import { useConfigStore } from '@/modules/settings'
import {
  RefreshCw,
  Settings,
  Search,
  Loader2,
  Database,
  Network,
  FileText,
  X
} from '@lucide/vue'
import GraphCanvas from '@/modules/knowledge/ui/GraphCanvas.vue'
import FileSearchModal from '@/modules/knowledge/ui/FileSearchModal.vue'
import GraphDetailPanel from '@/modules/knowledge/ui/GraphDetailPanel.vue'
import ResourceEmptyState from '@/shared/ui/ResourceEmptyState.vue'
import { getKbTypeLabel } from '@/modules/knowledge/model/kb_utils'
import { graphApi } from '@/apis/graph_api'
import { documentApi, graphBuildApi } from '@/apis/knowledge_api'
import { Modal, message } from 'ant-design-vue'
import ModelSelectorComponent from '@/modules/agents/ui/ModelSelectorComponent.vue'
import { useGraph } from '@/modules/knowledge/model/useGraph'

const MILVUS_KB_TYPE = 'milvus'
const GRAPH_SUPPORTED_KB_TYPES = new Set([MILVUS_KB_TYPE])

const props = defineProps({
  active: {
    type: Boolean,
    default: false
  },
  readonly: {
    type: Boolean,
    default: false
  },
  fileId: {
    type: String,
    default: null
  }
})

const emit = defineEmits(['file-change'])

const store = useKnowledgeBaseStore()
const jobsStore = useBackgroundJobsStore()
const configStore = useConfigStore()

const kbId = computed(() => store.kbId)
const kbType = computed(() => store.knowledgeBase.kb_type)
const kbTypeLabel = computed(() => getKbTypeLabel(kbType.value || 'milvus'))
const isMilvus = computed(() => kbType.value?.toLowerCase() === MILVUS_KB_TYPE)

const graphRef = ref(null)
const showSettings = ref(false)
const showBuildPanel = ref(false)
const subgraphParams = reactive({
  maxNodes: 100,
  maxDepth: 2,
  excludeChunk: true
})
const searchInput = ref('')
const showFileSearch = ref(false)
const selectedFile = ref(null)
const selectedFileLabel = computed(() =>
  props.fileId
    ? selectedFile.value?.filename || selectedFile.value?.original_filename || '已选择文件'
    : '按文件查看'
)
const graphError = ref(false)
const graphBuildStatus = ref(null)
const graphBuildLoading = ref(false)
const graphBuildStatusError = ref(false)
const graphBuildSubmitting = ref(false)
const awaitingBuildStatus = ref(false)
const filePendingChunks = ref(null)
const fileStatusError = ref(false)
const showGraphConfig = ref(false)
const showFailedChunkSamples = ref(false)
const failedChunkSamplesLoading = ref(false)
const failedChunkSamples = ref([])
const activeFailedChunkKey = ref('')
let buildStatusPollTimer = null
let buildStatusPollGeneration = 0
let graphStatusInFlight = null
let graphSectionDisposed = false

const isBuildActive = computed(() => {
  const s = graphBuildStatus.value?.build_job_status
  return s === 'pending' || s === 'running'
})

const isBuildFailed = computed(() => {
  return graphBuildStatus.value?.build_job_status === 'failed'
})

const pendingGraphChunks = computed(() => {
  return Number(graphBuildStatus.value?.pending_chunks ?? 0)
})

const hasPendingGraphChunks = computed(() => pendingGraphChunks.value > 0)
const extractionFailedCount = computed(() =>
  Number(graphBuildStatus.value?.extraction_counts?.failed || 0)
)
const vectorPendingCount = computed(() => {
  const counts = graphBuildStatus.value?.vector_counts || {}
  return Number(counts.pending || 0) + Number(counts.processing || 0)
})
const vectorFailedCount = computed(() => Number(graphBuildStatus.value?.vector_counts?.failed || 0))

const isGraphIndexComplete = computed(() => {
  return (
    Boolean(graphBuildStatus.value?.locked) &&
    !isBuildActive.value &&
    pendingGraphChunks.value === 0
  )
})

const graphIndexDotStatus = computed(() => {
  if (isBuildActive.value) return 'active'
  if (hasPendingGraphChunks.value) return 'pending'
  if (isGraphIndexComplete.value) return 'complete'
  return ''
})

const graphIndexButtonTitle = computed(() => {
  if (hasPendingGraphChunks.value) return `全库索引管理，${pendingGraphChunks.value} 待索引`
  if (isGraphIndexComplete.value) return '全库索引管理，已全部索引'
  if (isBuildActive.value) return '全库索引管理，索引中'
  return '全库索引管理'
})

const toggleBuildPanel = () => {
  showBuildPanel.value = !showBuildPanel.value
  showSettings.value = false
  if (showBuildPanel.value) graph.handleCanvasClick()
}

const toggleSettingsPanel = () => {
  showSettings.value = !showSettings.value
  showBuildPanel.value = false
  if (showSettings.value) graph.handleCanvasClick()
}

const handleGraphNodeClick = (nodeData) => {
  showSettings.value = false
  showBuildPanel.value = false
  graph.handleNodeClick(nodeData)
}

const handleGraphEdgeClick = (edgeData) => {
  showSettings.value = false
  showBuildPanel.value = false
  graph.handleEdgeClick(edgeData)
}

const isEditingGraphConfig = computed(() => Boolean(graphBuildStatus.value?.locked))

const graphConfigTitle = computed(() =>
  isEditingGraphConfig.value ? '修改图谱抽取配置' : '图谱展示配置'
)

const stopBuildStatusPoll = () => {
  buildStatusPollGeneration++
  if (buildStatusPollTimer) {
    clearTimeout(buildStatusPollTimer)
    buildStatusPollTimer = null
  }
}

const startBuildStatusPoll = () => {
  stopBuildStatusPoll()
  const generation = buildStatusPollGeneration
  const schedule = () => {
    buildStatusPollTimer = setTimeout(async () => {
      buildStatusPollTimer = null
      if (graphStatusInFlight) {
        await graphStatusInFlight.catch(() => {})
      } else {
        await loadGraphBuildStatus()
      }
      if (generation === buildStatusPollGeneration) schedule()
    }, 5000)
  }
  schedule()
}

watch(
  () => props.active && (isBuildActive.value || awaitingBuildStatus.value),
  (polling, wasPolling) => {
    if (polling) {
      startBuildStatusPoll()
    } else {
      stopBuildStatusPoll()
      if (wasPolling && props.active && !graphSectionDisposed) {
        loadGraph()
        loadFileBuildStatus()
      }
    }
  },
  { immediate: true, flush: 'sync' }
)
const DEFAULT_EXTRACTION_TIMEOUT_SECONDS = 60
const graphConfigForm = reactive({
  model_spec: '',
  schema: '',
  concurrency_count: 50,
  timeout_seconds: DEFAULT_EXTRACTION_TIMEOUT_SECONDS,
  model_params_text: ''
})

const graph = reactive(useGraph(graphRef))
const graphLoaded = ref(false)

// 计算属性：是否支持知识图谱
const isGraphSupported = computed(() => GRAPH_SUPPORTED_KB_TYPES.has(kbType.value?.toLowerCase()))
const hasGraphNodes = computed(() => graph.graphData.nodes.length > 0)
const showGraphConfigEmpty = computed(
  () => !props.fileId && !props.readonly && isMilvus.value && Boolean(graphBuildStatus.value) && !graphBuildStatus.value.locked && !graphBuildLoading.value && !graphBuildStatusError.value && !graphError.value
)
const showGraphDataEmpty = computed(
  () =>
    isMilvus.value &&
    (props.readonly || Boolean(graphBuildStatus.value?.locked) || Boolean(props.fileId)) &&
    !graphError.value &&
    graphLoaded.value &&
    !graph.fetching &&
    !hasGraphNodes.value
)
const graphDataEmptyTitle = computed(() =>
  searchInput.value.trim() ? '未找到匹配实体' : props.fileId ? '该文件暂无知识图谱' : '暂无知识图谱'
)
const graphDataEmptyDescription = computed(() => {
  if (searchInput.value.trim()) return '换个关键词或调整图谱设置后再搜索。'
  if (props.fileId) {
    if (!props.readonly && graphBuildStatus.value && !graphBuildStatus.value.locked) return '请先配置抽取器，再构建此文件的图谱。'
    return '该文件没有可展示的实体与关系。'
  }
  if (isBuildActive.value) return '图谱索引正在运行，完成后会展示实体与关系。'
  if (hasPendingGraphChunks.value) return '当前还有待索引 Chunk，完成索引后会展示实体与关系。'
  return '当前知识库还没有可展示的实体与关系。'
})

let pendingLoadTimer = null
let graphStatusRequestSeq = 0
let graphLoadRequestSeq = 0
let fileInfoRequestSeq = 0
let fileStatusRequestSeq = 0

/** 读取当前文件待构建分块，丢弃切换文件后的旧结果。 */
const loadFileBuildStatus = async () => {
  const requestSeq = ++fileStatusRequestSeq
  const fileId = props.fileId
  const currentKnowledgeBaseId = kbId.value
  filePendingChunks.value = null
  fileStatusError.value = false
  if (!fileId || !currentKnowledgeBaseId || props.readonly || !isMilvus.value) return
  try {
    const status = await graphBuildApi.getFileStatus(currentKnowledgeBaseId, fileId)
    if (requestSeq === fileStatusRequestSeq && currentKnowledgeBaseId === kbId.value && fileId === props.fileId && !graphSectionDisposed) {
      filePendingChunks.value = status.pending_chunks
    }
  } catch (error) {
    if (requestSeq === fileStatusRequestSeq && currentKnowledgeBaseId === kbId.value && fileId === props.fileId && !graphSectionDisposed) {
      console.error('Failed to load file graph build status:', error)
      fileStatusError.value = true
    }
  }
}

const getErrorDetail = (e, fallback) => {
  return e?.response?.data?.detail || e?.response?.data?.message || e?.message || fallback
}

const loadGraphBuildStatus = async () => {
  if (!kbId.value || !isMilvus.value || props.readonly) return
  const requestSeq = ++graphStatusRequestSeq
  const currentKnowledgeBaseId = kbId.value
  graphBuildLoading.value = true
  let request
  try {
    request = graphBuildApi.getStatus(currentKnowledgeBaseId)
    graphStatusInFlight = request
    const status = await request
    if (requestSeq === graphStatusRequestSeq && currentKnowledgeBaseId === kbId.value) {
      graphBuildStatus.value = status
      graphBuildStatusError.value = false
      awaitingBuildStatus.value = false
    }
  } catch (e) {
    if (requestSeq === graphStatusRequestSeq && currentKnowledgeBaseId === kbId.value && !graphSectionDisposed) {
      console.error('Failed to load graph build status:', e)
      graphBuildStatusError.value = true
      message.error('加载图谱构建状态失败')
    }
  } finally {
    if (graphStatusInFlight === request) graphStatusInFlight = null
    if (requestSeq === graphStatusRequestSeq) {
      graphBuildLoading.value = false
    }
  }
}

const parseModelParams = () => {
  const text = graphConfigForm.model_params_text.trim()
  if (!text) return {}
  let params
  try {
    params = JSON.parse(text)
  } catch {
    throw new Error('模型参数必须是合法 JSON 对象')
  }
  if (!params || Array.isArray(params) || typeof params !== 'object') {
    throw new Error('模型参数必须是 JSON 对象')
  }
  return params
}

const fillGraphConfigForm = () => {
  const config = graphBuildStatus.value?.config
  const options = config?.extractor_options || {}
  graphConfigForm.model_spec = options.model_spec || configStore.config?.default_model || ''
  graphConfigForm.schema = options.schema || ''
  graphConfigForm.concurrency_count = Number(options.concurrency_count || 50)
  graphConfigForm.timeout_seconds = Number(options.timeout_seconds || DEFAULT_EXTRACTION_TIMEOUT_SECONDS)
  graphConfigForm.model_params_text = options.model_params
    ? JSON.stringify(options.model_params)
    : ''
}

const openGraphConfig = () => {
  fillGraphConfigForm()
  showGraphConfig.value = true
}

const buildExtractorOptions = () => {
  return {
    model_spec: graphConfigForm.model_spec,
    schema: graphConfigForm.schema.trim(),
    concurrency_count: graphConfigForm.concurrency_count || 50,
    timeout_seconds: graphConfigForm.timeout_seconds || DEFAULT_EXTRACTION_TIMEOUT_SECONDS,
    model_params: parseModelParams()
  }
}

const configureGraphBuild = async () => {
  try {
    document.activeElement?.blur()
    await nextTick()
    await graphBuildApi.configure(kbId.value, {
      extractor_type: 'llm',
      extractor_options: buildExtractorOptions()
    })
    message.success(isEditingGraphConfig.value ? '图谱抽取配置已更新' : '图谱抽取配置已保存')
    showGraphConfig.value = false
    await loadGraphBuildStatus()
  } catch (e) {
    console.error('Failed to configure graph build:', e)
    message.error(getErrorDetail(e, '配置图谱抽取失败'))
  }
}

const startGraphBuild = async (fileId = null) => {
  if (graphBuildSubmitting.value) return
  graphBuildSubmitting.value = true
  const registerJob = jobsStore.createJobRegistration()
  const currentKnowledgeBaseId = kbId.value
  try {
    const data = await graphBuildApi.startIndex(currentKnowledgeBaseId, fileId)
    message.success(data.message || '图谱构建任务已提交')
    if (data.job_id) {
      registerJob({ job_id: data.job_id })
    }
    if (!graphSectionDisposed && props.active && currentKnowledgeBaseId === kbId.value) {
      awaitingBuildStatus.value = true
      await loadGraphBuildStatus()
    }
  } catch (e) {
    console.error('Failed to start graph build:', e)
    message.error(getErrorDetail(e, '提交图谱构建任务失败'))
  } finally {
    graphBuildSubmitting.value = false
  }
}

const retryGraphVectors = async () => {
  const registerJob = jobsStore.createJobRegistration()
  try {
    const data = await graphBuildApi.reconcile(kbId.value, 'failed')
    message.success(data.message || '图谱向量索引修复任务已提交')
    if (data.job_id) {
      registerJob({ job_id: data.job_id })
    }
    await loadGraphBuildStatus()
  } catch (e) {
    console.error('Failed to reconcile graph vectors:', e)
    message.error(getErrorDetail(e, '提交图谱向量索引修复任务失败'))
  }
}

const openFailedChunkSamples = async () => {
  if (!extractionFailedCount.value || failedChunkSamplesLoading.value) return
  showFailedChunkSamples.value = true
  failedChunkSamplesLoading.value = true
  failedChunkSamples.value = []
  activeFailedChunkKey.value = ''
  try {
    const data = await graphBuildApi.getFailedChunks(kbId.value, 10)
    failedChunkSamples.value = data.samples || []
    activeFailedChunkKey.value = failedChunkSamples.value[0]?.chunk_id || ''
  } catch (e) {
    console.error('Failed to load graph extraction failed chunks:', e)
    message.error(getErrorDetail(e, '加载抽取失败 Chunk 失败'))
  } finally {
    failedChunkSamplesLoading.value = false
  }
}

const confirmResetGraph = () => {
  Modal.confirm({
    title: '清空并重建图谱',
    content: '将删除该知识库在 Neo4j 中的图谱，重置 Chunk 图谱状态，并清空抽取结果与配置。',
    okText: '确认重置',
    cancelText: '取消',
    onOk: resetGraphBuild
  })
}

const resetGraphBuild = async () => {
  try {
    await graphBuildApi.reset(kbId.value, {
      clear_extraction_result: true,
      clear_config: true
    })
    message.success('图谱构建状态已重置')
    showGraphConfig.value = false
    graphLoaded.value = false
    graph.clearGraph()
    await loadGraphBuildStatus()
  } catch (e) {
    console.error('Failed to reset graph build:', e)
    message.error(getErrorDetail(e, '重置图谱构建状态失败'))
  }
}

const loadGraph = async () => {
  if (!kbId.value || !isGraphSupported.value) return

  const requestSeq = ++graphLoadRequestSeq
  const currentKnowledgeBaseId = kbId.value
  const currentFileId = props.fileId
  graphError.value = false
  graph.fetching = true
  if (!hasGraphNodes.value) {
    graphLoaded.value = false
  }
  try {
    const res = await graphApi.getSubgraph({
      kb_id: currentKnowledgeBaseId,
      file_id: currentFileId,
      node_label: searchInput.value || '*',
      max_nodes: subgraphParams.maxNodes,
      max_depth: subgraphParams.maxDepth,
      exclude_chunk: subgraphParams.excludeChunk
    })

    if (
      requestSeq === graphLoadRequestSeq &&
      currentKnowledgeBaseId === kbId.value &&
      currentFileId === props.fileId
    ) {
      if (!res.success || !res.data) throw new Error('加载图谱失败')
      graph.updateGraphData(res.data.nodes, res.data.edges)
    }
  } catch (e) {
    if (requestSeq === graphLoadRequestSeq && currentKnowledgeBaseId === kbId.value && currentFileId === props.fileId) {
      console.error('Failed to load graph:', e)
      graph.clearGraph()
      graphError.value = true
    }
  } finally {
    if (requestSeq === graphLoadRequestSeq && currentKnowledgeBaseId === kbId.value && currentFileId === props.fileId) {
      graph.fetching = false
      graphLoaded.value = true
    }
  }
}

const selectFile = (file) => {
  selectedFile.value = file
  emit('file-change', file.file_id)
}

const clearFileFilter = () => emit('file-change', null)

const applySettings = () => {
  showSettings.value = false
  loadGraph()
}

const onSearch = () => {
  loadGraph()
}

const clearGraphSearch = () => {
  searchInput.value = ''
  loadGraph()
}

const scheduleGraphLoad = (delay = 200) => {
  if (!props.active || !isGraphSupported.value || !kbId.value) {
    return
  }

  if (pendingLoadTimer) {
    clearTimeout(pendingLoadTimer)
  }
  pendingLoadTimer = setTimeout(async () => {
    pendingLoadTimer = null
    await nextTick()
    if (props.active && isGraphSupported.value && kbId.value) {
      await loadGraph()
    }
  }, delay)
}

watch(
  [() => props.fileId, kbId],
  async ([fileId, currentKnowledgeBaseId], previous) => {
    graphLoadRequestSeq++
    graph.fetching = false
    graphLoaded.value = false
    graphError.value = false
    graph.clearGraph()
    const requestSeq = ++fileInfoRequestSeq
    scheduleGraphLoad(0)
    loadFileBuildStatus()
    if (previous && previous[1] !== currentKnowledgeBaseId) selectedFile.value = null
    if (!fileId) {
      selectedFile.value = null
    } else if (selectedFile.value?.file_id !== fileId) {
      selectedFile.value = null
      try {
        const response = await documentApi.getDocumentBasicInfo(currentKnowledgeBaseId, fileId)
        if (requestSeq === fileInfoRequestSeq) selectedFile.value = response?.meta || response
      } catch {
        // 文件名不可用时仍保留文件范围，图谱请求负责报告错误。
      }
    }
  },
  { immediate: true }
)

watch(
  () => props.active,
  (active) => {
    if (active) {
      if (isMilvus.value) {
        loadGraphBuildStatus()
        loadFileBuildStatus()
      }
      scheduleGraphLoad()
    } else {
      awaitingBuildStatus.value = false
      graphStatusRequestSeq++
      fileStatusRequestSeq++
      graphStatusInFlight = null
      graphBuildLoading.value = false
    }
  },
  { immediate: true }
)

watch(kbId, () => {
  awaitingBuildStatus.value = false
  graphStatusRequestSeq += 1
  graphStatusInFlight = null
  graphBuildLoading.value = false
  graphLoadRequestSeq += 1
  graphLoaded.value = false
  graph.clearGraph()
  graphBuildStatus.value = null
  graphBuildStatusError.value = false
  showFailedChunkSamples.value = false
  failedChunkSamples.value = []
  if (isMilvus.value) {
    loadGraphBuildStatus()
  }
  if (isGraphSupported.value) {
    scheduleGraphLoad(300)
  }
})

watch(isGraphSupported, (supported) => {
  if (!supported) {
    awaitingBuildStatus.value = false
    graphStatusRequestSeq++
    graphStatusInFlight = null
    graphBuildLoading.value = false
    graphLoadRequestSeq++
    graph.fetching = false
    graphLoaded.value = false
    graph.clearGraph()
    graphBuildStatus.value = null
    graphBuildStatusError.value = false
    return
  }
  if (isMilvus.value) {
    loadGraphBuildStatus()
    loadFileBuildStatus()
  }
  scheduleGraphLoad(200)
})

onUnmounted(() => {
  graphSectionDisposed = true
  graphStatusRequestSeq++
  awaitingBuildStatus.value = false
  if (pendingLoadTimer) {
    clearTimeout(pendingLoadTimer)
    pendingLoadTimer = null
  }
  graphLoadRequestSeq++
  fileInfoRequestSeq++
  fileStatusRequestSeq++
  stopBuildStatusPoll()
})
</script>

<style scoped lang="less">
.graph-section {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
  user-select: none;
}

.graph-container-compact {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  position: relative;
}

.graph-wrapper {
  height: 100%;
  width: 100%;
  position: relative;
  --graph-search-input-width: 240px;
  --graph-search-toolbar-width: calc(var(--graph-search-input-width) + 78px);
}

.graph-empty-state {
  position: absolute;
  inset: 0;
  z-index: 30;
  pointer-events: none;

  :deep(.resource-empty-state__actions) {
    pointer-events: auto;
  }
}

.compact-actions {
  position: absolute;
  top: 10px;
  left: 10px;
  right: 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  pointer-events: none; /* Let clicks pass through empty areas */

  .file-build-action {
    pointer-events: auto;
  }

  .actions-left,
  .actions-right,
  .file-filter,
  .file-status-retry,
  .build-status-retry {
    pointer-events: auto; /* Re-enable clicks for buttons/inputs */
    display: flex;
    align-items: center;
    gap: 0;
    background: var(--color-trans-light);
    backdrop-filter: blur(12px);
    padding: 2px;
    border-radius: 8px;
    box-shadow: 0 0 4px 0px var(--shadow-2);
    border: 1px solid var(--gray-100);
  }

  :deep(.ant-input-affix-wrapper) {
    padding: 4px 11px;
    border-radius: 6px;
    border-color: transparent;
    box-shadow: none;
    background: var(--color-trans-light);

    &:hover,
    &:focus,
    &-focused {
      background: var(--main-0);
      border-color: var(--main-color);
    }

    input {
      background: transparent;
    }
  }

  .graph-search-input {
    flex: 0 0 var(--graph-search-input-width);
    width: var(--graph-search-input-width);
    border-radius: 0;
  }

  .search-action-btn {
    border-radius: 6px 0 0 6px;
  }

  .settings-action-btn {
    border-radius: 0 6px 6px 0;
  }

  .action-btn {
    width: 32px;
    height: 32px;
    padding: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    border: none;
    background: transparent;
    color: var(--gray-600);
    border-radius: 6px;
    box-shadow: none;
    position: relative;

    &:hover {
      background: var(--dark-5);
      color: var(--main-color);
    }
  }

  .file-filter {
    margin-right: auto;
    min-width: 0;
  }

  .file-filter-button {
    display: flex;
    align-items: center;
    gap: 6px;
    max-width: 200px;
    border: 0;
    box-shadow: none;
    background: transparent;
    color: var(--gray-700);

    span {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .file-filter-clear {
    width: 30px;
    padding: 0;
    border: 0;
    box-shadow: none;
    background: transparent;
    color: var(--gray-600);
  }

  .index-action-btn {
    gap: 6px;
    overflow: visible;

    &.has-index-label {
      width: auto;
      min-width: 84px;
      padding: 0 22px 0 8px;
      justify-content: flex-start;
    }

    .index-status-label {
      font-size: 12px;
      line-height: 1;
      color: var(--gray-700);
      white-space: nowrap;
    }
  }

  .status-dot {
    position: absolute;
    bottom: 4px;
    right: 4px;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    box-shadow: 0 0 0 1px var(--color-trans-light);
  }

  .status-dot--pending {
    background: var(--color-warning-500);
  }

  .status-dot--active {
    background: var(--color-warning-500);
    animation: blink 1.2s ease-in-out infinite;
  }

  .status-dot--complete {
    background: var(--color-success-500);
  }

  .spin {
    animation: spin 1s linear infinite;
  }
}

@keyframes blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.2;
  }
}

.graph-disabled {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
}

.disabled-content {
  text-align: center;
  color: var(--gray-400);

  h4 {
    margin-bottom: 8px;
  }
}

.floating-panel {
  position: absolute;
  top: 60px;
  right: 10px;
  width: 300px;
  max-height: calc(100% - 60px);
  overflow-y: auto;
  z-index: 100;
  background: var(--color-trans-light);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-radius: 8px;
  border: 1px solid var(--gray-100);
  box-shadow: 0 0 4px 0px var(--shadow-2);
  font-size: 13px;

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    border-bottom: 1px solid var(--gray-200);

    .panel-title {
      font-size: 13px;
      font-weight: 600;
      color: var(--gray-1000);
    }

    .panel-config-btn {
      padding: 2px 6px;
    }
  }

  .panel-body {
    padding: 10px 14px;
  }
}

.settings-panel {
  left: 10px;
  right: auto;
  width: min(var(--graph-search-toolbar-width), calc(100% - 20px));

  .panel-body {
    :deep(.ant-form-item) {
      margin-bottom: 12px;
    }
  }

  .settings-exclude-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-height: 32px;
    margin: 4px 0 14px;
    color: var(--gray-800);
  }

  .settings-field-label {
    font-size: 13px;
  }

  .settings-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;

    :deep(.ant-btn) {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
  }
}

.build-panel {
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    margin-bottom: 12px;
  }

  .stat-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 6px 4px;
    border-radius: 4px;
    background: var(--gray-0);
    outline: 1px solid var(--gray-100);

    &.is-clickable {
      cursor: pointer;
      border: 1px solid var(--color-error-100);

      &:hover,
      &:focus-visible {
        background: var(--color-error-50);
        outline: none;
      }

      .stat-value,
      .stat-label {
        color: var(--color-error-700);
      }
    }

    .stat-value {
      font-size: 15px;
      font-weight: 600;
      color: var(--gray-1000);
      line-height: 1.2;
    }

    .stat-label {
      font-size: 11px;
      color: var(--gray-500);
      margin-top: 2px;
    }
  }

  .build-actions {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
}

.failed-chunk-loading {
  min-height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--gray-600);
}

.failed-chunk-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  margin-bottom: 12px;
  color: var(--gray-600);
  font-size: 12px;
}

.failed-chunk-content {
  max-height: 360px;
  overflow: auto;
  margin: 12px 0 0;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-50);
  color: var(--gray-900);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.schema-description {
  margin-bottom: 8px;
  color: var(--gray-600);
  font-size: 13px;
  line-height: 1.5;
}

.form-grid.model-config-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 180px;
  gap: 12px;

  @media (max-width: 640px) {
    grid-template-columns: 1fr;
  }
}

.form-item-hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--gray-600);
}

.graph-config-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.graph-config-footer-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.settings-expand-enter-active,
.settings-expand-leave-active {
  transform-origin: top left;
  transition: transform 0.2s ease, opacity 0.2s ease;
}

.settings-expand-enter-from,
.settings-expand-leave-to {
  transform: translateY(-8px) scaleY(0.96);
  opacity: 0;
}

.slide-fade-enter-active {
  transition: all 0.25s ease-out;
}

.slide-fade-leave-active {
  transition: all 0.2s cubic-bezier(1, 0.5, 0.8, 1);
}

.slide-fade-enter-from,
.slide-fade-leave-to {
  transform: translateX(20px);
  opacity: 0;
}
</style>
