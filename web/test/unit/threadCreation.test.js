import assert from 'node:assert/strict'
import test from 'node:test'

import { createThreadForContext } from '../../src/utils/threadCreation.js'

test('延迟创建响应遇到上下文切换时不被接受', async () => {
  const context = { agentId: 'agent-a', projectId: 'auto', threadId: null }
  let currentContext = { ...context }
  let resolveRequest
  const response = new Promise((resolve) => {
    resolveRequest = resolve
  })

  const pending = createThreadForContext({
    context,
    getCurrentContext: () => currentContext,
    requestId: 'stable-request',
    create: async (requestId) => {
      assert.equal(requestId, 'stable-request')
      return response
    }
  })

  currentContext = { ...context, agentId: 'agent-b' }
  resolveRequest({ id: 'thread-a' })

  assert.deepEqual(await pending, { thread: { id: 'thread-a' }, accepted: false })
})
