<template>
  <div class="thread-stats-wrapper">
    <div class="thread-filter-header">
      <div class="scope-note">
        <Info class="scope-icon" />
        统计不含已注销用户、已删除会话与已删除智能体；完整历史仍可在下方审计列表中检索。
      </div>

      <div class="header-controls">
        <div class="filter-group">
          <span class="filter-label">统计周期</span>
          <a-segmented
            v-model:value="timeRange"
            :options="timeRangeOptions"
            size="small"
            @change="loadData"
          />
        </div>

        <a-button type="default" size="small" :loading="loading" @click="loadData" class="refresh-btn">
          <template #icon><RefreshCw class="btn-icon" :class="{ spinning: loading }" /></template>
          刷新
        </a-button>
      </div>
    </div>

    <!-- 顶部核心指标 -->
    <div class="stats-overview-grid">
      <div class="stat-card primary">
        <div class="stat-icon">
          <MessageSquare class="icon" />
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ (threadData?.summary?.total_threads || 0).toLocaleString() }}</div>
          <div class="stat-label">累计会话总量</div>
          <div class="stat-subtag" v-if="threadData?.summary?.pinned_threads">
            置顶 {{ threadData.summary.pinned_threads }} 个
          </div>
        </div>
      </div>

      <div class="stat-card success">
        <div class="stat-icon">
          <Activity class="icon" />
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ (threadData?.summary?.active_threads || 0).toLocaleString() }}</div>
          <div class="stat-label">活跃会话数</div>
          <div class="stat-subtag">周期内有更新</div>
        </div>
      </div>

      <div class="stat-card info">
        <div class="stat-icon">
          <Layers class="icon" />
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ threadData?.summary?.avg_messages_per_thread || 0 }}</div>
          <div class="stat-label">平均对话轮数 / 会话</div>
          <div class="stat-subtag">
            总计 {{ (threadData?.summary?.total_messages || 0).toLocaleString() }} 条消息
          </div>
        </div>
      </div>

      <div class="stat-card warning">
        <div class="stat-icon">
          <Cpu class="icon" />
        </div>
        <div class="stat-content">
          <div class="stat-value">{{ formatTokenNumber(threadData?.summary?.avg_tokens_per_thread || 0) }}</div>
          <div class="stat-label">平均 Token 消耗 / 会话</div>
          <div class="stat-subtag">
            总消耗 {{ formatTokenNumber(threadData?.summary?.total_tokens || 0) }}
          </div>
        </div>
      </div>
    </div>

    <!-- 2x2 可视化图表区域 -->
    <div class="charts-2x2-grid">
      <!-- 1. 会话增长与活跃趋势 -->
      <div class="chart-card">
        <div class="card-header">
          <div class="card-title-group">
            <span class="card-title">会话增长与活跃趋势</span>
            <span class="card-desc">每日新增会话与活跃会话统计</span>
          </div>
        </div>
        <div class="chart-body">
          <div ref="trendChartRef" class="echart-box"></div>
        </div>
      </div>

      <!-- 2. 对话轮数深度分布 -->
      <div class="chart-card">
        <div class="card-header">
          <div class="card-title-group">
            <span class="card-title">对话轮数深度分布</span>
            <span class="card-desc">探索单次问答与多轮连续交互占比</span>
          </div>
        </div>
        <div class="chart-body">
          <div ref="depthChartRef" class="echart-box"></div>
        </div>
      </div>

      <!-- 3. 智能体会话分布与负载 -->
      <div class="chart-card">
        <div class="card-header">
          <div class="card-title-group">
            <span class="card-title">智能体会话承载排行</span>
            <span class="card-desc">各 Agent 累计会话数与平均轮数</span>
          </div>
        </div>
        <div class="chart-body">
          <div ref="agentChartRef" class="echart-box"></div>
        </div>
      </div>

      <!-- 4. 高频活跃用户榜 -->
      <div class="chart-card">
        <div class="card-header">
          <div class="card-title-group">
            <span class="card-title">高频用户活跃榜 TOP 10</span>
            <span class="card-desc">按会话数与消息交互量排序</span>
          </div>
        </div>
        <div class="chart-body table-body">
          <a-table
            :columns="userColumns"
            :data-source="threadData?.top_users || []"
            :pagination="false"
            size="small"
            :scroll="{ y: 220 }"
            row-key="uid"
          >
            <template #bodyCell="{ column, record, index }">
              <template v-if="column.key === 'rank'">
                <span class="user-rank" :class="{ top3: index < 3 }">{{ index + 1 }}</span>
              </template>
              <template v-if="column.key === 'user'">
                <div class="user-cell">
                  <FallbackAvatar
                    :src="record.avatar"
                    :name="record.username"
                    :seed="record.uid"
                    kind="user"
                    :size="24"
                    shape="circle"
                    decorative
                  />
                  <div class="user-cell-meta">
                    <span class="user-cell-name">{{ record.username || record.uid }}</span>
                    <span class="user-cell-uid" :title="record.uid">{{ record.uid }}</span>
                  </div>
                </div>
              </template>
              <template v-if="column.key === 'thread_count'">
                <span class="metric-num">{{ record.thread_count }}</span>
              </template>
              <template v-if="column.key === 'message_count'">
                <span class="metric-num font-medium">{{ record.message_count }}</span>
              </template>
            </template>
          </a-table>
        </div>
      </div>
    </div>

    <!-- 会话明细审计与探索表格 -->
    <div class="explorer-card">
      <div class="explorer-header">
        <div class="explorer-title-group">
          <span class="explorer-title">全平台会话审计</span>
          <span class="explorer-subtitle">保留已删除会话、已注销用户与已删除智能体的历史记录</span>
        </div>

        <div class="explorer-actions">
          <a-input
            v-model:value="searchKeyword"
            placeholder="搜索标题、Thread ID、用户名或 UID"
            allow-clear
            size="small"
            class="search-input"
            @pressEnter="handleSearch"
            @change="handleSearchChange"
          >
            <template #prefix><Search class="input-icon" /></template>
          </a-input>

          <a-select
            v-model:value="selectedStatus"
            aria-label="按会话状态筛选"
            size="small"
            class="filter-select status-filter"
            @change="handleSearch"
          >
            <a-select-option value="all">全部状态</a-select-option>
            <a-select-option value="active">进行中</a-select-option>
            <a-select-option value="archived">已归档</a-select-option>
            <a-select-option value="deleted">已删除</a-select-option>
            <a-select-option value="subagent">子智能体会话</a-select-option>
          </a-select>

          <a-select
            v-model:value="selectedAgentId"
            aria-label="按智能体筛选"
            allow-clear
            show-search
            option-filter-prop="label"
            placeholder="全部智能体"
            size="small"
            class="filter-select agent-filter"
            @change="handleSearch"
          >
            <a-select-option
              v-for="agent in filterOptions.agents"
              :key="agent.agent_id"
              :value="agent.agent_id"
              :label="`${agent.agent_name} ${agent.agent_id}`"
            >
              <span class="filter-option-label">
                <span class="option-name">{{ agent.agent_name }}</span>
                <span v-if="agent.is_deleted" class="deleted-text">已删除</span>
              </span>
            </a-select-option>
          </a-select>

          <a-select
            v-model:value="selectedUid"
            aria-label="按用户筛选"
            allow-clear
            show-search
            option-filter-prop="label"
            placeholder="全部用户"
            size="small"
            class="filter-select user-filter"
            @change="handleSearch"
          >
            <a-select-option
              v-for="user in filterOptions.users"
              :key="user.uid"
              :value="user.uid"
              :label="`${user.username} ${user.uid}`"
            >
              <span class="filter-option-label">
                <span class="option-name">{{ user.username }}</span>
                <span v-if="user.is_deleted" class="deleted-text">已注销</span>
              </span>
            </a-select-option>
          </a-select>

          <a-button size="small" @click="resetFilters">重置</a-button>
          <a-button type="primary" size="small" @click="handleSearch">查询</a-button>
        </div>
      </div>

      <div class="explorer-body">
        <a-empty
          v-if="!tableLoading && conversationList.length === 0"
          description="没有符合当前条件的会话，可调整关键词或状态后重试"
        />
        <a-table
          v-else
          :columns="conversationColumns"
          :data-source="conversationList"
          :loading="tableLoading"
          :pagination="tablePagination"
          :scroll="{ x: 980 }"
          size="middle"
          row-key="thread_id"
          @change="handleTableChange"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'title'">
              <div class="conv-title-cell">
                <div class="title-row">
                  <span class="conv-title" :title="record.title">{{ record.title || '未命名会话' }}</span>
                  <a-tag v-if="record.is_pinned" color="orange" size="small">置顶</a-tag>
                </div>
                <div class="id-row">
                  <span class="thread-id-badge" :title="record.thread_id">
                    {{ truncateIdentifier(record.thread_id, 12, 6) }}
                  </span>
                </div>
              </div>
            </template>

            <template v-if="column.key === 'agent'">
              <div class="entity-cell">
                <FallbackAvatar
                  :src="record.agent_avatar"
                  :name="record.agent_name || record.agent_id"
                  :seed="record.agent_id"
                  kind="agent"
                  :size="24"
                  shape="rounded"
                  decorative
                />
                <div class="entity-meta">
                  <span class="entity-name" :title="record.agent_name || record.agent_id">
                    {{ record.agent_name || record.agent_id }}
                  </span>
                  <span class="entity-id" :title="record.agent_id">{{ truncateIdentifier(record.agent_id) }}</span>
                </div>
                <a-tag v-if="record.agent_deleted" class="history-tag">已删除</a-tag>
              </div>
            </template>

            <template v-if="column.key === 'user'">
              <div class="entity-cell">
                <FallbackAvatar
                  :src="record.user_avatar"
                  :name="record.user_deleted ? '已注销用户' : record.username || record.uid"
                  :seed="record.uid"
                  kind="user"
                  :size="24"
                  shape="circle"
                  decorative
                />
                <div class="entity-meta">
                  <span class="entity-name" :title="record.username || record.uid">
                    {{ record.username || record.uid }}
                  </span>
                  <span class="entity-id" :title="record.uid">{{ truncateIdentifier(record.uid) }}</span>
                </div>
                <a-tag v-if="record.user_deleted" class="history-tag">已注销</a-tag>
              </div>
            </template>

            <template v-if="column.key === 'status'">
              <a-tag v-if="record.status === 'active'" color="green">进行中</a-tag>
              <a-tag v-else-if="record.status === 'archived'" color="default">已归档</a-tag>
              <a-tag v-else-if="record.status === 'deleted'" class="history-tag">已删除</a-tag>
              <a-tag v-else-if="record.status === 'subagent'" color="blue">子智能体</a-tag>
              <a-tag v-else>{{ record.status }}</a-tag>
            </template>

            <template v-if="column.key === 'message_count'">
              <span class="count-badge">{{ record.message_count }} 条</span>
            </template>

            <template v-if="column.key === 'total_tokens'">
              <span class="token-num">{{ (record.total_tokens || 0).toLocaleString() }}</span>
            </template>

            <template v-if="column.key === 'updated_at'">
              <span class="time-text">{{ formatFullDateTime(record.updated_at) }}</span>
            </template>

            <template v-if="column.key === 'actions'">
              <a-button type="link" size="small" @click="handleOpenDetail(record.thread_id)">
                查看详情
              </a-button>
            </template>
          </template>
        </a-table>
      </div>
    </div>

    <!-- 会话详情抽屉 -->
    <ThreadDetailDrawer ref="detailDrawerRef" />
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import {
  MessageSquare,
  Activity,
  Layers,
  Cpu,
  RefreshCw,
  Search,
  Info
} from 'lucide-vue-next'
import { message } from 'ant-design-vue'
import { dashboardApi } from '@/apis/dashboard_api'
import { getColorByIndex } from '@/utils/chartColors'
import { formatFullDateTime } from '@/utils/time'
import { useThemeStore } from '@/stores/theme'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import ThreadDetailDrawer from './ThreadDetailDrawer.vue'

