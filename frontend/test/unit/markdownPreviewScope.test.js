import assert from 'node:assert/strict'
import test from 'node:test'
import { createSSRApp, h } from 'vue'
import { createPinia } from 'pinia'
import { renderToString } from 'vue/server-renderer'
import { createTestServer } from '../helpers/createTestServer.js'

test('Markdown 根节点继承调用方 scoped 标记并保留 class 和 style', async () => {
  globalThis.localStorage = { getItem: () => null }
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  try {
    const { default: MarkdownPreview } = await server.ssrLoadModule('/src/modules/workspace/ui/MarkdownPreview.vue')
    globalThis.document = { documentElement: { classList: { add() {}, remove() {} } } }
    globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' })
    const Parent = {
      __scopeId: 'data-v-caller',
      render: () => h('section', [h(MarkdownPreview, { class: 'caller-markdown', style: { color: 'red' } })])
    }
    const app = createSSRApp(Parent)
    app.use(createPinia())
    const html = await renderToString(app)
    assert.match(html, /<div(?=[^>]*data-v-caller)(?=[^>]*class="[^"]*caller-markdown)(?=[^>]*style="color:red;")[^>]*>/)
  } finally {
    await server.close()
    delete globalThis.localStorage
    delete globalThis.document
    delete globalThis.getComputedStyle
  }
})
