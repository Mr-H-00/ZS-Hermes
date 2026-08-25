import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  isSubagentLaunchToolName,
  mergeSubagentRunsForDisplay
} from '../../src/utils/subagentRuns.js'

test('同步 task 和异步 subagent_start 都属于子智能体启动调用', () => {
  assert.equal(isSubagentLaunchToolName('task'), true)
  assert.equal(isSubagentLaunchToolName('subagent_start'), true)
  assert.equal(isSubagentLaunchToolName('subagent_status'), false)
})

test('同一 child_thread_id 只显示最新运行并保留已有任务描述', () => {
  const runs = mergeSubagentRunsForDisplay([
    {
      id: 'tool-1',
      run_id: 'run-1',
      child_thread_id: 'child-thread-1',
      description: '整理调研资料',
      status: 'completed'
    },
    {
      id: 'tool-2',
      run_id: 'run-2',
      child_thread_id: 'child-thread-1',
      status: 'running'
    }
  ])

  assert.equal(runs.length, 1)
  assert.equal(runs[0].run_id, 'run-2')
  assert.equal(runs[0].status, 'running')
  assert.equal(runs[0].description, '整理调研资料')
})

test('任务描述从 task 调用回填，缺失时显示 child_thread_id', () => {
  const descriptions = new Map([['tool-1', '生成交付报告']])
  const runs = mergeSubagentRunsForDisplay(
    [
      {
        id: 'tool-1',
        child_thread_id: 'child-thread-1',
        description: '   ',
        status: 'completed'
      },
      { id: 'tool-2', child_thread_id: 'child-thread-2', status: 'completed' }
    ],
    descriptions
  )

  assert.equal(runs[0].description, '生成交付报告')
  assert.equal(runs[1].description, 'child-thread-2')
})

test('异步启动继续同一子线程时显示最新一次输入描述', () => {
  const descriptions = new Map([
    ['start-tool-1', '开始收集资料'],
    ['start-tool-2', '继续整理报告']
  ])
  const runs = mergeSubagentRunsForDisplay(
    [
      {
        id: 'start-tool-1',
        run_id: 'run-1',
        child_thread_id: 'child-thread-1',
        status: 'completed'
      },
      {
        id: 'start-tool-2',
        run_id: 'run-2',
        child_thread_id: 'child-thread-1',
        status: 'running'
      }
    ],
    descriptions
  )

  assert.equal(runs.length, 1)
  assert.equal(runs[0].run_id, 'run-2')
  assert.equal(runs[0].description, '继续整理报告')
})

test('历史会话从 messages 分组收集子智能体任务描述', () => {
  const source = readFileSync(
    new URL('../../src/components/AgentChatComponent.vue', import.meta.url),
    'utf8'
  )
  const collector = source.slice(
    source.indexOf('const subagentDescriptionByToolCallId'),
    source.indexOf('// 先按真实 run')
  )

  assert.match(
    collector,
    /historyConversations\.value\.forEach\(\(conversation\) => collect\(conversation\?\.messages\)\)/
  )
})

test('尚未获得 child_thread_id 的流式任务不会被误合并', () => {
  const runs = mergeSubagentRunsForDisplay([
    { id: 'tool-1', status: 'running' },
    { id: 'tool-2', status: 'running' }
  ])

  assert.equal(runs.length, 2)
  assert.deepEqual(
    runs.map((run) => run.description),
    ['tool-1', 'tool-2']
  )
})