function getCSSVariable(variableName, element = document.documentElement) {
  return getComputedStyle(element).getPropertyValue(variableName).trim()
}

const themeStore = useThemeStore()

const loading = ref(false)
const tableLoading = ref(false)
const timeRange = ref('30days')
const searchKeyword = ref('')
const selectedStatus = ref('all')
const selectedAgentId = ref(undefined)
const selectedUid = ref(undefined)
const threadData = ref(null)
const filterOptions = ref({ users: [], agents: [] })
const conversationList = ref([])
const detailDrawerRef = ref(null)

const timeRangeOptions = [
  { label: '近7天', value: '7days' },
  { label: '近14天', value: '14days' },
  { label: '近30天', value: '30days' },
  { label: '近90天', value: '90days' }
]

const tablePagination = ref({
  current: 1,
  pageSize: 10,
  total: 0,
  showSizeChanger: true,
  pageSizeOptions: ['10', '20', '50'],
  showTotal: (total) => `共 ${total} 条会话`
})

const userColumns = [
  { title: '排名', key: 'rank', width: 50, align: 'center' },
  { title: '用户', key: 'user', ellipsis: true },
  { title: '会话数', key: 'thread_count', width: 70, align: 'center' },
  { title: '消息数', key: 'message_count', width: 70, align: 'center' }
]

