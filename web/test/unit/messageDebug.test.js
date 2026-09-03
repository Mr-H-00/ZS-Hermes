import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildMessageDebugEntries,
  extractMessageToolNames,
  formatAuditDuration,
  getMessageRequestId,
  getMessageRunId,
  groupMessageDebugEntries,
  mergeMessageDebugAudits,
  mergeMessageDebugMessages,
  resolveLangfuseRunUrl
} from '../../src/utils/messageDebug.js'

test('消息身份按 metadata 优先，并显式控制 human id fallback', () => {
  const message = {
    id: 'human-1',
    type: 'human',
    request_id: 'request-direct',
    run_id: 'run-direct',
    extra_metadata: {
      request_id: 'request-meta',
      run_id: 'run-meta'
    }
  }

  assert.equal(getMessageRequestId(message), 'request-meta')
  assert.equal(getMessageRunId(message), 'run-meta')
  assert.equal(getMessageRequestId({ id: 'human-2', type: 'human' }), null)
  assert.equal(
    getMessageRequestId({ id: 'human-2', type: 'human' }, { allowMessageIdFallback: true }),
    'human-2'
  )
})

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

test('Langfuse Run 地址仅接受后端确认的 HTTP(S) URL', () => {
  assert.equal(
    resolveLangfuseRunUrl({
      available: true,
      url: 'https://langfuse.example/project/project-1/traces/trace-1'
    }),
    'https://langfuse.example/project/project-1/traces/trace-1'
  )
  assert.equal(resolveLangfuseRunUrl({ available: true, url: 'javascript:alert(1)' }), null)
  assert.equal(
    resolveLangfuseRunUrl({ available: false, url: 'https://langfuse.example/trace-1' }),
    null
  )
})

test('没有稳定身份时不按 AI 位置替换实时投影', () => {
  const history = [
    { id: 'user-1', type: 'human', request_id: 'request-1' },
    { id: 'ai-db', type: 'ai', run_id: 'run-1', content: '中间投影' },
    { id: 'tool-1', type: 'tool', run_id: 'run-1', content: '工具结果' }
  ]
  const ongoing = [{ id: 'ai-live', type: 'ai', run_id: 'run-1', content: '流式投影' }]

  const merged = mergeMessageDebugMessages(history, ongoing)

  assert.deepEqual(
    merged.map((message) => message.id),
    ['user-1', 'ai-db', 'tool-1', 'ai-live']
  )
})

test('active run 没有流式 AI 时保留持久化 AI', () => {
  const history = [
    { id: 'user-1', type: 'human' },
    { id: 'ai-db', type: 'ai', run_id: 'run-1', content: '持久化内容' }
  ]

  const merged = mergeMessageDebugMessages(history, [])

  assert.deepEqual(
    merged.map((message) => message.id),
    ['user-1', 'ai-db']
  )
})

test('同 request_id 的实时 User 将 Run 关联补入旧持久快照', () => {
  const history = [
    {
      id: 41,
      type: 'human',
      request_id: 'request-1',
      content: '快排',
      extra_metadata: { request_id: 'request-1' }
    }
  ]
  const ongoing = [
    {
      id: 'request-1',
      type: 'human',
      run_id: 'run-1',
      content: '快排',
      extra_metadata: { request_id: 'request-1', run_id: 'run-1' }
    }
  ]

  const merged = mergeMessageDebugMessages(history, ongoing)

  assert.equal(merged.length, 1)
  assert.equal(merged[0].id, 41)
  assert.equal(merged[0].run_id, 'run-1')
  assert.equal(merged[0].extra_metadata.run_id, 'run-1')
})

test('active run 尚无持久化 AI 时保持流式 Human 到 AI 的顺序', () => {
  const ongoing = [
    { id: 'user-live', type: 'human', request_id: 'request-live' },
    { id: 'ai-live', type: 'ai', run_id: 'run-live' }
  ]

  const merged = mergeMessageDebugMessages([], ongoing)

  assert.deepEqual(
    merged.map((message) => message.id),
    ['user-live', 'ai-live']
  )
})

