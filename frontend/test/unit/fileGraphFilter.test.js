import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test, { before, after } from 'node:test'
import { setTimeout as realSetTimeout, clearTimeout as realClearTimeout } from 'node:timers'
import { createRenderer, h, nextTick, reactive, ssrContextKey } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

const source = readFileSync(new URL('../../src/modules/knowledge/ui/KnowledgeGraphSection.vue', import.meta.url), 'utf8')
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
    name: 'graph-boundaries', enforce: 'pre',
    resolveId(id) { if (id === 'ant-design-vue') return '\0graph-ant' },
    load(id) {
      if (id === '\0graph-ant') return 'export const message = new Proxy({}, { get: (_, k) => globalThis.__graphTest.message[k] }); export const Modal = {}'
      if (id.endsWith('/src/modules/knowledge/model/knowledgeBase.js')) return 'export const useKnowledgeBaseStore = () => globalThis.__graphTest.store'
      if (id.endsWith('/src/modules/background-jobs/index.js')) return 'export const useBackgroundJobsStore = () => ({ createJobRegistration: () => () => {} })'
      if (id.endsWith('/src/modules/settings/index.js')) return 'export const useConfigStore = () => ({})'
      if (id.endsWith('/src/apis/graph_api.js')) return 'export const graphApi = { getSubgraph: (...args) => globalThis.__graphTest.getSubgraph(...args) }'
      if (id.endsWith('/src/apis/knowledge_api.js')) return 'export const graphBuildApi = new Proxy({}, { get: (_, k) => globalThis.__graphTest.graphBuildApi[k] }); export const documentApi = { getDocumentBasicInfo: async (_kb, id) => ({ file_id: id, filename: `${id}.txt` }) }'
      if (['GraphCanvas.vue', 'FileSearchModal.vue', 'GraphDetailPanel.vue', 'ResourceEmptyState.vue', 'ModelSelectorComponent.vue'].some((name) => id.endsWith('/' + name))) return '<script>export default {}</script>'
    }
  }] })
  component = (await server.ssrLoadModule('/src/modules/knowledge/ui/KnowledgeGraphSection.vue')).default
})
after(async () => { await server.close() })

/** 使用真实图谱状态和组件加载流程，替换网络边界。 */
function setupGraph(getSubgraph, options = {}) {
  const emitted = []
  const props = reactive({ active: false, readonly: false, fileId: null })
  const store = reactive({ kbId: 'kb', knowledgeBase: { kb_type: 'milvus' } })
  const originalSetTimeout = globalThis.setTimeout
  const originalClearTimeout = globalThis.clearTimeout
  if (options.setTimeout) globalThis.setTimeout = options.setTimeout
  if (options.clearTimeout) globalThis.clearTimeout = options.clearTimeout
  globalThis.__graphTest = { store, getSubgraph, graphBuildApi: { getFileStatus: async () => ({ pending_chunks: 1 }), ...options.graphBuildApi }, message: options.message || { error() {}, success() {} } }
  let state
  const app = renderer.createApp({ setup() { state = component.setup(props, { expose() {}, emit: (...args) => emitted.push(args) }); return () => h('div') } })
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount(node())
  return { state, props, store, emitted, unmount: () => { app.unmount(); globalThis.setTimeout = originalSetTimeout; globalThis.clearTimeout = originalClearTimeout; delete globalThis.__graphTest } }
}

const result = (name) => ({ success: true, data: { nodes: name ? [{ id: name }] : [], edges: [] } })

test('图谱 API 只在选择文件时发送 file_id', async () => {
  const requests = []
  const apiServer = await createTestServer({ server: { middlewareMode: true, hmr: false }, plugins: [{
    name: 'graph-api-boundary', enforce: 'pre',
    load(id) { if (id.endsWith('/src/apis/base.js')) return 'export const apiGet = async (url) => globalThis.__graphRequests.push(url)' }
  }] })
  globalThis.__graphRequests = requests
  try {
    const { graphApi } = await apiServer.ssrLoadModule('/src/apis/graph_api.js')
    await graphApi.getSubgraph({ kb_id: 'kb' })
    await graphApi.getSubgraph({ kb_id: 'kb', file_id: 'file & 1' })
  } finally { await apiServer.close(); delete globalThis.__graphRequests }
  assert.equal(new URL(requests[0], 'http://localhost').searchParams.has('file_id'), false)
  assert.equal(new URL(requests[1], 'http://localhost').searchParams.get('file_id'), 'file & 1')
})

