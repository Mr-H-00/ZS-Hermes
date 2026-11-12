import assert from 'node:assert/strict'
import test from 'node:test'
import { getReconnectDelay } from '../../src/modules/session/model/reconnectBackoff.js'

test('SSE 重连延迟按指数增长并封顶', () => {
  assert.deepEqual(
    [0, 1, 2, 3, 10].map((attempt) => getReconnectDelay(attempt)),
    [1000, 2000, 4000, 8000, 30000]
  )
})

test('非法重连参数回退到安全默认值', () => {
  assert.equal(getReconnectDelay(-1), 1000)
  assert.equal(getReconnectDelay(Number.NaN), 1000)
  assert.equal(getReconnectDelay(2, { baseDelay: -1 }), 4000)
  assert.equal(getReconnectDelay(2, { baseDelay: 500, maxDelay: 100 }), 2000)
})
