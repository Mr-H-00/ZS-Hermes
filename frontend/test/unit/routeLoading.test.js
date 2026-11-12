import assert from 'node:assert/strict'
import test, { before, after } from 'node:test'
import { createRenderer, h, ssrContextKey } from 'vue'
import { createMemoryHistory, createRouter, routerKey } from 'vue-router'
import { createTestServer } from '../helpers/createTestServer.js'

let server
let component
const node = () => ({ children: [], parent: null })
const renderer = createRenderer({
  createElement: node, createText: node, createComment: node,
  insert(child, parent) { child.parent = parent; parent.children.push(child) },
  remove(child) { const i = child.parent?.children.indexOf(child) ?? -1; if (i >= 0) child.parent.children.splice(i, 1) },
  setText() {}, setElementText() {}, parentNode: (n) => n.parent, nextSibling: () => null, patchProp() {}
})

before(async () => {
  server = await createTestServer({ server: { middlewareMode: true, hmr: false }, ssr: { noExternal: ['ant-design-vue'] }, plugins: [{
    name: 'route-loading-boundaries', enforce: 'pre',
    resolveId(id) { if (id === 'ant-design-vue/es/locale/zh_CN') return '\0route-locale' },
    load(id) {
      if (id === '\0route-locale') return 'export default {}'
      if (id.endsWith('/src/shared/model/theme.js')) return 'export const useThemeStore = () => ({})'
      if (id.endsWith('/src/shared/ui/AntEmpty.js')) return 'export const renderEmpty = () => null'
    }
  }] })
  component = (await server.ssrLoadModule('/src/app/App.vue')).default
})
after(async () => { await server.close() })

/** 通过真实 App 挂载及独立路由和浏览器边界观测恢复行为。 */
function setupApp(matched = [], providedRouter = null) {
  const callbacks = {}
  const requests = []
  const router = providedRouter || {
    currentRoute: { value: { matched } },
    resolve: (to) => ({ href: `/base${to.fullPath}` }),
    beforeEach: (callback) => { callbacks.before = callback; return () => { delete callbacks.before } },
    onError: (callback) => { callbacks.error = callback; return () => { delete callbacks.error } },
    afterEach: (callback) => { callbacks.after = callback; return () => { delete callbacks.after } }
  }
  globalThis.window = { location: { assign: (href) => requests.push(href), reload: () => requests.push('reload') } }
  let state
  const app = renderer.createApp({ setup() { state = component.setup({}, { expose() {} }); return () => h('div') } })
  app.provide(routerKey, router)
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount(node())
  callbacks.unmount = () => { app.unmount(); delete globalThis.window }
  return { state, callbacks, requests }
}

test('首屏长等待提供恢复入口，等待不伪装成失败，成功后停止提醒', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { state, callbacks, requests } = setupApp()
  assert.equal(state.loadingTooLong.value, false)
  t.mock.timers.tick(10000)
  assert.equal(state.loadingTooLong.value, true)
  assert.equal(state.failedRoute.value, null)
  assert.deepEqual(requests, [])
  state.reloadPage()
  assert.deepEqual(requests, ['reload'])
  callbacks.after({}, {}, undefined)
  assert.equal(state.loadingTooLong.value, false)
  callbacks.unmount()
})

test('路由失败后重新加载失败目标及 base 路径，成功导航清除错误', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { state, callbacks, requests } = setupApp([{}])
  callbacks.error(new Error('private module diagnostic'), { fullPath: '/extensions?tab=knowledge' })
  state.reloadPage()
  assert.deepEqual(requests, ['/base/extensions?tab=knowledge'])
  callbacks.after({}, {}, new Error('cancelled'))
  assert.equal(state.failedRoute.value, '/base/extensions?tab=knowledge')
  callbacks.after({}, {}, undefined)
  assert.equal(state.failedRoute.value, null)
  t.mock.timers.tick(10000)
  assert.equal(state.loadingTooLong.value, false)
  callbacks.unmount()
})

test('首屏快速完成和 App 卸载后都不会出现迟到的长等待提醒', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const ready = setupApp([{}])
  t.mock.timers.tick(10000)
  assert.equal(ready.state.loadingTooLong.value, false)
  ready.callbacks.unmount()
  for (const complete of ['after', 'unmount']) {
    const { state, callbacks } = setupApp()
    if (complete === 'after') callbacks.after({}, {}, undefined)
    else callbacks.unmount()
    t.mock.timers.tick(10000)
    assert.equal(state.loadingTooLong.value, false)
    if (complete === 'after') callbacks.unmount()
    assert.equal(callbacks.before, undefined)
    assert.equal(callbacks.error, undefined)
    assert.equal(callbacks.after, undefined)
  }
})

test('真实路由已完成新导航后，旧模块迟到失败不显示错误或覆盖重试目标', async () => {
  let rejectOldModule
  let markStarted
  const started = new Promise((resolve) => { markStarted = resolve })
  const router = createRouter({
    history: createMemoryHistory('/base/'),
    routes: [
      { path: '/login', component: { render: () => null } },
      { path: '/next', component: { render: () => null } },
      { path: '/slow', component: () => {
        markStarted()
        return new Promise((_resolve, reject) => { rejectOldModule = reject })
      } }
    ]
  })
  await router.push('/login')
  const { state, callbacks } = setupApp([], router)
  const pending = router.push('/slow').catch((error) => error)
  await started
  await router.push('/next')
  rejectOldModule(new Error('old module failed'))
  await pending
  assert.equal(router.currentRoute.value.fullPath, '/next')
  assert.equal(state.failedRoute.value, null, '旧导航错误不得出现在已成功的新页面上')
  callbacks.unmount()
})