test('Model 审计按稳定 operation 合并并按 sequence 插入隐藏调用', () => {
  const messages = [
    { id: 'user-1', type: 'human', run_id: 'run-1', content: '开始' },
    { id: 21, type: 'ai', run_id: 'run-1', content: '流式工具调用' },
    { id: 23, type: 'ai', run_id: 'run-1', content: '最终回答' }
  ]
  const audits = [
    {
      id: 21,
      type: 'ai',
      run_id: 'run-1',
      operation_id: 'operation-1',
      sequence: 3,
      content: '持久化工具调用',
      execution_status: 'completed'
    },
    {
      id: 22,
      type: 'ai',
      run_id: 'run-1',
      operation_id: 'operation-2',
      sequence: 7,
      content: '隐藏的中间调用',
      execution_status: 'completed'
    },
    {
      id: 23,
      type: 'ai',
      run_id: 'run-1',
      operation_id: 'operation-3',
      sequence: 10,
      content: '最终回答',
      execution_status: 'completed'
    }
  ]

  const merged = mergeMessageDebugAudits(messages, audits)

  assert.deepEqual(
    merged.map((message) => message.operation_id || message.id),
    ['user-1', 'operation-1', 'operation-2', 'operation-3']
  )
  assert.equal(merged[1].content, '流式工具调用')
})

test('审计快照落后于 SSE 时仍按已知 sequence 放置持久操作', () => {
  const messages = mergeMessageDebugMessages(
    [{ id: 21, type: 'ai', run_id: 'run-1', content: 'operation 1' }],
    [{ id: 'operation-3', type: 'ai', run_id: 'run-1', content: 'operation 3 live' }]
  )
  const audits = [
    {
      id: 21,
      type: 'ai',
      run_id: 'run-1',
      operation_id: 'operation-1',
      sequence: 3,
      content: 'operation 1'
    },
    {
      id: 22,
      type: 'ai',
      run_id: 'run-1',
      operation_id: 'operation-2',
      sequence: 7,
      content: 'operation 2'
    }
  ]

  const merged = mergeMessageDebugAudits(messages, audits)

  assert.deepEqual(
    merged.map((message) => message.operation_id || message.id),
    ['operation-1', 'operation-2', 'operation-3']
  )
})

test('同一 Model 的历史行和实时投影按稳定 operation 合并为一条', () => {
  const messages = [
    { id: 42, type: 'ai', run_id: 'run-1', content: '持久化内容' },
    { id: 'operation-1', type: 'ai', run_id: 'run-1', content: '更完整的实时内容' }
  ]
  const audits = [
    {
      id: 42,
      type: 'ai',
      run_id: 'run-1',
      operation_id: 'operation-1',
      content: '',
      execution_status: 'running'
    }
  ]

  const merged = mergeMessageDebugAudits(messages, audits)

  assert.equal(merged.length, 1)
  assert.equal(merged[0].operation_id, 'operation-1')
  assert.equal(merged[0].content, '更完整的实时内容')
})

test('实时 Model 投影只按同一 Run 的 operation id 合并', () => {
  const messages = [
    { id: 'shared-operation', type: 'ai', run_id: 'run-other', content: '其它 Run' },
    { id: 'shared-operation', type: 'ai', run_id: 'run-target', content: '实时内容' }
  ]
  const audits = [
    {
      id: 42,
      type: 'ai',
      run_id: 'run-target',
      operation_id: 'shared-operation',
      content: '',
      duration_ms: 0,
      execution_status: 'running'
    }
  ]

  const merged = mergeMessageDebugAudits(messages, audits)

  assert.equal(merged[0].operation_id, undefined)
  assert.equal(merged[1].operation_id, 'shared-operation')
  assert.equal(merged[1].content, '实时内容')
  const entry = buildMessageDebugEntries([merged[1]])[0]
  assert.equal(entry.id, 'run-target:assistant:shared-operation')
  assert.equal(entry.roleLabel, 'Model')
  assert.equal(entry.durationMs, 0)
  assert.equal(entry.executionStatus, 'running')
})

