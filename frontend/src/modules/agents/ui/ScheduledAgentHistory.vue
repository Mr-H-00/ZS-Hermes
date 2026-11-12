<script setup>
import { onUnmounted, ref, watch } from 'vue'
import { ExternalLink, RefreshCw } from '@lucide/vue'
import { scheduledAgentApi } from '@/apis/scheduled_agent_api'

const props = defineProps({
  jobId: { type: String, required: true },
  revision: { type: Number, default: 0 }
})
const emit = defineEmits(['open-session'])
const PAGE_SIZE = 10
const items = ref([])
const total = ref(0)
const offset = ref(0)
const requestedOffset = ref(0)
const loading = ref(false)
const error = ref('')
let requestVersion = 0

const RUN_STATUS_LABELS = {
  dispatching: '提交中',
  submitted: '已提交',
  queued: '排队中',
  dispatched: '已派发',
  pending: '等待中',
  running: '运行中',
  completed: '已完成',
  skipped: '已跳过',
  failed: '失败',
  rejected: '已拒绝',
  cancelled: '已取消',
  interrupted: '已中断'
}

/** 分页请求仅可更新仍在查看的任务，失败后重试原目标页。 */
async function load(nextOffset = 0) {
  const version = ++requestVersion
  requestedOffset.value = nextOffset
  loading.value = true
  error.value = ''
  try {
    const result = await scheduledAgentApi.history(props.jobId, {
      limit: PAGE_SIZE,
      offset: nextOffset
    })
    if (version !== requestVersion) return
    items.value = result.items
    total.value = result.total
    offset.value = result.offset
  } catch (cause) {
    if (version !== requestVersion) return
    error.value = cause.message || '加载运行历史失败'
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

/** 任务切换和显式刷新从第一页重新读取持久事实。 */
watch(
  [() => props.jobId, () => props.revision],
  () => {
    items.value = []
    total.value = 0
    offset.value = 0
    void load()
  },
  { immediate: true }
)
onUnmounted(() => {
  requestVersion += 1
})

function formatRunTime(value) {
  if (!value) return '等待调度'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  }).format(date)
}

function runStatusLabel(run) {
  return RUN_STATUS_LABELS[run.status] || run.status
}

function runStatusTone(run) {
  if (run.status === 'completed') return 'success'
  if (['failed', 'rejected', 'cancelled', 'interrupted'].includes(run.status)) return 'danger'
  if (run.status === 'skipped') return 'warning'
  return 'active'
}

function canOpenSession(run) {
  return Boolean(run.session_available && run.thread_id)
}

function runRecordLabel(run) {
  const trigger = run.trigger === 'manual' ? '手动运行' : '定时运行'
  const availability = canOpenSession(run) ? '打开对应对话' : '没有可用对话'
  return `${trigger}，${runStatusLabel(run)}，${formatRunTime(run.scheduled_for)}，${availability}`
}
</script>

