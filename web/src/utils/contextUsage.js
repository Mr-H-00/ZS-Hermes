/**
 * 上下文 Token 用量与环形指示器辅助工具
 */

/**
 * 格式化 Token 数量（如 51.8K、168.0K、2.5M）
 * @param {number|string|null|undefined} value
 * @returns {string}
 */
export function formatContextToken(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) return '0'
  if (numeric >= 1_000_000) {
    return `${(numeric / 1_000_000).toFixed(1)}M`
  }
  if (numeric >= 1_000) {
    return `${(numeric / 1_000).toFixed(1)}K`
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
