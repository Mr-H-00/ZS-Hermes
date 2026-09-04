import assert from 'node:assert/strict'
import test from 'node:test'

import {
  normalizeAgent,
  normalizeAgentBackendOption
} from '../../src/utils/agentConfigUtils.js'

test('normalizeAgent 按 agent_id、slug、id 顺序统一身份字段', () => {
  const withAllIds = { agent_id: 'agent-id', slug: 'agent-slug', id: 'database-id' }
  assert.deepEqual(normalizeAgent(withAllIds), {
    agent_id: 'agent-id',
    slug: 'agent-slug',
    id: 'agent-id'
  })
  assert.equal(normalizeAgent({ slug: 'agent-slug', id: 'database-id' }).id, 'agent-slug')
  assert.deepEqual(normalizeAgent({ id: 'database-id' }), {
    id: 'database-id',
    agent_id: 'database-id',
    slug: 'database-id'
  })
  const withoutId = { name: '无身份字段' }
  assert.strictEqual(normalizeAgent(withoutId), withoutId)
})

test('normalizeAgentBackendOption 缺少名称时回退到 backend_id', () => {
  assert.deepEqual(normalizeAgentBackendOption({ backend_id: 'ChatbotAgent' }), {
    label: 'ChatbotAgent',
    value: 'ChatbotAgent'
  })
  assert.deepEqual(
    normalizeAgentBackendOption({ backend_id: 'ChatbotAgent', name: '对话智能体' }),
    { label: '对话智能体', value: 'ChatbotAgent' }
  )
})