<template>
  <section class="history-section" aria-labelledby="runs-heading" :aria-busy="loading">
    <header>
      <div>
        <h3 id="runs-heading">运行历史记录</h3>
        <p>点击记录进入本次运行创建的对话。</p>
      </div>
      <div class="history-actions">
        <span>{{ total }} 条</span>
        <button type="button" :disabled="loading" aria-label="刷新运行历史" @click="load(0)">
          <RefreshCw :size="14" aria-hidden="true" />
        </button>
      </div>
    </header>

    <p v-if="loading" class="runs-empty" role="status">正在加载运行历史…</p>
    <div v-else-if="error" class="history-error" role="alert">
      <span>{{ error }}</span>
      <button type="button" @click="load(requestedOffset)">重试</button>
    </div>
    <div v-else-if="items.length" class="run-list">
      <button
        v-for="run in items"
        :key="run.id"
        type="button"
        class="run-row"
        :class="{ actionable: canOpenSession(run) }"
        :disabled="!canOpenSession(run)"
        :aria-label="runRecordLabel(run)"
        @click="emit('open-session', run)"
      >
        <span class="run-status" :class="runStatusTone(run)">{{ runStatusLabel(run) }}</span>
        <span class="run-copy">
          <strong>{{ run.trigger === 'manual' ? '手动运行' : '定时运行' }}</strong>
          <small v-if="run.error_message" :title="run.error_message">{{ run.error_message }}</small>
          <small v-else>{{ canOpenSession(run) ? '查看对话和运行结果' : '尚未创建对话' }}</small>
        </span>
        <time :datetime="run.scheduled_for">{{ formatRunTime(run.scheduled_for) }}</time>
        <ExternalLink v-if="canOpenSession(run)" :size="15" aria-hidden="true" />
        <span v-else class="no-session">无对话</span>
      </button>
    </div>
    <p v-else class="runs-empty">任务运行后，记录会显示在这里。</p>
    <nav v-if="total > PAGE_SIZE" class="history-pagination" aria-label="运行历史分页">
      <button type="button" :disabled="loading || offset === 0" @click="load(offset - PAGE_SIZE)">
        上一页
      </button>
      <span aria-live="polite"
        >第 {{ Math.floor(offset / PAGE_SIZE) + 1 }} / {{ Math.ceil(total / PAGE_SIZE) }} 页</span
      >
      <button
        type="button"
        :disabled="loading || offset + PAGE_SIZE >= total"
        @click="load(offset + PAGE_SIZE)"
      >
        下一页
      </button>
    </nav>
  </section>
</template>

<style lang="less" scoped>
.history-section {
  padding: 0 22px 24px;

  > header {
    display: flex;
    padding: 10px 0 6px;
    border-bottom: 1px solid var(--gray-150);
    align-items: flex-end;
    justify-content: space-between;

    h3 {
      margin: 0;
      color: var(--gray-700);
      font-size: 12px;
      font-weight: 500;
    }

    p {
      margin: 2px 0 0;
      color: var(--gray-400);
      font-size: 11px;
    }

    > span {
      color: var(--gray-400);
      font-size: 11px;
    }
  }
}

.run-list {
  border-bottom: 1px solid var(--gray-100);
}

.run-row {
  display: grid;
  width: 100%;
  min-height: 44px;
  padding: 10px 12px;
  border: 0;
  border-bottom: 1px solid var(--gray-100);
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  grid-template-columns: 58px minmax(0, 1fr) auto 36px;
  align-items: center;
  gap: 10px;

  &:last-child {
    border-bottom: 0;
  }

  &.actionable {
    cursor: pointer;

    &:hover {
      background: var(--gray-25);
    }
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 1px;
  }

  time,
  svg,
  .no-session {
    color: var(--gray-400);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }
  .no-session {
    white-space: nowrap;
  }
}

.run-status {
  color: var(--color-info-700);
  font-size: 11px;

  &.success {
    color: var(--color-success-700);
  }

  &.danger {
    color: var(--color-error-700);
  }

  &.warning {
    color: var(--color-warning-900);
  }
}

.run-copy {
  display: grid;
  min-width: 0;
  gap: 2px;

  strong,
  small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  strong {
    color: var(--gray-800);
    font-size: 12px;
    font-weight: 550;
  }

  small {
    color: var(--gray-400);
    font-size: 11px;
  }
}

.runs-empty {
  margin: 0;
  padding: 22px 0;
  border-bottom: 1px solid var(--gray-100);
  color: var(--gray-400);
  font-size: 12px;
}

.history-actions,
.history-pagination,
.history-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-block: 12px;
  color: var(--gray-600);
  font-size: 12px;

  button {
    padding: 4px 8px;
    border: 1px solid var(--gray-150);
    border-radius: 4px;
    background: var(--gray-0);
    color: var(--gray-700);
    cursor: pointer;

    &:hover:not(:disabled) {
      background: var(--gray-50);
    }
    &:focus-visible {
      outline: 2px solid var(--main-color);
      outline-offset: 2px;
    }
    &:disabled {
      opacity: 0.45;
      cursor: default;
    }
  }
}
.history-actions {
  padding: 0;
}
@media (max-width: 640px) {
  .history-section {
    padding-inline: 16px;
  }
  .run-row {
    grid-template-columns: 52px minmax(0, 1fr) 18px;
    time,
    .no-session {
      display: none;
    }
  }
}
</style>
