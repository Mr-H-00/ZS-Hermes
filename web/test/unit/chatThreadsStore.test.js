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
