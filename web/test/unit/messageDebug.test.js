import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildMessageDebugEntries,
  extractMessageToolNames,
  groupMessageDebugEntries,
  mergeMessageDebugMessages
} from '../../src/utils/messageDebug.js'

test('消息调试条目保持后端数组顺序并保留独立工具消息', () => {
  const history = [
    { id: 1, type: 'human', content: '请查询' },
    {
      id: 2,
      type: 'ai',
      content: '开始查询',
      tool_calls: [{ name: 'search_kb' }, { function: { name: 'read_file' } }]
    },
    { id: 3, type: 'tool', name: 'search_kb', content: '查询结果' },
    { id: 4, type: 'system', content: '系统提示' }
  ]

  const entries = buildMessageDebugEntries(history)

  assert.deepEqual(
    entries.map((entry) => entry.id),
    ['1', '2', '3', '4']
  )
  assert.deepEqual(
    entries.map((entry) => entry.role),
    ['human', 'ai', 'tool', 'system']
  )
  assert.equal(entries[1].summary, '开始查询 | 工具: search_kb、read_file')
  assert.equal(entries[2].summary, '工具: search_kb | 查询结果')
})

test('消息调试按连续 Run 分组且不猜测无 run_id 消息的归属', () => {
  const entries = buildMessageDebugEntries([
    { id: 'user-a', type: 'human', run_id: 'run-a', content: '问题 A' },
    { id: 'ai-a', type: 'ai', extra_metadata: { run_id: 'run-a' }, content: '回答 A' },
    { id: 'system', type: 'system', content: '未关联消息' },
    { id: 'user-b', type: 'human', run_id: 'run-b', content: '问题 B' }
  ])

  const groups = groupMessageDebugEntries(entries)

  assert.deepEqual(
    groups.map((group) => group.runId),
    ['run-a', null, 'run-b']
  )
  assert.deepEqual(
    groups.map((group) => group.items.map((entry) => entry.id)),
    [
      ['user-a', 'ai-a'],
      ['system'],
      ['user-b']
    ]
  )
})

test('active run 的流式 AI 在原位置替换且不越过后续工具消息', () => {
  const history = [
    { id: 'user-1', type: 'human', request_id: 'request-1' },
    { id: 'ai-db', type: 'ai', run_id: 'run-1', content: '中间投影' },
    { id: 'tool-1', type: 'tool', run_id: 'run-1', content: '工具结果' },
    { id: 'system-1', type: 'system', content: '系统消息' }
  ]
  const ongoing = [{ id: 'ai-live', type: 'ai', run_id: 'run-1', content: '流式投影' }]

  const merged = mergeMessageDebugMessages(history, ongoing, 'run-1')

  assert.deepEqual(
    merged.map((message) => message.id),
    ['user-1', 'ai-live', 'tool-1', 'system-1']
  )
})

test('active run 没有流式 AI 时保留持久化 AI', () => {
  const history = [
    { id: 'user-1', type: 'human' },
    { id: 'ai-db', type: 'ai', run_id: 'run-1', content: '持久化内容' }
  ]

  const merged = mergeMessageDebugMessages(history, [], 'run-1')

  assert.deepEqual(
    merged.map((message) => message.id),
    ['user-1', 'ai-db']
  )
})

test('active run 尚无持久化 AI 时保持流式 Human 到 AI 的顺序', () => {
  const ongoing = [
    { id: 'user-live', type: 'human', request_id: 'request-live' },
    { id: 'ai-live', type: 'ai', run_id: 'run-live' }
  ]

  const merged = mergeMessageDebugMessages([], ongoing, 'run-live')

  assert.deepEqual(
    merged.map((message) => message.id),
    ['user-live', 'ai-live']
  )
})

test('工具名称按多种消息字段解析并去重', () => {
  const names = extractMessageToolNames({
    tool_calls: [
      { name: 'search' },
      { tool_name: 'search' },
      { function: { name: 'read_file' } },
      {}
    ]
  })

  assert.deepEqual(names, ['search', 'read_file'])
})