const conversationColumns = [
  { title: '会话标题 & ID', key: 'title', width: '28%' },
  { title: '所属智能体', key: 'agent', width: 190 },
  { title: '用户', key: 'user', width: 180 },
  { title: '状态', key: 'status', width: '90px', align: 'center' },
  { title: '消息数', key: 'message_count', width: '90px', align: 'center' },
  { title: 'Token 消耗', key: 'total_tokens', width: '110px', align: 'right' },
  { title: '更新时间', key: 'updated_at', width: '160px' },
  { title: '操作', key: 'actions', width: '90px', align: 'center' }
]

// Chart refs & instances
const trendChartRef = ref(null)
const depthChartRef = ref(null)
const agentChartRef = ref(null)
let trendChart = null
let depthChart = null
let agentChart = null

const truncateIdentifier = (value, startLength = 8, endLength = 4) => {
  const text = String(value || '')
  if (text.length <= startLength + endLength + 1) return text
  return `${text.slice(0, startLength)}…${text.slice(-endLength)}`
}

const formatTokenNumber = (val) => {
  if (!val) return '0'
  if (val >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`
  if (val >= 1_000) return `${(val / 1_000).toFixed(1)}K`
  return Number(val).toLocaleString()
}

const loadData = async () => {
  loading.value = true
  try {
    const res = await dashboardApi.getThreadStats({ timeRange: timeRange.value })
    threadData.value = res
    await nextTick()
    renderAllCharts()
  } catch (err) {
    console.error('加载会话统计数据失败:', err)
    message.error('加载会话统计数据失败')
  } finally {
    loading.value = false
  }
}

const loadFilterOptions = async () => {
  try {
    filterOptions.value = await dashboardApi.getConversationFilterOptions()
  } catch (err) {
    console.error('加载会话筛选项失败:', err)
  }
}

const loadConversations = async () => {
  tableLoading.value = true
  try {
    const offset = (tablePagination.value.current - 1) * tablePagination.value.pageSize
    const res = await dashboardApi.getConversations({
      status: selectedStatus.value,
      search: searchKeyword.value.trim() || undefined,
      agent_id: selectedAgentId.value,
      uid: selectedUid.value,
      limit: tablePagination.value.pageSize,
      offset
    })
    conversationList.value = res?.items || []
    tablePagination.value.total = res?.total || 0
  } catch (err) {
    console.error('加载会话明细列表失败:', err)
  } finally {
    tableLoading.value = false
  }
}

const resetFilters = () => {
  searchKeyword.value = ''
  selectedStatus.value = 'all'
  selectedAgentId.value = undefined
  selectedUid.value = undefined
  handleSearch()
}

const handleSearch = () => {
  tablePagination.value.current = 1
  loadConversations()
}

const handleSearchChange = () => {
  if (!searchKeyword.value) {
    handleSearch()
  }
}

const handleTableChange = (pag) => {
  tablePagination.value.current = pag.current
  tablePagination.value.pageSize = pag.pageSize
  loadConversations()
}

const handleOpenDetail = (threadId) => {
  detailDrawerRef.value?.open(threadId)
}

// Render Charts
const renderAllCharts = () => {
  renderTrendChart()
  renderDepthChart()
  renderAgentChart()
}

const renderTrendChart = () => {
  const container = trendChartRef.value
  if (!container || !threadData.value?.daily_trends) return

  if (trendChart) trendChart.dispose()
  trendChart = echarts.init(container)

  const trends = threadData.value.daily_trends
  const dates = trends.map((item) => item.date.slice(5))
  const newThreads = trends.map((item) => item.new_threads)
  const activeThreads = trends.map((item) => item.active_threads)

  const option = {
    tooltip: {
      trigger: 'axis',
      backgroundColor: getCSSVariable('--gray-0'),
      borderColor: getCSSVariable('--gray-200'),
      textStyle: { color: getCSSVariable('--gray-700') }
    },
    legend: {
      data: ['新增会话', '活跃会话'],
      top: 0,
      right: 10,
      textStyle: { color: getCSSVariable('--gray-600'), fontSize: 12 }
    },
    grid: {
      left: '3%',
      right: '4%',
      top: '12%',
      bottom: '3%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: getCSSVariable('--gray-200') } },
      axisLabel: { color: getCSSVariable('--gray-500'), fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      splitLine: { lineStyle: { color: getCSSVariable('--gray-100') } },
      axisLabel: { color: getCSSVariable('--gray-500'), fontSize: 11 }
    },
    series: [
      {
        name: '新增会话',
        type: 'bar',
        data: newThreads,
        itemStyle: {
          color: getColorByIndex(0),
          borderRadius: [4, 4, 0, 0]
        }
      },
      {
        name: '活跃会话',
        type: 'line',
        data: activeThreads,
        smooth: true,
        itemStyle: { color: getColorByIndex(1) },
        lineStyle: { width: 3 }
      }
    ]
  }

  trendChart.setOption(option)
}

const renderDepthChart = () => {
  const container = depthChartRef.value
  if (!container || !threadData.value?.depth_distribution) return

  if (depthChart) depthChart.dispose()
  depthChart = echarts.init(container)

  const depthMap = threadData.value.depth_distribution
  const categories = Object.keys(depthMap)
  const values = Object.values(depthMap)

  const option = {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: getCSSVariable('--gray-0'),
      borderColor: getCSSVariable('--gray-200'),
      textStyle: { color: getCSSVariable('--gray-700') }
    },
    grid: {
      left: '3%',
      right: '5%',
      top: '10%',
      bottom: '3%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: categories,
      axisLine: { lineStyle: { color: getCSSVariable('--gray-200') } },
      axisLabel: { color: getCSSVariable('--gray-500'), fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      splitLine: { lineStyle: { color: getCSSVariable('--gray-100') } },
      axisLabel: { color: getCSSVariable('--gray-500'), fontSize: 11 }
    },
    series: [
      {
        name: '会话数量',
        type: 'bar',
        data: values,
        itemStyle: {
          color: (params) => getColorByIndex(params.dataIndex),
          borderRadius: [4, 4, 0, 0]
        },
        label: {
          show: true,
          position: 'top',
          color: getCSSVariable('--gray-600'),
          fontSize: 11
        }
      }
    ]
  }

  depthChart.setOption(option)
}

const renderAgentChart = () => {
  const container = agentChartRef.value
  if (!container || !threadData.value?.agent_distribution) return

  if (agentChart) agentChart.dispose()
  agentChart = echarts.init(container)

  const agents = threadData.value.agent_distribution.slice(0, 8)
  const names = agents.map((a) => a.agent_name || a.agent_id)
  const threadCounts = agents.map((a) => a.thread_count)

  const option = {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: getCSSVariable('--gray-0'),
      borderColor: getCSSVariable('--gray-200'),
      textStyle: { color: getCSSVariable('--gray-700') }
    },
    grid: {
      left: '3%',
      right: '6%',
      top: '8%',
      bottom: '3%',
      containLabel: true
    },
    xAxis: {
      type: 'value',
      axisLine: { show: false },
      splitLine: { lineStyle: { color: getCSSVariable('--gray-100') } },
      axisLabel: { color: getCSSVariable('--gray-500'), fontSize: 11 }
    },
    yAxis: {
      type: 'category',
      data: names,
      axisLine: { lineStyle: { color: getCSSVariable('--gray-200') } },
      axisLabel: { color: getCSSVariable('--gray-600'), fontSize: 11 }
    },
    series: [
      {
        name: '会话数',
        type: 'bar',
        data: threadCounts,
        itemStyle: {
          color: getColorByIndex(2),
          borderRadius: [0, 4, 4, 0]
        },
        label: {
          show: true,
          position: 'right',
          color: getCSSVariable('--gray-600'),
          fontSize: 11
        }
      }
    ]
  }

  agentChart.setOption(option)
}

const handleResize = () => {
  if (trendChart) trendChart.resize()
  if (depthChart) depthChart.resize()
  if (agentChart) agentChart.resize()
}

const cleanup = () => {
  window.removeEventListener('resize', handleResize)
  if (trendChart) {
    trendChart.dispose()
    trendChart = null
  }
  if (depthChart) {
    depthChart.dispose()
    depthChart = null
  }
  if (agentChart) {
    agentChart.dispose()
    agentChart = null
  }
}

defineExpose({
  cleanup,
  resizeCharts: handleResize
})

onMounted(async () => {
  window.addEventListener('resize', handleResize)
  await Promise.all([loadData(), loadFilterOptions(), loadConversations()])
})

onUnmounted(() => {
  cleanup()
})

watch(
  () => themeStore.isDark,
  () => {
    nextTick(() => {
      if (trendChartRef.value?.offsetWidth) renderAllCharts()
    })
  }
)
</script>

<style scoped lang="less">
.thread-stats-wrapper {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 20px;
  padding: var(--page-padding);
}

.thread-filter-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);

  .scope-note {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--gray-500);
    font-size: 12px;
    line-height: 20px;

    .scope-icon {
      width: 14px;
      height: 14px;
      flex: 0 0 14px;
    }
  }

  .header-controls {
    display: flex;
    align-items: center;
    gap: 16px;

    .filter-group {
      display: flex;
      align-items: center;
      gap: 8px;

      .filter-label {
        font-size: 12px;
        color: var(--gray-600);
        font-weight: 500;
      }
    }

    .refresh-btn {
      display: inline-flex;
      align-items: center;
      gap: 4px;

      .btn-icon {
        width: 14px;
        height: 14px;

        &.spinning {
          animation: spin 1s linear infinite;
        }
      }
    }
  }
}

@keyframes spin {
  100% {
    transform: rotate(360deg);
  }
}

.stats-overview-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);

  .stat-card {
    background: var(--gray-0);
    padding: 14px 16px;
    border-right: 1px solid var(--gray-150);
    display: flex;
    align-items: center;
    gap: 16px;
    transition: background-color 0.2s ease;

    &:last-child {
      border-right: none;
    }

    &:hover {
      background: var(--gray-10);
    }

    .stat-icon {
      width: 44px;
      height: 44px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;

      .icon {
        width: 22px;
        height: 22px;
      }
    }

    &.primary .stat-icon {
      background: var(--color-primary-50);
      color: var(--main-color);
    }

    &.success .stat-icon {
      background: var(--color-success-50);
      color: var(--color-success-700);
    }

    &.info .stat-icon {
      background: var(--color-info-50);
      color: var(--color-info-700);
    }

    &.warning .stat-icon {
      background: var(--color-warning-50);
      color: var(--color-warning-700);
    }

    .stat-content {
      flex: 1;
      display: flex;
      flex-direction: column;

      .stat-value {
        font-size: 24px;
        font-weight: 700;
        color: var(--gray-1000);
        line-height: 1.1;
        margin-bottom: 4px;
      }

      .stat-label {
        font-size: 13px;
        color: var(--gray-600);
        font-weight: 500;
      }

      .stat-subtag {
        font-size: 11px;
        color: var(--gray-500);
        margin-top: 4px;
      }
    }
  }
}

.charts-2x2-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0;
  overflow: hidden;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);

  .chart-card {
    min-width: 0;
    background: transparent;
    border-right: 1px solid var(--gray-150);
    border-bottom: 1px solid var(--gray-150);
    padding: 16px 20px;
    display: flex;
    flex-direction: column;
    min-height: 320px;
    transition: background-color 0.2s ease;

    &:nth-child(2n) {
      border-right: none;
    }

    &:nth-last-child(-n + 2) {
      border-bottom: none;
    }

    &:hover {
      background: var(--gray-10);
    }

    .card-header {
      margin-bottom: 12px;

      .card-title-group {
        display: flex;
        flex-direction: column;
        gap: 2px;

        .card-title {
          font-size: 15px;
          font-weight: 600;
          color: var(--gray-900);
        }

        .card-desc {
          font-size: 12px;
          color: var(--gray-500);
        }
      }
    }

    .chart-body {
      flex: 1;
      min-height: 220px;

      .echart-box {
        width: 100%;
        height: 100%;
        min-height: 220px;
      }

      &.table-body {
        min-width: 0;
        overflow: hidden;
      }
    }
  }
}

.user-rank {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 600;
  color: var(--gray-600);
  background: var(--gray-100);

  &.top3 {
    background: var(--main-20);
    color: var(--main-color);
    border: 1px solid var(--main-100);
  }
}

.user-cell {
  display: flex;
  align-items: center;
  gap: 8px;

  .user-cell-meta {
    display: flex;
    flex-direction: column;
    overflow: hidden;

    .user-cell-name {
      font-size: 12px;
      font-weight: 500;
      color: var(--gray-900);
      line-height: 1.2;
    }

    .user-cell-uid {
      font-size: 10px;
      color: var(--gray-500);
      font-family: monospace;
      text-overflow: ellipsis;
      overflow: hidden;
      white-space: nowrap;
    }
  }
}

.metric-num {
  font-size: 12px;
  color: var(--gray-800);
}

.explorer-card {
  min-width: 0;
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  padding: 16px;

  .explorer-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 16px;
    margin-bottom: 16px;

    .explorer-title-group {
      display: flex;
      flex-direction: column;
      gap: 2px;

      .explorer-title {
        font-size: 16px;
        font-weight: 600;
        color: var(--gray-1000);
      }

      .explorer-subtitle {
        font-size: 12px;
        color: var(--gray-500);
      }
    }

    .explorer-actions {
      display: flex;
      align-items: center;
      gap: 10px;

      .search-input {
        width: 260px;

        .input-icon {
          width: 14px;
          height: 14px;
          color: var(--gray-400);
        }
      }

      .filter-select {
        min-width: 120px;
      }

      .agent-filter,
      .user-filter {
        width: 170px;
      }
    }
  }

  .conv-title-cell {
    display: flex;
    flex-direction: column;
    gap: 2px;

    .title-row {
      display: flex;
      align-items: center;
      gap: 6px;

      .conv-title {
        font-size: 13px;
        font-weight: 500;
        color: var(--gray-900);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
    }

    .id-row {
      .thread-id-badge {
        font-family: monospace;
        font-size: 11px;
        color: var(--gray-500);
      }
    }
  }

  .entity-cell {
    display: flex;
    align-items: center;
    min-width: 0;
    gap: 8px;

    .entity-meta {
      display: flex;
      min-width: 0;
      flex: 1;
      flex-direction: column;
    }

    .entity-name,
    .entity-id {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .entity-name {
      color: var(--gray-900);
      font-size: 12px;
      font-weight: 500;
    }

    .entity-id {
      color: var(--gray-500);
      font-family: monospace;
      font-size: 10px;
    }
  }

  .history-tag {
    flex-shrink: 0;
    border-color: var(--gray-150);
    background: var(--gray-100);
    color: var(--gray-600);
    font-size: 11px;
  }

  .count-badge {
    font-size: 12px;
    color: var(--gray-700);
  }

  .token-num {
    font-size: 12px;
    font-family: monospace;
    color: var(--gray-700);
  }

  .time-text {
    font-size: 12px;
    color: var(--gray-500);
  }
}

.filter-option-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  gap: 8px;

  .option-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .deleted-text {
    flex-shrink: 0;
    color: var(--gray-400);
    font-size: 11px;
  }
}

@media (max-width: 1200px) {
  .stats-overview-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .charts-2x2-grid {
    grid-template-columns: 1fr;

    .chart-card {
      border-right: none;
      border-bottom: 1px solid var(--gray-150);

      &:nth-last-child(-n + 2) {
        border-bottom: 1px solid var(--gray-150);
      }

      &:last-child {
        border-bottom: none;
      }
    }
  }
}

@media (max-width: 768px) {
  .thread-stats-wrapper {
    padding: 12px;
  }

  .thread-filter-header {
    flex-direction: column;
    align-items: flex-start;

    .header-controls,
    .filter-group {
      width: 100%;
      flex-wrap: wrap;
    }
  }

  .stats-overview-grid {
    grid-template-columns: 1fr;

    .stat-card {
      border-right: none;
      border-bottom: 1px solid var(--gray-150);

      &:last-child {
        border-bottom: none;
      }
    }
  }

  .explorer-card {
    padding: 14px;

    .explorer-header {
      flex-direction: column;
      align-items: flex-start;

      .explorer-actions {
        width: 100%;
        flex-direction: column;

        .search-input {
          width: 100%;
        }
      }
    }
  }
}
</style>
