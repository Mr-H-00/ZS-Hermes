import assert from 'node:assert/strict'
import test from 'node:test'
import { createSSRApp, h } from 'vue'
import { routerKey } from 'vue-router'
import { renderToString } from 'vue/server-renderer'
import { createTestServer } from '../helpers/createTestServer.js'

test('关闭 Memory 时仍可查看文件，导航成功后关闭设置且不改写偏好', async () => {
  const events = []
  const updates = []
  globalThis.__memorySettingsTest = {
    router: { push: async (location) => events.push(location) },
    api: {
      get: async () => ({ enable_memory: false }),
      update: async (config) => { updates.push(config); return config }
    }
  }
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    plugins: [{
      name: 'memory-settings-boundaries',
      enforce: 'pre',
      load(id) {
        if (id.endsWith('/src/apis/user_config_api.js')) return 'export const userConfigApi = globalThis.__memorySettingsTest.api'
      }
    }]
  })
  try {
    const { default: component } = await server.ssrLoadModule('/src/modules/settings/ui/UserConfigSettingsCard.vue')
    let state
    const app = createSSRApp({ setup() {
      state = component.setup({}, { expose() {} })
      return () => h('div')
    } })
    app.provide(routerKey, globalThis.__memorySettingsTest.router)
    app.provide('settingsModal', { closeSettingsModal: () => events.push('closed') })
    await renderToString(app)
    await state.loadUserConfig()
    assert.equal(state.draftEnableMemory.value, false)
    await state.viewMemory()
    assert.deepEqual(events, [{ path: '/workspace', query: { open: '/agents/MEMORY.md' } }, 'closed'])
    assert.deepEqual(updates, [])
    assert.equal(state.draftEnableMemory.value, false)
  } finally {
    await server.close()
    delete globalThis.__memorySettingsTest
  }
})
