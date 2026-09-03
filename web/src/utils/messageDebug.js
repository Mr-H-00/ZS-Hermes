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

export function getMessageRequestId(message, { allowMessageIdFallback = false } = {}) {
  const metadataId = message?.extra_metadata?.request_id
  if (typeof metadataId === 'string' && metadataId.trim()) return metadataId.trim()
  if (typeof message?.request_id === 'string' && message.request_id.trim()) {
    return message.request_id.trim()
  }
  if (allowMessageIdFallback && message?.type === 'human' && typeof message.id === 'string') {
    const messageId = message.id.trim()
    if (messageId) return messageId
  }
  return null
}

export function getMessageRunId(message) {
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
      .map((message) => [getMessageRequestId(message), message])
      .filter(([requestId]) => requestId)
  )
  const mergedPersisted = persisted.map((message) => {
    if (message?.type !== 'human' || getMessageRunId(message)) return message
    const liveMessage = liveHumanByRequestId.get(getMessageRequestId(message))
    const liveRunId = getMessageRunId(liveMessage)
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
      .map(getMessageRequestId)
      .filter(Boolean)
  )
  const pending = live.filter((message) => {
    if (persistedIds.has(String(message?.id ?? ''))) return false
    return message?.type !== 'human' || !persistedRequestIds.has(getMessageRequestId(message))
  })
  return [...mergedPersisted, ...pending]
}

function auditRole(message) {
  const type = message?.type || message?.role
  if (type === 'ai') return 'assistant'
  return type
}

function auditKey(message) {
  const runId = getMessageRunId(message)
  const operationId =
    typeof message?.operation_id === 'string' && message.operation_id.trim()
      ? message.operation_id.trim()
      : null
  const role = auditRole(message)
  return runId && role && operationId ? `${runId}\u0000${role}\u0000${operationId}` : null
}

function liveModelAuditKey(message) {
  const runId = getMessageRunId(message)
  const messageId = typeof message?.id === 'string' && message.id.trim() ? message.id.trim() : null
  const isAi = message?.type === 'ai' || message?.role === 'assistant'
  return runId && messageId && isAi ? `${runId}\u0000assistant\u0000${messageId}` : null
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

/** 按 Message ID 或 (run_id, role, operation_id) 合并 PG 审计与实时投影。 */
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
    const operationKey = auditKey(audit)
    if (operationKey) auditsByOperation.set(operationKey, audit)
  })

  const merged = []
  const matchedAuditIndexes = new Map()
  source.forEach((message) => {
    const messageId = message?.id === undefined || message?.id === null ? null : String(message.id)
    const operationKey = auditKey(message)
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
  const pendingByRun = new Map()
  pending.forEach((audit) => {
    const runId = getMessageRunId(audit)
    const runAudits = pendingByRun.get(runId) || []
    runAudits.push(audit)
    pendingByRun.set(runId, runAudits)
  })

  const lastMessageIndexByRun = new Map()
  merged.forEach((message, index) => lastMessageIndexByRun.set(getMessageRunId(message), index))

  const nextAuditIndexByRun = new Map()
  const emittedAudits = new Set()
  const ordered = []
  merged.forEach((message, messageIndex) => {
    const runId = getMessageRunId(message)
    const runAudits = pendingByRun.get(runId) || []
    let nextAuditIndex = nextAuditIndexByRun.get(runId) || 0
    const messageSequence = Number.isFinite(message?.sequence)
      ? message.sequence
      : Number.MAX_SAFE_INTEGER
    const isUnmatchedLiveModel = Boolean(liveModelAuditKey(message) && !auditKey(message))
    const hasPersistedOperation = Boolean(auditKey(message))
    while (nextAuditIndex < runAudits.length) {
      const audit = runAudits[nextAuditIndex]
      const auditSequence = Number.isFinite(audit?.sequence)
        ? audit.sequence
        : Number.MAX_SAFE_INTEGER
      if (!isUnmatchedLiveModel && (!hasPersistedOperation || auditSequence >= messageSequence)) break
      ordered.push(audit)
      emittedAudits.add(audit)
      nextAuditIndex += 1
    }
    ordered.push(message)

    if (lastMessageIndexByRun.get(runId) === messageIndex) {
      while (nextAuditIndex < runAudits.length) {
        const audit = runAudits[nextAuditIndex]
        ordered.push(audit)
        emittedAudits.add(audit)
        nextAuditIndex += 1
      }
    }
    nextAuditIndexByRun.set(runId, nextAuditIndex)
  })

  pending.forEach((audit) => {
    if (!emittedAudits.has(audit)) ordered.push(audit)
  })
  return ordered
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
    const runId = getMessageRunId(message)
    const operationId =
      typeof message?.operation_id === 'string' && message.operation_id.trim()
        ? message.operation_id.trim()
        : null
    const common = {
      id: operationId
        ? `${runId || 'unassigned'}:${auditRole(message) || 'unknown'}:${operationId}`
        : String(message?.id ?? `message-${index}`),
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
