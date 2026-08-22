import assert from 'node:assert/strict'
import test from 'node:test'
import { collapseConversationProcess, formatProcessDuration } from '../../src/utils/conversationProcessGrouping.js'

test('已完成对话将中间消息和工具调用聚合到过程组', () => {
    const items = collapseConversationProcess(
      [
        { key: 'h1', type: 'message', message: { type: 'human' } },
        { key: 'a1', type: 'message', message: { type: 'ai' } },
        { key: 'tools', type: 'tool-group', toolCalls: [{ id: 't1' }, { id: 't2' }] },
        {
          key: 'a3',
          type: 'message',
          message: {
            type: 'ai',
            run_started_at: '2026-08-20T00:00:00Z',
            run_finished_at: '2026-08-20T00:01:05Z'
          }
        }
      ],
      true
    )
    assert.deepEqual(items.map((item) => item.type), ['message', 'process-group', 'message'])
    assert.equal(items[1].messageCount, 1)
    assert.equal(items[1].toolCallCount, 2)
    assert.equal(items[1].durationMs, 65000)
})

test('运行中或最终消息后仍有工具调用时不聚合过程', () => {
  const items = [
    { key: 'h1', type: 'message', message: { type: 'human' } },
    { key: 'a1', type: 'message', message: { type: 'ai' } },
    { key: 'tools', type: 'tool-group', toolCalls: [{ id: 't1' }] }
  ]
  assert.equal(collapseConversationProcess(items).some((item) => item.type === 'process-group'), false)
  assert.equal(
    collapseConversationProcess(items, true).some((item) => item.type === 'process-group'),
    false
  )
})

test('formatProcessDuration: 保持分钟和秒数格式', () => {
  assert.equal(formatProcessDuration(0), '处理过程')
  assert.equal(formatProcessDuration(null), '处理过程')
  assert.equal(formatProcessDuration(undefined), '处理过程')
  assert.equal(formatProcessDuration(5000), '耗时0分钟5秒')
  assert.equal(formatProcessDuration(59000), '耗时0分钟59秒')
  assert.equal(formatProcessDuration(60000), '耗时1分钟0秒')
  assert.equal(formatProcessDuration(65000), '耗时1分钟5秒')
  assert.equal(formatProcessDuration(125000), '耗时2分钟5秒')
})
