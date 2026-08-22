export const formatProcessDuration = (durationMs) => {
  if (!durationMs) return '处理过程'
  const totalSeconds = Math.max(0, Math.round(durationMs / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `耗时${minutes}分钟${seconds}秒`
}

export const collapseConversationProcess = (items, enabled = false) => {
  if (!enabled) return items

  const finalIndex = items.findLastIndex(
    (item) => item.type === 'message' && item.message?.type === 'ai'
  )
  if (finalIndex <= 1 || finalIndex !== items.length - 1) return items

  const processStart = items.findIndex(
    (item, index) =>
      index < finalIndex &&
      (item.type === 'tool-group' || (item.type === 'message' && item.message?.type === 'ai'))
  )
  if (processStart < 0) return items

  const processItems = items.slice(processStart, finalIndex)
  if (processItems.some((item) => item.type !== 'tool-group' && item.message?.type !== 'ai')) {
    return items
  }

  const messageCount = processItems.filter((item) => item.type === 'message').length
  const toolCallCount = processItems.reduce(
    (count, item) => count + (item.type === 'tool-group' ? item.toolCalls.length : 0),
    0
  )
  if (!messageCount && !toolCallCount) return items

  const finalMessage = items[finalIndex].message
  const startedAt = Date.parse(finalMessage?.run_started_at || '')
  const finishedAt = Date.parse(finalMessage?.run_finished_at || finalMessage?.created_at || '')
  const durationMs =
    Number.isFinite(startedAt) && Number.isFinite(finishedAt) && finishedAt > startedAt
      ? finishedAt - startedAt
      : 0

  return [
    ...items.slice(0, processStart),
    {
      type: 'process-group',
      key: `process-group-${processItems[0].key}`,
      items: processItems,
      messageCount,
      toolCallCount,
      durationMs
    },
    items[finalIndex]
  ]
}
