const DEFAULT_BASE_DELAY = 1000
const DEFAULT_MAX_DELAY = 30000

/** 计算有界指数退避延迟，避免 SSE 故障时固定频率重连。 */
export function getReconnectDelay(
  attempt,
  { baseDelay = DEFAULT_BASE_DELAY, maxDelay = DEFAULT_MAX_DELAY } = {}
) {
  const safeAttempt = Number.isSafeInteger(attempt) && attempt >= 0 ? attempt : 0
  const safeBaseDelay = Number.isFinite(baseDelay) && baseDelay >= 0 ? baseDelay : DEFAULT_BASE_DELAY
  const safeMaxDelay = Number.isFinite(maxDelay) && maxDelay >= safeBaseDelay
    ? maxDelay
    : DEFAULT_MAX_DELAY
  return Math.min(safeMaxDelay, safeBaseDelay * 2 ** safeAttempt)
}
