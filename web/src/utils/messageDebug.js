/** 将消息内容压缩为单行摘要。 */
function summarizeContent(content, limit) {
  if (typeof content !== 'string') return ''
  return content.replace(/\s+/g, ' ').trim().slice(0, limit)
}

/** 提取 AI 消息中去重后的工具名称。 */
export function extractMessageToolNames(message) {
  const toolCalls = Array.isArray(message?.tool_calls) ? message.tool_calls : []
  return [
    ...new Set(
      toolCalls
        .map((toolCall) => toolCall?.name || toolCall?.tool_name || toolCall?.function?.name)
        .filter(Boolean)
    )
  ]
}

function messageRequestId(message) {
  const metadataId = message?.extra_metadata?.request_id
  if (typeof metadataId === 'string' && metadataId.trim()) return metadataId.trim()
  if (typeof message?.request_id === 'string' && message.request_id.trim()) {
    return message.request_id.trim()
  }
  return null
}

function messageRunId(message) {
  const metadataId = message?.extra_metadata?.run_id
  if (typeof metadataId === 'string' && metadataId.trim()) return metadataId.trim()
  if (typeof message?.run_id === 'string' && message.run_id.trim()) return message.run_id.trim()
  return null
}

/** 合并普通历史与实时投影；只按已有稳定 ID 去重，不按 AI 位置猜测。 */
export function mergeMessageDebugMessages(history, ongoing) {
  const persisted = Array.isArray(history) ? history : []
  const live = Array.isArray(ongoing) ? ongoing : []
  const liveHumanByRequestId = new Map(
    live
      .filter((message) => message?.type === 'human')
      .map((message) => [messageRequestId(message), message])
      .filter(([requestId]) => requestId)
  )
  const mergedPersisted = persisted.map((message) => {
    if (message?.type !== 'human' || messageRunId(message)) return message
    const liveMessage = liveHumanByRequestId.get(messageRequestId(message))
    const liveRunId = messageRunId(liveMessage)
    if (!liveRunId) return message
    return {
      ...message,
      run_id: liveRunId,
      extra_metadata: {
        ...(message.extra_metadata || {}),
        run_id: liveRunId
      }
    }
  })
  const persistedIds = new Set(mergedPersisted.map((message) => String(message?.id ?? '')))
  const persistedRequestIds = new Set(
    mergedPersisted
      .filter((message) => message?.type === 'human')
      .map(messageRequestId)
      .filter(Boolean)
  )
  const pending = live.filter((message) => {
    if (persistedIds.has(String(message?.id ?? ''))) return false
    return message?.type !== 'human' || !persistedRequestIds.has(messageRequestId(message))
  })
  return [...mergedPersisted, ...pending]
}

function modelAuditKey(message) {
  const runId = messageRunId(message)
  const operationId =
    typeof message?.operation_id === 'string' && message.operation_id.trim()
      ? message.operation_id.trim()
      : null
  return runId && operationId ? `${runId}\u0000${operationId}` : null
}

function liveModelAuditKey(message) {
  const runId = messageRunId(message)
  const messageId = typeof message?.id === 'string' && message.id.trim() ? message.id.trim() : null
  const isAi = message?.type === 'ai' || message?.role === 'assistant'
  return runId && messageId && isAi ? `${runId}\u0000${messageId}` : null
}

function mergeAuditMessage(message, audit, previous = null) {
  const content =
    (typeof message?.content === 'string' && message.content) || previous?.content || audit.content
  const messageToolCalls = Array.isArray(message?.tool_calls) ? message.tool_calls : []
  const previousToolCalls = Array.isArray(previous?.tool_calls) ? previous.tool_calls : []
  let toolCalls = Array.isArray(audit.tool_calls) ? audit.tool_calls : []
  if (previousToolCalls.length) toolCalls = previousToolCalls
  if (messageToolCalls.length) toolCalls = messageToolCalls
  return {
    ...(previous || {}),
    ...message,
    ...audit,
    content,
    tool_calls: toolCalls
  }
}

