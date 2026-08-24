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

/** 在原历史位置替换 active run 的流式 AI 投影，并保留其它消息相对顺序。 */
export function mergeMessageDebugMessages(history, ongoing, activeRunId = null) {
  const persisted = Array.isArray(history) ? history : []
  const live = Array.isArray(ongoing) ? ongoing : []
  const persistedIds = new Set(persisted.map((message) => String(message?.id ?? '')))
  const persistedRequestIds = new Set(
    persisted
      .filter((message) => message?.type === 'human')
      .map(messageRequestId)
      .filter(Boolean)
  )
  const pending = live.filter((message) => {
    if (persistedIds.has(String(message?.id ?? ''))) return false
    return message?.type !== 'human' || !persistedRequestIds.has(messageRequestId(message))
  })

  if (!activeRunId) return [...persisted, ...pending]

  const liveAi = pending.filter((message) => message?.type === 'ai')
  let replacedAiCount = 0
  const merged = []
  for (const message of persisted) {
    if (message?.type === 'ai' && messageRunId(message) === activeRunId) {
      if (replacedAiCount < liveAi.length) {
        merged.push(liveAi[replacedAiCount])
        replacedAiCount += 1
      } else {
        merged.push(message)
      }
      continue
    }
    merged.push(message)
  }

  let skippedAiCount = 0
  const remainingLive = pending.filter((message) => {
    if (message?.type !== 'ai' || skippedAiCount >= replacedAiCount) return true
    skippedAiCount += 1
    return false
  })
  return [...merged, ...remainingLive]
}

/** 保持输入顺序，将原始历史消息转换为调试列表条目。 */
export function buildMessageDebugEntries(messages) {
  const source = Array.isArray(messages) ? messages : []

  return source.map((message, index) => {
    const id = String(message?.id ?? `message-${index}`)
    const type = message?.type || message?.role || 'unknown'
    const usage = message?.usage || message?.extra_metadata?.usage
    const model = message?.model || message?.extra_metadata?.model || ''
    const isError = Boolean(message?.error_type || message?.error_message)

    if (type === 'human' || type === 'user') {
      return {
        id,
        role: 'human',
        roleLabel: 'User',
        model,
        usage,
        summary: summarizeContent(message.content, 200) || '[用户输入/附件]',
        raw: message
      }
    }

    if (type === 'system') {
      return {
        id,
        role: 'system',
        roleLabel: 'System',
        model,
        usage,
        summary: summarizeContent(message.content, 200) || '[系统消息]',
        raw: message
      }
    }

    if (type === 'tool') {
      const toolName = message?.name || message?.tool_name || message?.function?.name
      const content = summarizeContent(message.content, 180)
      return {
        id,
        role: 'tool',
        roleLabel: 'Tool',
        model,
        usage,
        summary: [toolName ? `工具: ${toolName}` : '', content].filter(Boolean).join(' | ') || '[工具结果]',
        raw: message
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
        id,
        role: isError ? 'error' : 'ai',
        roleLabel: isError ? 'Error' : toolNames.length ? 'AI · Tools' : 'AI',
        model,
        usage,
        summary,
        raw: message
      }
    }

    return {
      id,
      role: 'other',
      roleLabel: String(type),
      model,
      usage,
      summary: summarizeContent(message?.content, 200) || '[未知消息]',
      raw: message
    }
  })
}
