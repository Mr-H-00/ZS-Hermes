import assert from 'node:assert/strict'
import test from 'node:test'
import { createSSRApp, h } from 'vue'
import { routerKey } from 'vue-router'
import { createTestServer } from '../helpers/createTestServer.js'
import { renderToString } from 'vue/server-renderer'
import { Clock, Hourglass, CircleHelp, Database } from '@lucide/vue'

test('根组件让未指定尺寸的状态、帮助和按钮图标随字号缩放，保留显式尺寸', async () => {
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false }, ssr: { noExternal: ['ant-design-vue'] }, plugins: [{
    name: 'icon-theme-boundary', enforce: 'pre',
    resolveId(id) { if (id === 'ant-design-vue/es/locale/zh_CN') return '\0icon-locale' },
    load(id) {
      if (id === '\0icon-locale') return 'export default {}'
      if (id.endsWith('/src/shared/ui/AntEmpty.js')) return 'export const renderEmpty = () => null'
      if (id.endsWith('/src/shared/model/theme.js')) return 'export const useThemeStore = () => ({ currentTheme: {} })'
    }
  }] })
  try {
  const { default: component } = await server.ssrLoadModule('/src/app/App.vue')
  const renderIcons = () => h('div', [
    h(Clock), h(Hourglass), h(CircleHelp), h(Database),
    h(Clock, { size: 16 }), h(CircleHelp, { size: 32 }),
    h(Database, { style: { width: '48px', height: '48px' } })
  ])
  const app = createSSRApp({ setup() { component.setup({}, { expose() {} }); return renderIcons } })
  app.provide(routerKey, { currentRoute: { value: { matched: [{}] } }, beforeEach: () => () => {}, onError: () => () => {}, afterEach: () => () => {} })
  const html = await renderToString(app)
  const icons = [...html.matchAll(/<svg\b([^>]*)>/g)].map((match) => match[1])
  assert.equal(icons.length, 7)
  for (const attrs of icons.slice(0, 4)) {
    assert.match(attrs, /width="1em"/)
    assert.match(attrs, /height="1em"/)
  }
  assert.match(icons[4], /width="16" height="16"/)
  assert.match(icons[5], /width="32" height="32"/)
  assert.match(icons[6], /style="width:48px;height:48px;"/)
  } finally {
    await server.close()
  }
})
