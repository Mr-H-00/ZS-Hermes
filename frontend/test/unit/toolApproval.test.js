import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildToolApprovalDecisions,
  hasPendingInterruptPayload,
  isRunInterruptedConflict,
  isThreadWaitingForUserAction,
  isToolApprovalMode,
  resolveToolApprovalMode
} from '../../src/modules/session/model/toolApproval.js'

test('协作等待继续观察和排队输入，审批等待仍要求用户操作', () => {
  assert.equal(isThreadWaitingForUserAction({ turnStatus: 'in_progress', cooperationWaiting: true }), false)
  assert.equal(isThreadWaitingForUserAction({ turnStatus: 'requires_action', cooperationWaiting: false }), true)
})

test('tool approval modes and interrupt payloads follow their state contracts', () => {
  assert.equal(isToolApprovalMode('default'), true)
  assert.equal(isToolApprovalMode('always_trust'), true)
  assert.equal(isToolApprovalMode('unknown'), false)
  assert.equal(
    resolveToolApprovalMode({
      hasThread: false,
      savedMode: 'always_trust',
      agentMode: 'default'
    }),
    'always_trust'
  )
  assert.equal(
    resolveToolApprovalMode({
      hasThread: false,
      agentMode: 'always_trust'
    }),
    'always_trust'
  )
  assert.equal(
    resolveToolApprovalMode({
      hasThread: true,
      threadMode: 'default',
      savedMode: 'always_trust',
      agentMode: 'always_trust'
    }),
    'default'
  )
  assert.equal(
    resolveToolApprovalMode({
      hasThread: true,
      savedMode: 'always_trust',
      agentMode: 'always_trust'
    }),
    'always_trust'
  )

  assert.deepEqual(buildToolApprovalDecisions({ 0: 'approve', 1: 'reject' }, 2), [
    { type: 'approve' },
    { type: 'reject', message: '用户拒绝执行该操作' }
  ])
  assert.equal(hasPendingInterruptPayload({ kind: 'question', questions: [{}] }), true)
  assert.equal(hasPendingInterruptPayload({ kind: 'tool_approval', actionRequests: [{}] }), true)
  assert.equal(hasPendingInterruptPayload({ kind: 'tool_approval', actionRequests: [] }), false)
  assert.equal(
    isThreadWaitingForUserAction({
      pendingInterrupt: { kind: 'question', questions: [{ id: 'q-1' }] }
    }),
    true
  )
  assert.equal(isThreadWaitingForUserAction({ queueSnapshot: { status: 'running' } }), false)
  assert.equal(isThreadWaitingForUserAction({ turnStatus: 'requires_action', pendingInterrupt: null }), true)
  assert.equal(isThreadWaitingForUserAction({ turnStatus: 'in_progress', pendingInterrupt: null }), false)
  assert.equal(
    isThreadWaitingForUserAction({
      pendingInterrupt: null,
      queueSnapshot: { status: 'paused' }
    }),
    false
  )
  assert.equal(
    isRunInterruptedConflict({
      response: { status: 409, data: { detail: { code: 'run_interrupted' } } }
    }),
    true
  )
  assert.equal(isRunInterruptedConflict(new Error('线程正在等待用户回答或审批')), false)
})

test('前端默认完全信任，显式请求审批的会话和偏好保持不变', () => {
  assert.equal(resolveToolApprovalMode({ hasThread: false }), 'always_trust')
  assert.equal(resolveToolApprovalMode({ hasThread: false, agentMode: 'default' }), 'always_trust')
  assert.equal(resolveToolApprovalMode({ hasThread: false, savedMode: 'default' }), 'default')
  assert.equal(resolveToolApprovalMode({ hasThread: true, threadMode: 'default' }), 'default')
  assert.equal(resolveToolApprovalMode({ hasThread: true, threadMode: 'invalid' }), 'always_trust')
})
