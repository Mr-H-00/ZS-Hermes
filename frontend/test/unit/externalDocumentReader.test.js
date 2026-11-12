import assert from 'node:assert/strict'
import { once } from 'node:events'
import { createServer } from 'node:http'
import { setImmediate } from 'node:timers/promises'
import test, { before, after } from 'node:test'
import { createRenderer, nextTick, ssrContextKey } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

let server, component, queryApi
const previousStorage = globalThis.localStorage
before(async () => {
  globalThis.localStorage = { getItem: () => 'fixture-token', setItem() {}, removeItem() {} }
  server = await createTestServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ queryApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js'))
  ;({ default: component } = await server.ssrLoadModule('/src/modules/knowledge/ui/ExternalDocumentReader.vue'))
})
after(async () => {
  await server?.close()
  if (previousStorage === undefined) delete globalThis.localStorage
  else globalThis.localStorage = previousStorage
})
const renderer = createRenderer({
  createElement: () => ({ children: [] }), createText: () => ({}), createComment: () => ({}),
  insert: (child, parent) => parent.children.push(child), remove() {}, setText() {}, setElementText() {},
  parentNode: () => null, nextSibling: () => null, patchProp() {}
})

/** 挂载真实分页逻辑，并为每例恢复 API 与组件生命周期。 */
async function mountReader(open) {
  const original = queryApi.openDocument
  queryApi.openDocument = open
  let reader
  const app = renderer.createApp({ setup() {
    reader = component.setup({ kbId: 'fixture', fileId: 'document', startLine: 1001 }, { expose() {} })
    return () => null
  } })
  app.provide(ssrContextKey, {})
  app.mount({ children: [] })
  await setImmediate()
  await nextTick()
  return { reader, close() { app.unmount(); queryApi.openDocument = original } }
}

test('上一页首探针落在超长行时仍读取紧邻当前页的完整连续行', async () => {
  const lines = Array.from({ length: 1200 }, (_, index) => index === 899 ? 'x'.repeat(40000) : `line-${index + 1}`)
  const calls = []
  const originalFetch = globalThis.fetch
  const originalOpen = queryApi.openDocument
  const http = createServer(async (request, response) => {
    let body = ''
    for await (const chunk of request) body += chunk
    const { offset, window_size: size } = JSON.parse(body)
    calls.push(offset)
    const selected = []
    let bytes = 0
    for (const line of lines.slice(offset, offset + size)) {
      bytes += line.length
      if (bytes > 32000) {
        if (!selected.length) {
          response.writeHead(413, { 'Content-Type': 'application/json' })
          response.end(JSON.stringify({ detail: { code: 'document_line_too_large', message: '文档单行超过读取窗口字节上限' } }))
          return
        }
        break
      }
      selected.push(`${offset + selected.length + 1}\t${line}`)
    }
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ offset, window_size: size, end_line: offset + selected.length, start_line: offset + 1, content: selected.join('\n') }))
  })
  http.listen(0, '127.0.0.1')
  await once(http, 'listening')
  const baseUrl = `http://127.0.0.1:${http.address().port}`
  globalThis.fetch = (url, options) => originalFetch(new URL(url, baseUrl), options)
  const { reader, close } = await mountReader(originalOpen)
  try {
    // 初始读取经真实 HTTP 完成后再触发上一页。
    while (reader.loading.value) await setImmediate()
    await reader.load(1000, 'previous')
    assert.equal(calls[1], 899)
    assert.equal(reader.error.value, '')
    assert.equal(reader.document.value.start_line, 901)
    assert.equal(reader.document.value.end_line, 1000)
    assert.deepEqual(reader.lines.value.map((line) => line.text), lines.slice(900, 1000))
  } finally {
    close()
    globalThis.fetch = originalFetch
    await new Promise((resolve) => http.close(resolve))
  }
})

for (const message of ['网络连接失败', '无权读取该知识库']) {
  test(`上一页探针保留${message}并停止探测`, async () => {
    const calls = []
    const { reader, close } = await mountReader(async (_kb, _file, offset, size) => {
      calls.push(offset)
      if (calls.length > 1) throw new Error(message)
      return { offset, window_size: size, start_line: 1001, end_line: 1200, content: '1001\tcurrent' }
    })
    try {
      const current = reader.document.value
      await reader.load(1000, 'previous')
      assert.deepEqual(calls, [1000, 899])
      assert.equal(reader.error.value, message)
      assert.equal(reader.document.value, current)
      assert.deepEqual(reader.pageOffsets.value, [])
      assert.deepEqual(reader.forwardOffsets.value, [])
    } finally { close() }
  })
}
