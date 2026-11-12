/**
 * 上下文 Token 用量与环形指示器辅助工具
 */

/**
 * 格式化 Token 数量（如 51.8K、168K、2.5M）
 * @param {number|string|null|undefined} value
 * @returns {string}
 */
export function formatContextToken(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) return '0'
  if (numeric >= 1_000_000) {
    return `${Number((numeric / 1_000_000).toPrecision(3))}M`
  }
  if (numeric >= 1_000) {
    return `${Number((numeric / 1_000).toPrecision(3))}K`
  }
  return String(Math.round(numeric))
}

/**
 * 计算上下文使用比例 [0, 1]
 * @param {number|null} usedTokens
 * @param {number|null} limitTokens
 * @param {number|null} explicitRatio
 * @returns {number}
 */
export function calculateContextRatio(usedTokens, limitTokens, explicitRatio = null) {
  if (
    explicitRatio !== null &&
    explicitRatio !== undefined &&
    Number.isFinite(Number(explicitRatio))
  ) {
    return Math.max(0, Math.min(Number(explicitRatio), 1))
  }
  const numericLimit = Number(limitTokens)
  if (Number.isFinite(numericLimit) && numericLimit > 0) {
    const numericUsed = Math.max(0, Number(usedTokens) || 0)
    return Math.max(0, Math.min(numericUsed / numericLimit, 1))
  }
  return 0
}

/**
 * 生成上下文使用悬浮提示文本
 * 示例：30.8% · 51.8K/168.0K 上下文已使用
 * @param {Object} options
 * @param {number|null} [options.usedTokens]
 * @param {number|null} [options.limitTokens]
 * @param {number|null} [options.ratio]
 * @param {string} [options.customTitle]
 * @returns {string}
 */
export function formatContextUsageTooltip({
  usedTokens = 0,
  limitTokens = null,
  ratio = null,
  customTitle = ''
} = {}) {
  if (customTitle) return customTitle

  const computedRatio = calculateContextRatio(usedTokens, limitTokens, ratio)
  const percentText = `${(computedRatio * 100).toFixed(1)}%`
  const usedText = formatContextToken(usedTokens || 0)

  const numericLimit = Number(limitTokens)
  if (Number.isFinite(numericLimit) && numericLimit > 0) {
    const limitText = formatContextToken(numericLimit)
    return `${percentText} · ${usedText}/${limitText} 上下文已使用`
  }

  const numericUsed = Number(usedTokens)
  if (Number.isFinite(numericUsed) && numericUsed > 0) {
    return `${usedText} 上下文已使用`
  }

  return '0.0% · 0 上下文已使用'
}

/**
 * 获取上下文使用占比对应的色调类名
 * @param {number} ratio
 * @returns {'is-danger'|'is-warning'|'is-normal'}
 */
export function getContextUsageTone(ratio) {
  if (ratio >= 0.9) return 'is-danger'
  if (ratio >= 0.75) return 'is-warning'
  return 'is-normal'
}

/**
 * 选择完成当前回复后、下一轮实际会承受的压力估算。
 * @param {Object|null|undefined} usage
 * @returns {number|null}
 */
export function resolveContextPressureTokens(usage) {
  if (!usage || typeof usage !== 'object') return null
  const value = usage.next_llm_input_tokens ?? usage.llm_input_tokens
  if (value === null || value === undefined || value === '') return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? Math.max(numeric, 0) : null
}

/**
 * 85% 是只用于提示的派生线，不参与自动压缩判断。
 * @param {number|null} ratio
 * @returns {boolean}
 */
export function shouldSuggestContextCompression(ratio) {
  return Number.isFinite(Number(ratio)) && Number(ratio) >= 0.85
}

/** 将可用的遥测数值转为有限数。 */
export const toFiniteNumber = (value) => {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean')
    return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}
const TOKEN_COUNT_K_UNIT = 1024
const TOKEN_COUNT_M_UNIT = TOKEN_COUNT_K_UNIT * 1000
/** 保留用量详情的 1024 Token 展示单位。 */
export const formatTokenCount = (value) => {
  const numeric = toFiniteNumber(value)
  if (numeric === null) return '-'
  if (numeric >= TOKEN_COUNT_M_UNIT) {
    return `${Number((numeric / TOKEN_COUNT_M_UNIT).toPrecision(3))}M`
  }
  if (numeric >= TOKEN_COUNT_K_UNIT) {
    return `${Number((numeric / TOKEN_COUNT_K_UNIT).toPrecision(3))}K`
  }
  return String(Math.round(numeric))
}

