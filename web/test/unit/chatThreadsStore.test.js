import assert from 'node:assert/strict'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {}
}

test('线程创建期间共享 Store 拒绝外层切换，创建结果可显式提交', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  setActivePinia(createPinia())
  try {
    const { useChatThreadsStore } = await server.ssrLoadModule('/src/stores/chatThreads.js')
    const store = useChatThreadsStore()
    store.setCurrentThreadId('thread-before')
    store.setThreadCreationInFlight(true)

    assert.equal(store.setCurrentThreadId('thread-sidebar'), false)
    assert.equal(store.currentThreadId, 'thread-before')
    assert.equal(store.setCurrentThreadId('thread-created', { force: true }), true)
    assert.equal(store.currentThreadId, 'thread-created')

    store.setThreadCreationInFlight(false)
  } finally {
    await server.close()
  }
})

test('Project 删除后 Store 只移除对应线程并清空当前选择', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  setActivePinia(createPinia())
  try {
    const { useChatThreadsStore } = await server.ssrLoadModule('/src/stores/chatThreads.js')
    const store = useChatThreadsStore()
    store.threads = [
      { id: 'thread-a', project_id: 'project-a' },
      { id: 'thread-b', project_id: 'project-b' }
    ]
    store.setCurrentThreadId('thread-a')

    assert.deepEqual(store.removeThreadsByProject('project-a'), ['thread-a'])
    assert.deepEqual(store.threads, [{ id: 'thread-b', project_id: 'project-b' }])
    assert.equal(store.currentThreadId, null)
  } finally {
    await server.close()
  }
})

test('置顶线程不占用普通线程分页 offset 且不会提前结束加载', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  setActivePinia(createPinia())
  try {
    const { threadApi } = await server.ssrLoadModule('/src/apis/agent_api.js')
    const calls = []
    const pinned = { id: 'thread-pinned', is_pinned: true }
    threadApi.getThreads = async (_agentId, limit, offset) => {
      calls.push({ limit, offset })
      const count = offset < 200 ? 100 : 50
      return [
        pinned,
        ...Array.from({ length: count }, (_, index) => ({
          id: `thread-${offset + index}`,
          is_pinned: false
        }))
      ]
    }

    const { useChatThreadsStore } = await server.ssrLoadModule('/src/stores/chatThreads.js')
    const store = useChatThreadsStore()
    await store.loadThreads()
    await store.loadMoreThreads()
    await store.loadMoreThreads()

    assert.deepEqual(calls, [
      { limit: 100, offset: 0 },
      { limit: 100, offset: 100 },
      { limit: 100, offset: 200 }
    ])
    assert.equal(store.threads.length, 251)
    assert.equal(store.hasMoreThreads, false)
  } finally {
    await server.close()
  }
})
