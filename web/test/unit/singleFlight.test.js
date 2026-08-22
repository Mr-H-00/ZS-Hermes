import assert from 'node:assert/strict'
import test from 'node:test'

import { createSingleFlight } from '../../src/utils/singleFlight.js'

test('并发调用共享同一个进行中的线程创建 Promise', async () => {
  let calls = 0
  let resolveCreate
  const ensure = createSingleFlight(
    () =>
      new Promise((resolve) => {
        calls += 1
        resolveCreate = resolve
      })
  )

  const sending = ensure('发送')
  const attaching = ensure('附件')
  await Promise.resolve()
  assert.equal(calls, 1)

  resolveCreate('thread-1')
  assert.deepEqual(await Promise.all([sending, attaching]), ['thread-1', 'thread-1'])
  assert.equal(await ensure('后续'), 'thread-1')
  assert.equal(calls, 1)

  ensure.reset()
  const next = ensure('新草稿')
  await Promise.resolve()
  assert.equal(calls, 2)
  resolveCreate('thread-2')
  assert.equal(await next, 'thread-2')
})

test('线程创建失败后清除 in-flight 并允许保留选择后重试', async () => {
  let calls = 0
  const ensure = createSingleFlight(async () => {
    calls += 1
    if (calls === 1) throw new Error('create failed')
    return 'thread-2'
  })

  await assert.rejects(ensure(), /create failed/)
  assert.equal(await ensure(), 'thread-2')
  assert.equal(calls, 2)
})

test('reset 后旧请求完成不会缓存到新一代调用', async () => {
  const resolvers = []
  const ensure = createSingleFlight(
    () =>
      new Promise((resolve) => {
        resolvers.push(resolve)
      })
  )

  const oldRequest = ensure()
  await Promise.resolve()
  ensure.reset()
  const newRequest = ensure()
  await Promise.resolve()

  resolvers[0]('old-thread')
  assert.equal(await oldRequest, 'old-thread')
  resolvers[1]('new-thread')
  assert.equal(await newRequest, 'new-thread')
  assert.equal(await ensure(), 'new-thread')
})
