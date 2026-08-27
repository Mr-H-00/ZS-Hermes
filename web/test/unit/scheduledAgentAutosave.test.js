import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canLeaveScheduledTab,
  createScheduledAgentAutosave
} from '../../src/components/scheduled-agents/scheduledAgentAutosave.js'

function deferred() {
  let resolve
  const promise = new Promise((complete) => {
    resolve = complete
  })
  return { promise, resolve }
}

test('首次创建在途的新输入继续保存且不会被旧回包收敛', async () => {
  const createResponse = deferred()
  const requests = []
  const persisted = []
  const autosave = createScheduledAgentAutosave({
    delay: 60_000,
    async persist(request) {
      requests.push(structuredClone(request))
      if (!request.jobId) return createResponse.promise
      return { id: request.jobId, ...request.payload }
    },
    onPersisted(job, context) {
      persisted.push({ job, context })
    },
    onState() {}
  })

  autosave.beginDraft()
  autosave.queue({ payload: { name: 'A', prompt: '旧内容' }, error: '' }, null)
  const flushing = autosave.flush()
  autosave.queue({ payload: { name: 'A', prompt: '最新内容' }, error: '' }, null)
  createResponse.resolve({ id: 'job-1', name: 'A', prompt: '旧内容' })

  assert.equal(await flushing, true)
  assert.deepEqual(
    requests.map(({ jobId, payload }) => ({ jobId, payload })),
    [
      { jobId: null, payload: { name: 'A', prompt: '旧内容' } },
      { jobId: 'job-1', payload: { name: 'A', prompt: '最新内容' } }
    ]
  )
  assert.equal(persisted[0].context.finalizeDraft, false)
  assert.equal(persisted[1].context.finalizeDraft, true)
  assert.equal(persisted[1].job.prompt, '最新内容')
})

test('保存失败保留同一 payload 并阻止离开，重试成功后才放行', async () => {
  const requests = []
  const states = []
  let attempts = 0
  const autosave = createScheduledAgentAutosave({
    delay: 60_000,
    async persist(request) {
      requests.push(structuredClone(request))
      attempts += 1
      if (attempts === 1) throw new Error('网络中断')
      return { id: request.jobId, ...request.payload }
    },
    onPersisted() {},
    onState(next) {
      states.push(next)
    }
  })

  autosave.queue({ payload: { name: '任务', prompt: '不可丢失' }, error: '' }, 'job-1')

  assert.equal(await autosave.flush(), false)
  assert.match(states.at(-1).error, /网络中断/)
  assert.equal(await autosave.flush(), true)
  assert.equal(requests.length, 2)
  assert.deepEqual(requests[0], requests[1])
})

test('保存请求在途时出现非法编辑，旧回包不能覆盖状态或允许离开', async () => {
  const response = deferred()
  const states = []
  const autosave = createScheduledAgentAutosave({
    delay: 60_000,
    persist: () => response.promise,
    onPersisted() {},
    onState(next) {
      states.push(next.state)
    }
  })

  autosave.queue({ payload: { name: '任务', prompt: '合法内容' }, error: '' }, 'job-1')
  const flushing = autosave.flush()
  autosave.queue({ payload: null, error: '任务指令不能为空' }, 'job-1')
  response.resolve({ id: 'job-1', name: '任务', prompt: '合法内容' })

  assert.equal(await flushing, false)
  assert.equal(states.at(-1), 'invalid')
  assert.equal(await autosave.flush(), false)
})

test('离开定时任务标签前等待保存，失败时不执行导航', async () => {
  let attempts = 0
  const flush = async () => {
    attempts += 1
    return false
  }

  assert.equal(await canLeaveScheduledTab('schedules', 'agents', flush), false)
  assert.equal(attempts, 1)
  assert.equal(await canLeaveScheduledTab('agents', 'providers', flush), true)
  assert.equal(attempts, 1)
})
