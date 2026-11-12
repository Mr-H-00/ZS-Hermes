import assert from 'node:assert/strict'
import test from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createRenderer, ssrContextKey } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

test('mention 的智能体与对话各显示前五个匹配项，对话排在智能体后且键盘顺序一致', async (t) => {
  const previousDocument = globalThis.document
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  let app
  try {
    const { configureApiAuth } = await server.ssrLoadModule('/src/apis/auth_context.js')
    const sessions = [
      { thread_id: 'current', title: '当前对话' },
      ...Array.from({ length: 8 }, (_, i) => ({ thread_id: `session-${i}`, title: `对话 ${i}` }))
    ]
    t.mock.method(globalThis, 'fetch', async (url) => {
      const request = new URL(url, 'http://localhost')
      assert.ok(['/api/mention/sessions', '/api/mention/search'].includes(request.pathname))
      const query = request.searchParams.get('query') || ''
      return Response.json(request.pathname === '/api/mention/sessions'
        ? sessions.filter((session) => session.title.includes(query))
        : [])
    })
    const { default: component } = await server.ssrLoadModule('/src/modules/session/ui/MessageInputComponent.vue')
    configureApiAuth({ getToken: () => 'test-token' })
    globalThis.document = { addEventListener() {}, removeEventListener() {} }
    const renderer = createRenderer({
      createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null, nextSibling: () => null
    })
    let state
    app = renderer.createApp({ setup() {
      state = component.setup({ modelValue: '', enableUploads: true, threadId: 'current', mention: {
        agents: Array.from({ length: 8 }, (_, i) => ({ agent_id: `agent-${i}`, name: `智能体 ${i}` })),
        knowledgeBases: [{ kb_id: 'kb', name: '知识库' }],
        mcps: [{ slug: 'mcp', name: 'MCP' }],
        skills: [{ slug: 'skill', name: 'Skill' }]
      } }, { expose() {}, emit() {} })
      return () => null
    } })
    app.provide(ssrContextKey, {})
    app.mount({})
    t.mock.timers.enable({ apis: ['setTimeout'] })

    state.updateMentionItems('')
    assert.deepEqual(state.mentionItems.value.agents.map((item) => item.value),
      ['agent-0', 'agent-1', 'agent-2', 'agent-3', 'agent-4'])
    t.mock.timers.tick(250)
    await setImmediate()
    assert.equal(state.sessionSearchLoading.value, false)
    assert.equal(state.sessionSearchError.value, false)
    assert.deepEqual(state.mentionItems.value.sessions.map((item) => item.value),
      ['session-0', 'session-1', 'session-2', 'session-3', 'session-4'])
    assert.deepEqual(state.mentionSelectableItems.value.map((item) => item.type), [
      'action', 'action', 'knowledge', 'mcp', 'skill',
      'agent', 'agent', 'agent', 'agent', 'agent',
      'session', 'session', 'session', 'session', 'session'
    ])
    for (const [group, type] of [['knowledgeBases', 'knowledge'], ['mcps', 'mcp'], ['skills', 'skill'], ['agents', 'agent'], ['sessions', 'session']]) {
      for (const [index, item] of state.mentionItems.value[group].entries()) {
        state.mentionSelectedIndex.value = state.mentionSelectableItems.value.indexOf(item)
        assert.equal(state.isItemSelected(type, index), true)
        assert.equal(state.isItemSelected(type, index + 1), false)
      }
    }

    state.mentionQuery.value = '7'
    state.updateMentionItems('7')
    assert.deepEqual(state.mentionItems.value.agents.map((item) => item.value), ['agent-7'])
    t.mock.timers.tick(250)
    await setImmediate()
    assert.equal(state.sessionSearchLoading.value, false)
    assert.deepEqual(state.mentionItems.value.sessions.map((item) => item.value), ['session-7'])
  } finally {
    app?.unmount()
    await server.close()
    if (previousDocument === undefined) delete globalThis.document
    else globalThis.document = previousDocument
  }
})
