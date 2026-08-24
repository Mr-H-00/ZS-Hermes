import assert from 'node:assert/strict'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

test('调试模式读取受限 LocalStorage 时降级为关闭', async () => {
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: {
      getItem() {
        throw new DOMException('blocked', 'SecurityError')
      },
      setItem() {},
      removeItem() {}
    }
  })
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  setActivePinia(createPinia())
  try {
    const { useInfoStore } = await server.ssrLoadModule('/src/stores/info.js')
    const store = useInfoStore()

    assert.equal(store.debugMode, false)
  } finally {
    await server.close()
  }
})
