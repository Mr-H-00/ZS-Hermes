import assert from 'node:assert/strict'
import test, { after, before } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createRenderer, nextTick, reactive, ssrContextKey } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

let server, historyComponent, callComponent
const renderer = createRenderer({
  createElement: () => ({ children: [] }),
  createText: () => ({}),
  createComment: () => ({}),
  insert: (child, parent) => parent.children.push(child),
  remove() {},
  setText() {},
  setElementText() {},
  parentNode: () => null,
  nextSibling: () => null,
  patchProp() {}
})

before(async () => {
  server = await createTestServer({
    server: { middlewareMode: true },
    plugins: [
      {
        name: 'observability-network-and-theme',
        enforce: 'pre',
        load(id) {
          if (id.endsWith('/src/apis/base.js'))
            return `
        export const apiGet = (...args) => globalThis.__observabilityRequest(...args)
        export const apiPost = () => { throw new Error('只读测试不得写入') }
        export const apiDelete = apiPost
        export const apiRequest = apiPost
      `
          if (id.endsWith('/src/shared/model/theme.js'))
            return 'export const useThemeStore = () => ({ isDark: false })'
        }
      }
    ]
  })
  historyComponent = (
    await server.ssrLoadModule('/src/modules/agents/ui/ScheduledAgentHistory.vue')
  ).default
  callComponent = (await server.ssrLoadModule('/src/modules/dashboard/ui/CallStatsComponent.vue'))
    .default
})
after(async () => {
  await server?.close()
  delete globalThis.__observabilityRequest
})

/** 装配真实组件 setup 和 Vue 生命周期，仅替换网络与主题边界。 */
function mount(t, component, props = {}) {
  let view
  const app = renderer.createApp({
    setup() {
      view = component.setup(props, { expose() {}, emit() {} })
      return () => null
    }
  })
  app.provide(ssrContextKey, {})
  app.mount({ children: [] })
  t.after(() => app.unmount())
  return { view, stop: () => app.unmount() }
}

test('历史分页复用真实 API，失败重试保持目标页并拒绝迟到的其他任务响应', async (t) => {
  const requests = []
  let fail = false
  let hold = false
  let release
  globalThis.__observabilityRequest = async (path) => {
    requests.push(path)
    if (fail) throw new Error('历史暂不可用')
    const offset = Number(new URL(path, 'http://fixture.test').searchParams.get('offset'))
    const result = {
      items: [{ id: path.includes('task-b') ? 'task-b-record' : `record-${offset}` }],
      total: 25,
      offset
    }
    if (hold)
      return new Promise((resolve) => {
        release = () => resolve(result)
      })
    return result
  }
  const props = reactive({ jobId: 'task a', revision: 0 })
  const { view } = mount(t, historyComponent, props)
  await setImmediate()
  assert.equal(requests[0], '/api/scheduled-tasks/task%20a/runs?limit=10&offset=0')
  assert.equal(view.total.value, 25)
  fail = true
  await view.load(10)
  assert.equal(view.error.value, '历史暂不可用')
  assert.equal(view.offset.value, 0)
  assert.equal(view.requestedOffset.value, 10)
  fail = false
  await view.load(view.requestedOffset.value)
  assert.equal(view.items.value[0].id, 'record-10')
  assert.equal(view.offset.value, 10)
  hold = true
  const oldRequest = view.load(20)
  props.jobId = 'task-b'
  hold = false
  await nextTick()
  await setImmediate()
  release()
  await oldRequest
  assert.equal(view.items.value[0].id, 'task-b-record')
  assert.equal(view.offset.value, 0)
  assert.equal(view.canOpenSession({ thread_id: 'thread', session_available: false }), false)
  assert.equal(view.canOpenSession({ thread_id: 'thread', session_available: true }), true)
  assert.match(
    view.runRecordLabel({ status: 'failed', error_message: '失败原因' }),
    /失败.*没有可用对话/
  )
})

test('历史刷新回到第一页，卸载后的响应不能回写', async (t) => {
  let release
  globalThis.__observabilityRequest = async () => ({
    items: [{ id: 'first' }],
    total: 30,
    offset: 0
  })
  const props = reactive({ jobId: 'task-a', revision: 0 })
  const { view, stop } = mount(t, historyComponent, props)
  await setImmediate()
  view.offset.value = 20
  props.revision += 1
  await nextTick()
  await setImmediate()
  assert.equal(view.offset.value, 0)
  globalThis.__observabilityRequest = () =>
    new Promise((resolve) => {
      release = resolve
    })
  const pending = view.load(10)
  stop()
  release({ items: [{ id: 'late' }], total: 30, offset: 10 })
  await pending
  assert.equal(view.items.value[0].id, 'first')
})

test('Token 汇总保留未知、部分缺报和真实零，小于百万的实测用量保持可读', async (t) => {
  globalThis.__observabilityRequest = async () => ({ data: [], categories: [] })
  const { view } = mount(t, callComponent)
  await setImmediate()
  view.callDataType.value = 'tokens'
  view.callStatsData.value = {
    data: [
      { data: { input_tokens: null, output_tokens: null }, total: null },
      { data: { input_tokens: 0, output_tokens: 0 }, total: 0 }
    ],
    total_count: null,
    average_count: null,
    peak_count: null,
    token_usage_complete: false,
    run_count: 1,
    incomplete_run_count: 1
  }
  assert.equal(view.totalCount.value, null)
  assert.equal(view.averageCount.value, null)
  assert.equal(view.peakCount.value, null)
  assert.equal(view.formatValueForDisplay(view.totalCount.value), '未知')
  assert.match(view.usageStatus.value, /用量未上报/)
  view.callStatsData.value = {
    ...view.callStatsData.value,
    total_count: 200,
    average_count: 14.29,
    peak_count: 120,
    run_count: 3
  }
  assert.equal(view.totalCount.value, 200)
  assert.equal(view.formatValueForDisplay(view.totalCount.value), '200')
  assert.match(view.usageStatus.value, /部分缺报/)
  view.callStatsData.value = {
    ...view.callStatsData.value,
    total_count: 0,
    average_count: 0,
    peak_count: 0,
    token_usage_complete: true,
    incomplete_run_count: 0
  }
  assert.equal(view.totalCount.value, 0)
  assert.equal(view.formatValueForDisplay(view.totalCount.value), '0')
  assert.equal(view.usageStatus.value, '上报完整')
  assert.equal(view.usageComplete.value, true)
})
