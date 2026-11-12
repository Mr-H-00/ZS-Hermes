import assert from 'node:assert/strict'
import test from 'node:test'
import { createRenderer, h, reactive, ssrContextKey, toRaw } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

/** 加载真实组件，在 API 边界控制搜索响应。 */
async function setupSearch(t, searchDocuments) {
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  t.after(() => server.close())
  const { documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
  t.mock.method(documentApi, 'searchDocuments', searchDocuments)
  const { default: component } = await server.ssrLoadModule('/src/modules/knowledge/ui/FileSearchModal.vue')
  const props = reactive({ open: false, kbId: 'kb' })
  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null
  })
  const app = renderer.createApp({ ...component, render: () => h('div') }, props)
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount({})
  t.after(() => app.unmount())
  return { props, state: toRaw(app._instance.setupState) }
}

test('搜索失败显示错误，重试成功后展示结果，空结果保持独立', async (t) => {
  let attempt = 0
  const { state } = await setupSearch(t, async () => {
    attempt++
    if (attempt === 1) throw new Error('offline')
    return { files: attempt === 2 ? [{ file_id: 'file', filename: '报告.txt' }] : [] }
  })
  state.keyword.value = '报告'
  const warn = console.warn
  console.warn = () => {}
  try {
    await state.handleSearch()
  } finally {
    console.warn = warn
  }
  assert.equal(state.searchError.value, true)
  assert.equal(state.hasSearched.value, true)
  assert.equal(state.loading.value, false)
  assert.deepEqual(state.results.value, [])
  await state.handleSearch()
  assert.equal(state.searchError.value, false)
  assert.equal(state.results.value[0].filename, '报告.txt')
  await state.handleSearch()
  assert.equal(state.searchError.value, false)
  assert.deepEqual(state.results.value, [])
})

test('过期失败不能覆盖后一次搜索结果', async (t) => {
  const pending = []
  const { state } = await setupSearch(t, () => new Promise((resolve, reject) => pending.push({ resolve, reject })))
  state.keyword.value = '旧'
  const oldRequest = state.handleSearch()
  state.keyword.value = '新'
  const newRequest = state.handleSearch()
  pending[1].resolve({ files: [{ file_id: 'new' }] })
  await newRequest
  pending[0].reject(new Error('offline'))
  await oldRequest
  assert.equal(state.searchError.value, false)
  assert.equal(state.results.value[0].file_id, 'new')
})
