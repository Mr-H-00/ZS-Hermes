import assert from 'node:assert/strict'
import test from 'node:test'
import { createRenderer, h, nextTick, ssrContextKey } from 'vue'
import { routerKey, routeLocationKey } from 'vue-router'
import { createTestServer } from '../helpers/createTestServer.js'

const node = () => ({ children: [], parent: null })
const renderer = createRenderer({
  createElement: node, createText: node, createComment: node,
  insert(child, parent) { child.parent = parent; parent.children.push(child) },
  remove(child) { const i = child.parent?.children.indexOf(child) ?? -1; if (i >= 0) child.parent.children.splice(i, 1) },
  setText() {}, setElementText() {}, parentNode: (n) => n.parent, nextSibling: () => null, patchProp() {}
})

/** 驱动完整页面的挂载与卸载，控制兑换响应到达时序。 */
async function setup({ token = '', redirect = '/workspace' } = {}) {
  const navigations = []
  const applied = []
  let resolveExchange
  const pending = new Promise((resolve) => { resolveExchange = resolve })
  let calls = 0
  const user = { token, applySession: (response) => applied.push(response) }
  const storage = new Map([['oidc_redirect', redirect]])
  globalThis.sessionStorage = { getItem: (key) => storage.get(key), removeItem: (key) => storage.delete(key) }
  globalThis.__oidcTest = { user, exchange: () => { calls++; return pending } }
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false }, plugins: [{
    name: 'oidc-boundaries', enforce: 'pre',
    load(id) {
      if (id.endsWith('/src/modules/identity/model/user.js')) return 'export const useUserStore = () => globalThis.__oidcTest.user'
      if (id.endsWith('/src/apis/auth_api.js')) return 'export const authApi = { exchangeOIDCCode: (...args) => globalThis.__oidcTest.exchange(...args) }'
      if (id.endsWith('/src/modules/identity/model/oidcAutoStart.js')) return 'export const clearAutoStartAttempt = () => {}'
    }
  }] })
  const { default: component } = await server.ssrLoadModule('/src/pages/OIDCCallbackView.vue')
  const app = renderer.createApp({ setup() { component.setup({}, { expose() {} }); return () => h('div') } })
  app.provide(ssrContextKey, { modules: new Set() })
  app.provide(routerKey, { replace: async (target) => navigations.push(target) })
  app.provide(routeLocationKey, { path: '/auth/oidc/callback', query: { code: 'private-result' } })
  app.mount(node())
  const done = (async () => { await pending; await nextTick(); await nextTick() })()
  return { app, done, navigations, applied, user, resolveExchange, calls: () => calls,
    close: async () => { app.unmount(); await server.close(); delete globalThis.__oidcTest; delete globalThis.sessionStorage } }
}

test('回调先清 URL，兑换成功后只跳站内目标', async () => {
  for (const redirect of [
    '/workspace',
    '//attacker.example',
    '/\\attacker.example',
    '/\n/attacker.example'
  ]) {
    const fixture = await setup({ redirect })
    const done = fixture.done
    await Promise.resolve()
    assert.deepEqual(fixture.navigations, [{ path: '/auth/oidc/callback', query: {} }])
    assert.equal(fixture.calls(), 1)
    fixture.resolveExchange({ access_token: 'session' })
    await done
    assert.deepEqual(fixture.applied, [{ access_token: 'session' }])
    assert.equal(fixture.navigations.at(-1), redirect === '/workspace' ? '/workspace' : '/agent')
    await fixture.close()
  }
})

test('离页或切换账号后的迟到兑换响应不能覆盖当前会话', async () => {
  for (const change of ['unmount', 'switch']) {
    const fixture = await setup()
    const done = fixture.done
    await Promise.resolve()
    if (change === 'unmount') fixture.app.unmount()
    else fixture.user.token = 'newer-session'
    fixture.resolveExchange({ access_token: 'stale-session' })
    await done
    assert.deepEqual(fixture.applied, [])
    assert.equal(fixture.navigations.length, 1)
    await fixture.close()
  }
})

test('已经登录的浏览器清掉回调凭据且不重新兑换', async () => {
  const fixture = await setup({ token: 'existing' })
  fixture.resolveExchange(); await fixture.done
  assert.equal(fixture.calls(), 0)
  assert.deepEqual(fixture.applied, [])
  assert.equal(fixture.navigations.at(-1), '/agent')
  await fixture.close()
})
