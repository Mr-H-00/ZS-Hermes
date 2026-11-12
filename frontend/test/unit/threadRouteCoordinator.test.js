import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { setImmediate } from 'node:timers'
import { createTestServer } from '../helpers/createTestServer.js'
let server, createCoordinator
before(async () => {
  server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  ;({ createThreadRouteCoordinator: createCoordinator } = await server.ssrLoadModule(
    '/src/pages/agent/threadRouteCoordinator.ts'
  ))
})
after(() => server.close())
function setup(overrides = {}) {
  const effects = []
  const coordinator = createCoordinator({
    initializeAgents: async () => effects.push('initialize'),
    selectAgent: async (id) => effects.push(`agent:${id}`),
    rejectThread: async () => effects.push('redirect'),
    consumeAgent: async () => effects.push('consume'),
    onError: (error) => effects.push(error.message),
    ...overrides
  })
  return { coordinator, effects }
}
const target = (threadId = '', agentId = '') => ({ threadId, agentId })
test('组件挂载前保留 agent query，准备后只清空一次并消费选择', async () => {
  const { coordinator, effects } = setup()
  await coordinator.sync(target('', 'chosen'), null)
  assert.deepEqual(effects, [])
  const selections = []
  await coordinator.sync(target('', 'chosen'), {
    selectThreadFromRoute: async (id) => {
      selections.push(id)
      return true
    }
  })
  assert.deepEqual(selections, [''])
  assert.deepEqual(effects, ['initialize', 'agent:chosen', 'consume'])
})
test('延迟失败的旧线程不能重定向新线程，选择严格串行且只处理最新待选路由', async () => {
  const { coordinator, effects } = setup()
  let finish
  const selections = []
  const selection = {
    selectThreadFromRoute: (id) => {
      selections.push(id)
      return id === 'old'
        ? new Promise((resolve) => {
            finish = resolve
          })
        : Promise.resolve(true)
    }
  }
  const pending = coordinator.sync(target('old'), selection)
  await Promise.resolve()
  coordinator.sync(target('middle'), selection)
  const latest = coordinator.sync(target('latest'), selection)
  assert.deepEqual(selections, ['old'])
  finish(false)
  await Promise.all([pending, latest])
  assert.deepEqual(selections, ['old', 'latest'])
  assert.deepEqual(effects, [])
  assert.equal(coordinator.isSyncing(), false)
})
test('当前不存在的线程重定向，创建阻塞不消费 agent，失败可重新选择', async () => {
  const { coordinator, effects } = setup()
  await coordinator.sync(target('missing'), { selectThreadFromRoute: async () => false })
  await coordinator.sync(target('', 'agent'), { selectThreadFromRoute: async () => null })
  assert.deepEqual(effects, ['redirect', 'initialize'])
  await coordinator.sync(target('retry'), {
    selectThreadFromRoute: async () => {
      throw new Error('load failed')
    }
  })
  await coordinator.sync(target('retry'), { selectThreadFromRoute: async () => true })
  assert.equal(effects.at(-1), 'load failed')
})
test('卸载后的操作不报告错误、不重定向、不消费 query', async () => {
  const { coordinator, effects } = setup()
  let finish
  const pending = coordinator.sync(target('old'), {
    selectThreadFromRoute: () =>
      new Promise((resolve) => {
        finish = resolve
      })
  })
  await Promise.resolve()
  coordinator.dispose()
  finish(false)
  await pending
  await coordinator.sync(target('new'), { selectThreadFromRoute: async () => false })
  assert.deepEqual(effects, [])
})

test('失活页面使在途选择失效，重新激活仍可协调本会话', async () => {
  const { coordinator, effects } = setup()
  let finish
  const pending = coordinator.sync(target('cached'), {
    selectThreadFromRoute: () => new Promise((resolve) => { finish = resolve })
  })
  await Promise.resolve()
  await coordinator.sync(target('cached'), null)
  finish(false)
  await pending
  assert.deepEqual(effects, [])
  const selections = []
  await coordinator.sync(target('cached'), {
    selectThreadFromRoute: async (id) => {
      selections.push(id)
      return true
    }
  })
  assert.deepEqual(selections, ['cached'])
  assert.equal(coordinator.isSyncing(), false)
})

test('旧选择完成同一微任务窗口加入的新路由仍然被执行', async () => {
  const { coordinator } = setup()
  const selections = []
  let finish
  const completed = new Promise((resolve) => { finish = resolve })
  const selection = { selectThreadFromRoute: (id) => {
    selections.push(id)
    return id === 'old' ? completed : Promise.resolve(true)
  } }
  const old = coordinator.sync(target('old'), selection)
  await Promise.resolve()
  let latest
  completed.then(() => { latest = coordinator.sync(target('latest'), selection) })
  finish(true)
  await old
  await latest
  assert.deepEqual(selections, ['old', 'latest'])
  assert.equal(coordinator.isSyncing(), false)
})

test('来源成员在根页面选择成功后打开，旧路由的迟到选择不能打开旧成员', async () => {
  const { coordinator, effects } = setup()
  let finish
  const selection = {
    selectThreadFromRoute: id => id === 'old-root' ? new Promise(resolve => { finish = resolve }) : Promise.resolve(true),
    openSessionFromRoute: async id => effects.push(`member:${id}`)
  }
  const old = coordinator.sync({ ...target('old-root'), memberSessionId: 'old-child' }, selection)
  await new Promise(resolve => setImmediate(resolve))
  const current = coordinator.sync({ ...target('new-root'), memberSessionId: 'new-child' }, selection)
  finish(true)
  await Promise.all([old, current])
  assert.deepEqual(effects, ['member:new-child'])
})

test('成员 query 移除时恢复根主会话，防止保活页面停留在最大化成员', async () => {
  const { coordinator, effects } = setup()
  const selection = { selectThreadFromRoute: async () => true, openSessionFromRoute: async id => effects.push(id) }
  await coordinator.sync({ ...target('root'), memberSessionId: 'child' }, selection)
  await coordinator.sync({ ...target('root'), memberSessionId: '' }, selection)
  assert.deepEqual(effects, ['child', ''])
})