test('搜索和刷新保留文件范围，清除后恢复全库', async () => {
  const requests = []
  const { state, props, emitted, unmount } = setupGraph(async (params) => { requests.push(params); return result('one') })
  state.selectFile({ file_id: 'file-1', filename: '报告.txt' })
  assert.deepEqual(emitted.pop(), ['file-change', 'file-1'])
  props.fileId = 'file-1'
  await nextTick()
  state.searchInput.value = '实体'
  await state.loadGraph()
  assert.equal(requests.at(-1).file_id, 'file-1')
  assert.equal(requests.at(-1).node_label, '实体')
  state.clearGraphSearch()
  await nextTick()
  assert.equal(requests.at(-1).file_id, 'file-1')
  assert.equal(requests.at(-1).node_label, '*')
  state.clearFileFilter()
  assert.deepEqual(emitted.pop(), ['file-change', null])
  props.fileId = null
  await nextTick()
  await state.loadGraph()
  assert.equal(requests.at(-1).file_id, null)
  unmount()
})

test('切换文件后旧请求不能覆盖新图谱，空结果按文件提示', async () => {
  const pending = []
  const { state, props, unmount } = setupGraph((params) => new Promise((resolve) => pending.push({ params, resolve })))
  props.fileId = 'old'
  await nextTick()
  const oldLoad = state.loadGraph()
  props.fileId = 'new'
  await nextTick()
  const newLoad = state.loadGraph()
  pending[1].resolve(result(null))
  await newLoad
  pending[0].resolve(result('stale'))
  await oldLoad
  assert.deepEqual(state.graph.graphData.nodes, [])
  assert.equal(state.showGraphDataEmpty.value, true)
  assert.equal(state.graphDataEmptyTitle.value, '该文件暂无知识图谱')
  unmount()
})

test('文件图谱查询失败显示错误，不伪装成空结果', async () => {
  const { state, props, unmount } = setupGraph(async () => ({ success: false }))
  props.fileId = 'broken'
  await nextTick()
  const previousError = console.error
  console.error = () => {}
  try {
    await state.loadGraph()
  } finally {
    console.error = previousError
  }
  assert.equal(state.graphError.value, true)
  assert.equal(state.showGraphDataEmpty.value, false)
  unmount()
})


