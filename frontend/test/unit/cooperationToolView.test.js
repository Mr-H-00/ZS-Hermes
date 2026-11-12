import assert from 'node:assert/strict'
import test from 'node:test'
import { collectCooperationTasks, getCooperationToolTargets, groupCooperationEntries } from '../../src/modules/session/model/cooperationToolView.js'

const sessions = [
  { session_id: 'child-1', name: 'tester', path: '/root/tester' },
  { session_id: 'child-2', name: 'tester', path: '/root/other/tester' }
]
test('协作入口用精确回执匹配多个等待目标，相同名称保持不同会话', () => {
  const messages = [{ tool_calls: [
    { name: 'create_session', tool_call_result: { content: JSON.stringify({ session_id: 'child-1', input_id: 'input-1' }) } },
    { name: 'submit_input', tool_call_result: { content: { session_id: 'child-2', input_id: 'input-2' } } }
  ] }]
  const tasks = collectCooperationTasks(messages)
  assert.deepEqual(getCooperationToolTargets({ name: 'wait_inputs', args: { input_ids: ['input-1', 'input-2'] } }, { sessions, tasks }), [
    { sessionId: 'child-1', name: 'tester' }, { sessionId: 'child-2', name: 'tester' }
  ])
  assert.deepEqual(getCooperationToolTargets({ name: 'wait_sessions', args: { targets: ['/root/other/tester'] } }, { sessions, tasks }), [
    { sessionId: 'child-2', name: 'tester' }
  ])
})
test('未知任务不猜测会话，相同名称也不作为导航身份', () => {
  const context = { sessions, tasks: new Map() }
  assert.deepEqual(getCooperationToolTargets({ name: 'wait_inputs', args: { input_ids: ['missing'] } }, context), [{ sessionId: undefined, name: '' }])
  assert.deepEqual(getCooperationToolTargets({ name: 'submit_input', args: { target: 'tester' } }, context), [{ sessionId: undefined, name: '' }])
})

test('连续同类协作合并，推理、错误和不同动作保持原顺序与全部调用', () => {
  const entry = (id, name = 'create_session', status = 'success') => ({ type: 'tool', key: id, toolCall: { id, name, status } })
  const entries = [entry('a'), entry('b'), entry('c'), { type: 'reasoning', key: 'r', content: 'thinking' },
    entry('d'), entry('e', 'wait_inputs'), entry('f', 'wait_inputs', 'error'), entry('g', 'wait_inputs', 'error')]
  const groups = groupCooperationEntries(entries)
  assert.deepEqual(groups.map((item) => item.key), ['a', 'r', 'd', 'e', 'f', 'g'])
  assert.deepEqual(groups[0].toolCalls.map((call) => call.id), ['a', 'b', 'c'])
  assert.equal(groups[1], entries[3])
  assert.equal(groups[4].status, 'error')
  assert.equal(groups[5].status, 'error')
})

test('全用户会话中相同路径只解析当前协作树，稳定 ID 仍可跨树', () => {
  const context = { treeRootSessionId: 'current-root', tasks: new Map(), sessions: [
    { session_id: 'foreign-member', tree_root_session_id: 'other-root', path: '/root/worker', name: '同名外树' },
    { session_id: 'own-member', tree_root_session_id: 'current-root', path: '/root/worker', name: '当前成员' }
  ] }
  const targets = getCooperationToolTargets({ name: 'wait_sessions', args: { targets: ['/root/worker', 'foreign-member'] } }, context)
  assert.deepEqual(targets, [{ sessionId: 'own-member', name: '当前成员' }, { sessionId: 'foreign-member', name: '同名外树' }])
})

test('成功与超时等待保持分组边界，各目标保留自己的结局', () => {
  for (const name of ['wait_inputs', 'wait_sessions']) {
    const entries = [false, true, true, false].map((waitTimedOut, index) => ({
      type: 'tool', key: `${index}`, toolCall: { name, status: 'success',
        tool_call_result: { content: { wait_timed_out: waitTimedOut } } }
    }))
    const groups = groupCooperationEntries(entries)
    assert.deepEqual(groups.map(group => group.toolCalls.length), [1, 2, 1])
    assert.deepEqual(groups.map(group => group.waitTimedOut), [false, true, false])
  }
})
