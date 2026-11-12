import assert from 'node:assert/strict'
import test from 'node:test'

import { formatChatTime } from '../../src/shared/lib/time.js'

// 固定"当前时间"避免跨日边界导致用例不稳定：2026-08-15 12:00（周六）
const NOW = '2026-08-15T12:00:00+08:00'

test('今天的时间只显示 HH:mm', () => {
  assert.equal(formatChatTime('2026-08-15T09:05:00+08:00', NOW), '09:05')
})

test('昨天显示"昨天 HH:mm"', () => {
  assert.equal(formatChatTime('2026-08-14T23:59:00+08:00', NOW), '昨天 23:59')
})

test('一周内显示周几加时间', () => {
  // 2026-08-12 是周三
  assert.equal(formatChatTime('2026-08-12T08:30:00+08:00', NOW), '周三 08:30')
})

test('一周前同年显示月-日与时间', () => {
  assert.equal(formatChatTime('2026-08-01T10:00:00+08:00', NOW), '08-01 10:00')
})

test('跨年显示年月日', () => {
  assert.equal(formatChatTime('2025-12-31T10:00:00+08:00', NOW), '2025-12-31')
})

test('无效输入返回空字符串', () => {
  assert.equal(formatChatTime(null, NOW), '')
  assert.equal(formatChatTime('', NOW), '')
})