test('只读用户可请求文件搜索与基本信息，后端拒绝仍向调用者报错', async () => {
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  const originalFetch = globalThis.fetch
  const calls = []
  try {
    const { configureApiAuth } = await server.ssrLoadModule('/src/apis/auth_context.js')
    configureApiAuth({ getToken: () => 'reader-token', isAdmin: () => false })
    const { documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
    globalThis.fetch = async (url, options) => {
      calls.push({ url, authorization: options.headers.Authorization })
      return new Response('{}', { status: url.includes('/denied/') ? 403 : 200, headers: { 'content-type': 'application/json' } })
    }
    await documentApi.searchDocuments('kb', { query: '报告' })
    await documentApi.getDocumentBasicInfo('kb', 'file')
    await assert.rejects(documentApi.getDocumentBasicInfo('denied', 'file'), /没有权限/)
    assert.equal(calls.length, 3)
    assert.match(calls[0].url, /documents\/search\?query=/)
    assert.match(calls[1].url, /documents\/file\/basic$/)
    assert.equal(calls[0].authorization, 'Bearer reader-token')
    await assert.rejects(documentApi.listDocuments('kb'), /需要管理员权限/)
  } finally {
    globalThis.fetch = originalFetch
    await server.close()
  }
})


test('单文件构建提交文件 ID，并在任务结束后重新读取当前文件', async () => {
  const requests = []
  const builds = []
  let status = { locked: true, pending_chunks: 0, build_job_status: 'running' }
  const { state, props, unmount } = setupGraph(
    async (params) => { requests.push(params); return result(null) },
    { graphBuildApi: {
      startIndex: async (...args) => { builds.push(args); return { job_id: 'job' } },
      getStatus: async () => status
    } }
  )
  props.fileId = 'file-1'
  props.active = true
  await nextTick()
  await state.startGraphBuild('file-1')
  assert.deepEqual(builds, [['kb', 'file-1']])
  assert.equal(state.isBuildActive.value, true)
  status = { locked: true, pending_chunks: 0, build_job_status: 'completed' }
  await state.loadGraphBuildStatus()
  await nextTick()
  assert.equal(requests.at(-1).file_id, 'file-1')
  unmount()
})

test('全库构建不传文件范围，文件入口按文件待构建数显示', async () => {
  const builds = []
  const { state, props, unmount } = setupGraph(async () => result(null), {
    graphBuildApi: {
      startIndex: async (...args) => { builds.push(args); return {} },
      getStatus: async () => ({ locked: true, pending_chunks: 0, build_job_status: 'completed' })
    }
  })
  props.fileId = 'file-1'
  await state.startGraphBuild()
  assert.deepEqual(builds, [['kb', null]])
  const fileAction = source.match(/v-if="fileId && isMilvus && !readonly && filePendingChunks > 0"[\s\S]*?构建此文件图谱/)[0]
  assert.doesNotMatch(fileAction, /hasPendingGraphChunks/)
  assert.match(fileAction, /startGraphBuild\(fileId\)/)
  unmount()
})

test('构建 API 仅在指定文件时携带 file_id', async () => {
  const server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  const originalFetch = globalThis.fetch
  const calls = []
  try {
    const { configureApiAuth } = await server.ssrLoadModule('/src/apis/auth_context.js')
    configureApiAuth({ getToken: () => 'token', isAdmin: () => true })
    const { graphBuildApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
    globalThis.fetch = async (_url, options) => {
      calls.push(JSON.parse(options.body))
      return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
    }
    await graphBuildApi.startIndex('kb', 'file-1')
    await graphBuildApi.startIndex('kb')
    assert.deepEqual(calls, [{ file_id: 'file-1' }, {}])
  } finally {
    globalThis.fetch = originalFetch
    await server.close()
  }
})


test('文件已有图谱仍可提交文件构建', async () => {
  const builds = []
  const { state, props, unmount } = setupGraph(async () => result('已有实体'), {
    graphBuildApi: {
      startIndex: async (...args) => { builds.push(args); return {} },
      getStatus: async () => ({ locked: true, pending_chunks: 0, build_job_status: 'completed' })
    }
  })
  props.fileId = 'file-1'
  await nextTick()
  await state.loadGraph()
  assert.equal(state.hasGraphNodes.value, true)
  assert.equal(state.showGraphDataEmpty.value, false)
  await state.startGraphBuild('file-1')
  assert.deepEqual(builds, [['kb', 'file-1']])
  const toolbar = source.slice(source.indexOf('<template #top>'), source.indexOf('</template>'))
  assert.match(toolbar, /v-if="fileId && isMilvus && !readonly && filePendingChunks > 0"/)
  assert.match(toolbar, /startGraphBuild\(fileId\)/)
  assert.doesNotMatch(toolbar, /hasGraphNodes|showGraphDataEmpty/)
  unmount()
})

test('文件待构建数区分已完成、待处理和查询错误，切换时不沿用旧结果', async () => {
  const requests = []
  const { state, props, unmount } = setupGraph(async () => result(null), {
    graphBuildApi: { getFileStatus: (_kb, id) => new Promise((resolve, reject) => requests.push({ id, resolve, reject })) }
  })
  props.fileId = 'old'
  await nextTick()
  const old = requests.at(-1)
  props.fileId = 'new'
  await nextTick()
  const current = requests.at(-1)
  current.resolve({ pending_chunks: 0 })
  await nextTick()
  old.resolve({ pending_chunks: 7 })
  await nextTick()
  assert.equal(state.filePendingChunks.value, 0)
  const retry = state.loadFileBuildStatus()
  requests.at(-1).resolve({ pending_chunks: 2 })
  await retry
  assert.equal(state.filePendingChunks.value, 2)
  const failed = state.loadFileBuildStatus()
  const previousError = console.error
  console.error = () => {}
  try {
    requests.at(-1).reject(new Error('offline'))
    await failed
  } finally {
    console.error = previousError
  }
  assert.equal(state.filePendingChunks.value, null)
  assert.equal(state.fileStatusError.value, true)
  unmount()
})

test('构建状态首次加载失败可重试，不误判为未配置', async () => {
  let failing = true
  const { state, props, unmount } = setupGraph(async () => result(null), {
    graphBuildApi: {
      getStatus: async () => {
        if (failing) throw new Error('offline')
        return { locked: true, pending_chunks: 1 }
      },
      getFileStatus: async () => ({ pending_chunks: 1 })
    }
  })
  props.fileId = 'file-1'
  await nextTick()
  const previousError = console.error
  console.error = () => {}
  try {
    await state.loadGraphBuildStatus()
  } finally {
    console.error = previousError
  }
  assert.equal(state.graphBuildStatusError.value, true)
  assert.equal(state.graphBuildStatus.value, null)
  assert.equal(state.showGraphConfigEmpty.value, false)
  assert.match(source, /v-if="isMilvus && !readonly && graphBuildStatusError"[\s\S]*?@click="loadGraphBuildStatus"/)
  assert.match(source, /:disabled="!graphBuildStatus \|\| graphBuildStatusError/)
  failing = false
  await state.loadGraphBuildStatus()
  assert.equal(state.graphBuildStatusError.value, false)
  assert.equal(state.graphBuildStatus.value.locked, true)
  assert.equal(state.filePendingChunks.value, 1)
  unmount()
})

test('只读用户按图谱结果显示空状态，不请求管理构建状态', async () => {
  let calls = 0
  const { state, props, unmount } = setupGraph(async () => result(null), {
    graphBuildApi: { getStatus: async () => { calls++; throw new Error('不应请求') } }
  })
  props.readonly = true
  await state.loadGraphBuildStatus()
  await state.loadGraph()
  assert.equal(calls, 0)
  assert.equal(state.graphBuildStatusError.value, false)
  assert.equal(state.showGraphDataEmpty.value, true)
  unmount()
})

test('提交后首次状态失败仍轮询，恢复终态时刷新当前文件，离开时停止', async () => {
  const requests = []
  let poll = null
  let status = { locked: true, build_job_status: 'completed' }
  const { state, props, unmount } = setupGraph(async (params) => {
    requests.push(params)
    return result('实体')
  }, {
    setTimeout: (fn, delay) => {
      if (delay !== 5000) return realSetTimeout(fn, delay)
      poll = fn
      return 1
    },
    clearTimeout: (timer) => {
      if (timer === 1) poll = null
      else realClearTimeout(timer)
    },
    graphBuildApi: {
      startIndex: async () => ({ job_id: 'job' }),
      getStatus: async () => {
        if (!status) throw new Error('暂时不可用')
        return status
      }
    }
  })
  props.fileId = 'file-1'
  props.active = true
  await nextTick()
  await state.loadGraphBuildStatus()
  status = null
  const previousError = console.error
  console.error = () => {}
  try {
    await state.startGraphBuild('file-1')
  } finally {
    console.error = previousError
  }
  assert.equal(state.awaitingBuildStatus.value, true)
  assert.equal(typeof poll, 'function')
  status = { locked: true, build_job_status: 'completed' }
  const runPoll = poll
  poll = null
  await runPoll()
  await nextTick()
  assert.equal(state.awaitingBuildStatus.value, false)
  assert.equal(poll, null)
  assert.equal(requests.at(-1).file_id, 'file-1')
  status = { locked: true, build_job_status: 'running' }
  await state.loadGraphBuildStatus()
  assert.equal(typeof poll, 'function')
  props.active = false
  await nextTick()
  assert.equal(poll, null)
  unmount()
})


test('慢状态请求完成后才安排下一次，终态刷新且切库丢弃旧响应', async () => {
  let poll = null
  let statusRequest = null
  let calls = 0
  const graphRequests = []
  const { state, props, store, unmount } = setupGraph(async (params) => {
    graphRequests.push(params)
    return result('结果')
  }, {
    setTimeout: (fn, delay) => {
      if (delay !== 5000) return realSetTimeout(fn, delay)
      poll = fn
      return 1
    },
    clearTimeout: (timer) => {
      if (timer === 1) poll = null
      else realClearTimeout(timer)
    },
    graphBuildApi: {
      getStatus: async () => {
        calls++
        if (!statusRequest) return { locked: true, build_job_status: 'running' }
        return statusRequest.promise
      }
    }
  })
  props.fileId = 'file-1'
  props.active = true
  await nextTick()
  await state.loadGraphBuildStatus()
  statusRequest = {}
  statusRequest.promise = new Promise((resolve) => { statusRequest.resolve = resolve })
  const beforePoll = calls
  const runPoll = poll
  poll = null
  const inFlight = runPoll()
  assert.equal(calls, beforePoll + 1)
  // 请求仍在途时，没有下一轮定时器；无论过去多少周期都不会覆盖该请求。
  assert.equal(poll, null)
  statusRequest.resolve({ locked: true, build_job_status: 'completed' })
  await inFlight
  assert.equal(poll, null)
  assert.equal(state.isBuildActive.value, false)
  assert.equal(graphRequests.at(-1).file_id, 'file-1')

  statusRequest = null
  await state.loadGraphBuildStatus()
  statusRequest = {}
  statusRequest.promise = new Promise((resolve) => { statusRequest.resolve = resolve })
  const nextPoll = poll
  poll = null
  const oldRequest = nextPoll()
  const old = statusRequest
  statusRequest = null
  store.kbId = 'new-kb'
  await nextTick()
  old.resolve({ locked: true, build_job_status: 'completed' })
  await oldRequest
  assert.equal(state.isBuildActive.value, true)
  assert.equal(typeof poll, 'function')
  props.active = false
  await nextTick()
  assert.equal(poll, null)
  unmount()
})


for (const outcome of ['success', 'failure']) {
  test(`卸载后迟到的状态请求 ${outcome} 不更新状态、不提示或恢复轮询`, async () => {
    let poll = null
    let pending
    const errors = []
    const { state, props, unmount } = setupGraph(async () => result(null), {
      setTimeout: (fn, delay) => {
        if (delay !== 5000) return realSetTimeout(fn, delay)
        poll = fn
        return 1
      },
      clearTimeout: (timer) => {
        if (timer === 1) poll = null
        else realClearTimeout(timer)
      },
      message: { error: (text) => errors.push(text), success() {} },
      graphBuildApi: {
        getStatus: () => pending || Promise.resolve({ locked: true, build_job_status: 'running' })
      }
    })
    props.active = true
    await nextTick()
    await state.loadGraphBuildStatus()
    assert.equal(typeof poll, 'function')
    let resolveRequest
    let rejectRequest
    pending = new Promise((resolve, reject) => {
      resolveRequest = resolve
      rejectRequest = reject
    })
    const runPoll = poll
    poll = null
    const inFlight = runPoll()
    unmount()
    if (outcome === 'success') resolveRequest({ locked: true, build_job_status: 'completed' })
    else rejectRequest(new Error('迟到的错误'))
    await inFlight
    assert.equal(state.graphBuildStatus.value.build_job_status, 'running')
    assert.deepEqual(errors, [])
    assert.equal(poll, null)
  })
}
