const METRICS = [
  {
    key: 'dispatch_latency_ms',
    label: '调度等待',
    description: 'Run 创建到 Worker 取得执行权'
  },
  {
    key: 'preparation_latency_ms',
    label: '运行准备',
    description: 'Worker 取得执行权到 Agent 运行上下文与模型流准备完成'
  },
  {
    key: 'model_first_output_latency_ms',
    label: '模型首响',
    description: '准备完成到首个非空模型文本、推理或工具调用数据'
  },
  {
    key: 'first_output_latency_ms',
    label: '首次输出',
    description: 'Run 创建到首个非空模型语义输出'
  },
  {
    key: 'total_latency_ms',
    label: '总耗时',
    description: 'Run 创建到 PostgreSQL 终态'
  }
]

const finiteDuration = (value) =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null

export const formatRunTimingDuration = (value) => {
  const milliseconds = finiteDuration(value)
  if (milliseconds === null) return ''
  if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`

  const seconds = milliseconds / 1000
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`

  const roundedSeconds = Math.round(seconds)
  const minutes = Math.floor(roundedSeconds / 60)
  const remainingSeconds = roundedSeconds % 60
  return `${minutes}m ${remainingSeconds}s`
}

export const buildRunTimingRows = (timing) => {
  if (!timing || typeof timing !== 'object') return []
  return METRICS.flatMap((metric) => {
    const value = finiteDuration(timing[metric.key])
    return value === null
      ? []
      : [{ ...metric, value, formattedValue: formatRunTimingDuration(value) }]
  })
}

export const formatRunTimingSummary = (timing) => {
  const firstOutput = formatRunTimingDuration(timing?.first_output_latency_ms)
  if (firstOutput) return `首输出 ${firstOutput}`

  const total = formatRunTimingDuration(timing?.total_latency_ms)
  return total ? `耗时 ${total}` : ''
}
