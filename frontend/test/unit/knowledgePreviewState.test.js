import assert from 'node:assert/strict'
import test from 'node:test'
import { createRenderer, h, nextTick, reactive, ssrContextKey, toRaw } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

/** 装配真实检索面板，并控制 API 响应顺序。 */
async function createQueryPanel(t) {
  const requests = []
  const store = reactive({ knowledgeBase: { kb_id: 'kb-1' }, state: { searchLoading: false }, meta: {}, loadQueryParams() {} })
  globalThis.__queryPanelStore = store
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false }, appType: 'custom',
    plugins: [{
      name: 'query-panel-store', enforce: 'pre',
      resolveId(id) { if (id.endsWith('/modules/knowledge/model/knowledgeBase') || id.endsWith('/modules/knowledge/model/knowledgeBase.js')) return '\0query-panel-store' },
      load(id) { if (id === '\0query-panel-store') return 'export const useKnowledgeBaseStore = () => globalThis.__queryPanelStore' }
    }]
  })
  t.after(async () => { await server.close(); delete globalThis.__queryPanelStore })
  const { queryApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
  t.mock.method(queryApi, 'queryTest', () => new Promise((resolve) => requests.push(resolve)))
  t.mock.method(queryApi, 'getSampleQuestions', async () => ({ questions: [] }))
  const { default: component } = await server.ssrLoadModule('/src/modules/knowledge/ui/QuerySection.vue')
  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null
  })
  const app = renderer.createApp({ ...component, render: () => h('div') })
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount({})
  t.after(() => app.unmount())
  const panel = toRaw(app._instance.setupState)
  panel.queryText.value = '测试问题'
  return { panel, store, requests }
}

const chunk = { content: '原文命中', metadata: { file_id: 'file-1', chunk_id: 'chunk-g1' } }

test('新查询立即清空旧结果与原文选择，完成后只展示新结果', async (t) => {
  const { panel, requests } = await createQueryPanel(t)
  panel.queryResult.value = [chunk]
  panel.selectChunk(chunk)
  const pending = panel.onQuery()
  assert.equal(panel.queryResult.value, '')
  assert.equal(panel.selectedChunk.value, null)
  requests[0]([{ ...chunk, content: '新命中' }])
  await pending
  assert.equal(panel.queryResult.value[0].content, '新命中')
  assert.equal(panel.selectedChunk.value, null)
})

test('清空让待处理响应失效，不重新打开结果或原文', async (t) => {
  const { panel, requests, store } = await createQueryPanel(t)
  const pending = panel.onQuery()
  panel.clearQueryResult()
  requests[0]([chunk])
  await pending
  assert.equal(panel.queryResult.value, '')
  assert.equal(panel.selectedChunk.value, null)
  assert.equal(store.state.searchLoading, false)
})

test('切库与反序响应均不能覆盖当前查询', async (t) => {
  const { panel, requests, store } = await createQueryPanel(t)
  const first = panel.onQuery()
  store.knowledgeBase.kb_id = 'kb-2'
  await nextTick()
  const second = panel.onQuery()
  requests[1]([{ ...chunk, content: '当前库命中' }])
  await second
  requests[0]([chunk])
  await first
  assert.equal(panel.queryResult.value[0].content, '当前库命中')
  assert.equal(panel.selectedChunk.value, null)
  assert.equal(store.state.searchLoading, false)
})