/** 按 Message ID 或 (run_id, operation_id) 合并 PG 审计与实时投影。 */
export function mergeMessageDebugAudits(messages, audits) {
  const source = Array.isArray(messages) ? messages : []
  const persistedAudits = Array.isArray(audits) ? audits : []
  if (persistedAudits.length === 0) return source

  const auditsByMessageId = new Map()
  const auditsByOperation = new Map()
  persistedAudits.forEach((audit) => {
    if (audit?.id !== undefined && audit?.id !== null) {
      auditsByMessageId.set(String(audit.id), audit)
    }
    const operationKey = modelAuditKey(audit)
    if (operationKey) auditsByOperation.set(operationKey, audit)
  })

  const merged = []
  const matchedAuditIndexes = new Map()
  source.forEach((message) => {
    const messageId = message?.id === undefined || message?.id === null ? null : String(message.id)
    const operationKey = modelAuditKey(message)
    const liveOperationKey = liveModelAuditKey(message)
    const audit =
      (messageId && auditsByMessageId.get(messageId)) ||
      (operationKey && auditsByOperation.get(operationKey)) ||
      (liveOperationKey && auditsByOperation.get(liveOperationKey))
    if (!audit) {
      merged.push(message)
      return
    }

    const matchedIndex = matchedAuditIndexes.get(audit)
    if (matchedIndex !== undefined) {
      merged[matchedIndex] = mergeAuditMessage(message, audit, merged[matchedIndex])
      return
    }

    matchedAuditIndexes.set(audit, merged.length)
    merged.push(mergeAuditMessage(message, audit))
  })

  const pending = persistedAudits.filter((audit) => !matchedAuditIndexes.has(audit))

  pending.forEach((audit) => {
    const runId = messageRunId(audit)
    const sequence = Number.isFinite(audit?.sequence) ? audit.sequence : Number.MAX_SAFE_INTEGER
    const laterOperationIndex = merged.findIndex((message) => {
      if (messageRunId(message) !== runId) return false
      if (liveModelAuditKey(message) && !modelAuditKey(message)) return true
      if (!modelAuditKey(message)) return false
      const messageSequence = Number.isFinite(message?.sequence)
        ? message.sequence
        : Number.MAX_SAFE_INTEGER
      return messageSequence > sequence
    })
    if (laterOperationIndex >= 0) {
      merged.splice(laterOperationIndex, 0, audit)
      return
    }

    const lastRunIndex = merged.findLastIndex((message) => messageRunId(message) === runId)
    merged.splice(lastRunIndex >= 0 ? lastRunIndex + 1 : merged.length, 0, audit)
  })

  return merged
}

function aiRoleLabel({ isError, operationId, hasTools }) {
  if (isError) return 'Error'
  if (operationId) return hasTools ? 'Model · Tools' : 'Model'
  return hasTools ? 'AI · Tools' : 'AI'
}