test('同 Run 同 operation id 的 Model 与 Tool 审计保持独立', () => {
  const messages = [
    {
      id: 'shared-operation',
      type: 'ai',
      run_id: 'run-1',
      content: '实时模型输出'
    }
  ]
  const audits = [
    {
      id: 10,
      type: 'ai',
      run_id: 'run-1',
      operation_id: 'shared-operation',
      sequence: 3,
      content: '模型输出'
    },
    {
      id: 11,
      type: 'tool',
      run_id: 'run-1',
      operation_id: 'shared-operation',
      sequence: 6,
      content: '工具输出'
    }
  ]

  const merged = mergeMessageDebugAudits(messages, audits)
  const entries = buildMessageDebugEntries(merged)

  assert.equal(merged.length, 2)
  assert.equal(merged[0].content, '实时模型输出')
  assert.equal(merged[0].type, 'ai')
  assert.equal(merged[1].content, '工具输出')
  assert.equal(merged[1].type, 'tool')
  assert.deepEqual(
    entries.map((entry) => entry.id),
    ['run-1:assistant:shared-operation', 'run-1:tool:shared-operation']
  )
})

test('Model 与 Tool 审计按 sequence 形成交错时间线并展示真实工具事实', () => {
  const messages = [{ id: 'user-1', type: 'human', run_id: 'run-1', content: '查询' }]
  const audits = [
    {
      id: 10,
      type: 'ai',
      run_id: 'run-1',
      operation_id: 'model-1',
      sequence: 3,
      execution_status: 'completed'
    },
    {
      id: 11,
      type: 'tool',
      run_id: 'run-1',
      operation_id: 'call-1',
      tool_name: 'search',
      tool_input: { q: 'Yuxi' },
      content: '查询结果',
      sequence: 6,
      duration_ms: 125,
      execution_status: 'completed'
    },
    {
      id: 12,
      type: 'ai',
      run_id: 'run-1',
      operation_id: 'model-2',
      sequence: 9,
      execution_status: 'running'
    }
  ]

  const merged = mergeMessageDebugAudits(messages, audits)
  const entries = buildMessageDebugEntries(merged)

  assert.deepEqual(
    entries.map((entry) => entry.operationId || entry.id),
    ['user-1', 'model-1', 'call-1', 'model-2']
  )
  assert.equal(entries[2].role, 'tool')
  assert.equal(entries[2].roleLabel, 'Tool · search')
  assert.equal(entries[2].summary, '输出: 查询结果')
  assert.equal(entries[2].durationMs, 125)
  assert.equal(entries[2].executionStatus, 'completed')
})

test('失败 Tool 审计展示错误而不把 running wall-clock 推算成耗时', () => {
  const [entry] = buildMessageDebugEntries([
    {
      id: 11,
      type: 'tool',
      run_id: 'run-1',
      operation_id: 'call-error',
      tool_name: 'search',
      tool_input: { q: 'Yuxi' },
      error_message: 'provider unavailable',
      execution_status: 'failed'
    }
  ])

  assert.equal(entry.summary, '错误: provider unavailable')
  assert.equal(entry.durationMs, null)
})

test('Model/Tool monotonic 耗时在分钟边界正确进位', () => {
  assert.equal(formatAuditDuration(675), '675 ms')
  assert.equal(formatAuditDuration(1120), '1.12 s')
  assert.equal(formatAuditDuration(60_000), '1m 0s')
  assert.equal(formatAuditDuration(119_600), '2m 0s')
  assert.equal(formatAuditDuration(null), '')
})

test('大量未匹配审计按 sequence 一次合并', () => {
  const auditCount = 2000
  const audits = Array.from({ length: auditCount }, (_, index) => ({
    id: index + 1,
    type: 'ai',
    run_id: 'run-large',
    operation_id: `operation-${index + 1}`,
    sequence: index + 1
  }))
  const messages = [
    { id: 'user-large', type: 'human', run_id: 'run-large' },
    {
      id: 'operation-live',
      type: 'ai',
      run_id: 'run-large',
      content: '实时输出'
    }
  ]

  const merged = mergeMessageDebugAudits(messages, audits)

  assert.equal(merged.length, auditCount + 2)
  assert.equal(merged[0].id, 'user-large')
  assert.equal(merged[1].operation_id, 'operation-1')
  assert.equal(merged[auditCount].operation_id, `operation-${auditCount}`)
  assert.equal(merged.at(-1).id, 'operation-live')
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