/** 计算最近上下文构成，保留旧遥测的消息回退。 */
export function getContextUsageSegments(usage) {
  if (!usage) return []

  const summaryTokens = usage.summary_active
    ? Math.max(toFiniteNumber(usage.summary_message_tokens) || 0, 0)
    : 0
  const llmMessageTokens = Math.max(toFiniteNumber(usage.llm_messages_tokens) || 0, 0)
  const hasSplitMessageTokens =
    toFiniteNumber(usage.llm_content_message_tokens) !== null ||
    toFiniteNumber(usage.llm_tool_message_tokens) !== null
  const contentMessageTokens = hasSplitMessageTokens
    ? Math.max(toFiniteNumber(usage.llm_content_message_tokens) || 0, 0)
    : Math.max(llmMessageTokens - summaryTokens, 0)
  const toolMessageTokens = Math.max(toFiniteNumber(usage.llm_tool_message_tokens) || 0, 0)
  const stateMessageTokensBeforeCall = Math.max(
    toFiniteNumber(usage.state_messages_tokens_before_call ?? usage.state_messages_tokens) || 0,
    0
  )
  const cutMessageTokens = Math.max(stateMessageTokensBeforeCall - llmMessageTokens, 0)
  const llmMessageCount = Math.max(toFiniteNumber(usage.llm_message_count) || 0, 0)
  const contentMessageCount = hasSplitMessageTokens
    ? Math.max(toFiniteNumber(usage.llm_content_message_count) || 0, 0)
    : Math.max(llmMessageCount - (usage.summary_active ? 1 : 0), 0)
  const toolMessageCount = Math.max(toFiniteNumber(usage.llm_tool_message_count) || 0, 0)
  const stateMessageCountBeforeCall = Math.max(
    toFiniteNumber(usage.state_message_count_before_call ?? usage.state_message_count) || 0,
    0
  )
  const cutMessageCount = Math.max(stateMessageCountBeforeCall - llmMessageCount, 0)
  const systemTokens = Math.max(toFiniteNumber(usage.system_tokens) || 0, 0)
  const toolsTokens = Math.max(toFiniteNumber(usage.tools_tokens) || 0, 0)
  const inputTokens = Math.max(toFiniteNumber(usage.llm_input_tokens) || 0, 0)
  const rawSegments = [
    {
      key: 'system',
      label: '系统提示',
      value: systemTokens,
      tone: 'is-system'
    },
    {
      key: 'tools',
      label: `工具定义 (${usage.tool_count || 0})`,
      value: toolsTokens,
      tone: 'is-tools'
    },
    {
      key: 'messages',
      label: contentMessageCount > 0 ? `内容消息 (${contentMessageCount})` : '内容消息',
      value: contentMessageTokens,
      messageCount: contentMessageCount,
      tone: 'is-messages'
    },
    {
      key: 'toolMessages',
      label: toolMessageCount > 0 ? `工具消息 (${toolMessageCount})` : '工具消息',
      value: toolMessageTokens,
      messageCount: toolMessageCount,
      tone: 'is-tool-messages'
    },
    {
      key: 'summary',
      label: '摘要',
      value: summaryTokens,
      messageCount: usage.summary_active ? 1 : 0,
      tone: 'is-summary'
    },
    {
      key: 'cut',
      label: cutMessageCount > 0 ? `已压缩 (${cutMessageCount})` : '已压缩',
      value: cutMessageTokens,
      messageCount: cutMessageCount,
      tone: 'is-cut'
    }
  ].filter((segment) => segment.value > 0)

  const accountedInputTokens = llmMessageTokens + systemTokens + toolsTokens
  if (inputTokens > accountedInputTokens) {
    rawSegments.push({
      key: 'overhead',
      label: '其他',
      value: inputTokens - accountedInputTokens,
      tone: 'is-overhead'
    })
  }

  const segmentTotal = rawSegments.reduce((sum, segment) => sum + segment.value, 0)
  const total = Math.max(cutMessageTokens + inputTokens, segmentTotal, 1)
  return rawSegments.map((segment) => {
    const ratio = segment.value / total
    return {
      ...segment,
      percent: `${Math.max(0, Math.min(ratio * 100, 100)).toFixed(2)}%`,
      valueLabel: formatTokenCount(segment.value)
    }
  })
}

/** 输入环与详情面板共用的上下文占用事实。 */
export function resolveContextUsageSummary(usage) {
  const inputTokens = toFiniteNumber(usage?.llm_input_tokens)
  const stackTotal =
    inputTokens === null
      ? getContextUsageSegments(usage)
          .filter((segment) => segment.key !== 'cut')
          .reduce((sum, segment) => sum + segment.value, 0)
      : Math.max(inputTokens, 0)
  const estimate = resolveContextPressureTokens(usage)
  const usedTokens = estimate === null ? stackTotal : estimate
  const trigger = toFiniteNumber(usage?.summary_trigger_tokens)
  const window = toFiniteNumber(usage?.context_window)
  const limitTokens = trigger > 0 ? trigger : window > 0 ? window : null
  const ratio =
    limitTokens === null || estimate === null
      ? null
      : Math.max(0, Math.min(usedTokens / limitTokens, 1))
  return { usedTokens, limitTokens, ratio, stackTotal }
}
