import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildRunTimingRows,
  formatRunTimingDuration,
  formatRunTimingSummary
} from '../../src/utils/runTiming.js'

test('运行时延使用紧凑且稳定的单位', () => {
  assert.equal(formatRunTimingDuration(235), '235ms')
  assert.equal(formatRunTimingDuration(1530), '1.5s')
  assert.equal(formatRunTimingDuration(27776), '28s')
  assert.equal(formatRunTimingDuration(110128), '1m 50s')
  assert.equal(formatRunTimingDuration(119600), '2m 0s')
  assert.equal(formatRunTimingDuration(-1), '')
  assert.equal(formatRunTimingDuration(null), '')
})

test('运行时延只展示 PostgreSQL 已持久化的可用阶段', () => {
  const rows = buildRunTimingRows({
    dispatch_latency_ms: 200,
    preparation_latency_ms: 800,
    model_first_output_latency_ms: null,
    first_output_latency_ms: 7500,
    total_latency_ms: undefined
  })

  assert.deepEqual(
    rows.map(({ key, label, formattedValue }) => ({ key, label, formattedValue })),
    [
      { key: 'dispatch_latency_ms', label: '调度等待', formattedValue: '200ms' },
      { key: 'preparation_latency_ms', label: '运行准备', formattedValue: '800ms' },
      { key: 'first_output_latency_ms', label: '首次输出', formattedValue: '7.5s' }
    ]
  )
})

test('摘要优先展示首次输出并兼容没有首输出的历史 Run', () => {
  assert.equal(
    formatRunTimingSummary({ first_output_latency_ms: 7533, total_latency_ms: 43670 }),
    '首输出 7.5s'
  )
  assert.equal(
    formatRunTimingSummary({ first_output_latency_ms: null, total_latency_ms: 43670 }),
    '耗时 44s'
  )
  assert.equal(formatRunTimingSummary(null), '')
})