/** 保持输入顺序，将原始历史消息转换为调试列表条目。 */
export function buildMessageDebugEntries(messages) {
  const source = Array.isArray(messages) ? messages : []

  return source.map((message, index) => {
    const type = message?.type || message?.role || 'unknown'
    const runId = messageRunId(message)
    const operationId =
      typeof message?.operation_id === 'string' && message.operation_id.trim()
        ? message.operation_id.trim()
        : null
    const common = {
      id: operationId ? `${runId || 'unassigned'}:${operationId}` : String(message?.id ?? `message-${index}`),
      runId,
      operationId,
      model: message?.model || message?.extra_metadata?.model || '',
      usage: message?.usage || message?.extra_metadata?.usage,
      startedAt: message?.started_at || null,
      finishedAt: message?.finished_at || null,
      durationMs: Number.isFinite(message?.duration_ms) ? message.duration_ms : null,
      sequence: Number.isFinite(message?.sequence) ? message.sequence : null,
      finishedSequence: Number.isFinite(message?.finished_sequence)
        ? message.finished_sequence
        : null,
      executionStatus: message?.execution_status || null,
      raw: message
    }
    const isError = Boolean(message?.error_type || message?.error_message)

    if (type === 'human' || type === 'user') {
      return {
        ...common,
        role: 'human',
        roleLabel: 'User',
        summary: summarizeContent(message.content, 200) || '[用户输入/附件]'
      }
    }

    if (type === 'system') {
      return {
        ...common,
        role: 'system',
        roleLabel: 'System',
        summary: summarizeContent(message.content, 200) || '[系统消息]'
      }
    }

    if (type === 'tool') {
      const toolName = message?.name || message?.tool_name || message?.function?.name
      const content = summarizeContent(message.content, 140)
      const error = summarizeContent(message.error_message, 140)
      const input = summarizeContent(
        message?.tool_input ? JSON.stringify(message.tool_input) : '',
        120
      )
      let auditDetail = ''
      if (error) auditDetail = `错误: ${error}`
      else if (content) auditDetail = `输出: ${content}`
      else if (input) auditDetail = `输入: ${input}`
      const historyDetail = [toolName ? `工具: ${toolName}` : '', content]
        .filter(Boolean)
        .join(' | ')
      return {
        ...common,
        role: 'tool',
        roleLabel: operationId && toolName ? `Tool · ${toolName}` : 'Tool',
        summary: (operationId ? auditDetail : historyDetail) || '[工具执行]'
      }
    }

    if (type === 'ai' || type === 'assistant') {
      const toolNames = extractMessageToolNames(message)
      const content = summarizeContent(message.content, 160)
      const summary = isError
        ? message.error_message || message.error_type || '执行异常'
        : [content, toolNames.length ? `工具: ${toolNames.join('、')}` : '']
            .filter(Boolean)
            .join(' | ') || '[AI 回复]'

      return {
        ...common,
        role: isError ? 'error' : 'ai',
        roleLabel: aiRoleLabel({ isError, operationId, hasTools: toolNames.length > 0 }),
        summary
      }
    }

    return {
      ...common,
      role: 'other',
      roleLabel: String(type),
      summary: summarizeContent(message?.content, 200) || '[未知消息]'
    }
  })
}

/** 格式化后端 monotonic Model/Tool 耗时，不从 wall-clock 推导。 */
export function formatAuditDuration(durationMs) {
  if (!Number.isFinite(durationMs) || durationMs < 0) return ''
  if (durationMs < 1000) return `${durationMs} ms`
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(durationMs < 10_000 ? 2 : 1)} s`
  const totalSeconds = Math.round(durationMs / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}m ${seconds}s`
}

/** 仅在面板和页面可见且 Run 确实执行中时轮询审计。 */
export function shouldPollMessageAudits({ panelActive, pageVisible, runActive, activeRunId }) {
  return Boolean(panelActive && pageVisible && runActive && activeRunId)
}

/** 从后端响应中读取可安全打开的 Langfuse HTTP(S) 地址。 */
export function resolveLangfuseRunUrl(payload) {
  if (payload?.available !== true || typeof payload?.url !== 'string') return null

  const value = payload.url.trim()
  if (!value) return null
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? value : null
  } catch {
    return null
  }
}

/** 按连续 run_id 建立调试分组，不改变消息的事实顺序。 */
export function groupMessageDebugEntries(entries) {
  const source = Array.isArray(entries) ? entries : []
  const groups = []

  source.forEach((item) => {
    const runId = typeof item?.runId === 'string' && item.runId.trim() ? item.runId.trim() : null
    let group = groups.at(-1)
    if (!group || group.runId !== runId) {
      group = {
        key: `${runId || 'unassigned'}-${groups.length}`,
        runId,
        items: []
      }
      groups.push(group)
    }
    group.items.push(item)
  })

  return groups
}
