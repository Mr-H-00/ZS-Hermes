import assert from 'node:assert/strict'
import test from 'node:test'

import { useStreamSmoother } from '../../src/composables/useStreamSmoother.js'

test('流式微增量按帧平滑输出', async () => {
  const threadState = {
    onGoingConv: {
      msgChunks: {}
    }
  }

  const smoother = useStreamSmoother({
    getThreadState: () => threadState
  })

  smoother.pushChunk(
    {
      id: 'msg-1',
      type: 'ai',
      content: 'Hello'
    },
    'thread-1'
  )

  // 初始骨架 chunk 已进入 msgChunks
  assert.ok(threadState.onGoingConv.msgChunks['msg-1'])
  assert.strictEqual(threadState.onGoingConv.msgChunks['msg-1'][0].content, '')

  // 模拟等待 50ms（多个 animation frame）
  await new Promise((resolve) => setTimeout(resolve, 50))

  const totalContent = threadState.onGoingConv.msgChunks['msg-1']
    .map((c) => c.content || '')
    .join('')

  assert.ok(totalContent.length > 0)
  assert.ok('Hello'.startsWith(totalContent))
})

test('重新进入对话或历史补发大文本时 fast-forward 快速放行不重放打字', () => {
  const threadState = {
    onGoingConv: {
      msgChunks: {}
    }
  }

  const smoother = useStreamSmoother({
    getThreadState: () => threadState
  })

  const longHistory = 'A'.repeat(200)

  smoother.pushChunk(
    {
      id: 'msg-catchup',
      type: 'ai',
      content: longHistory
    },
    'thread-catchup'
  )

  // 应该直接触发 fast-forward，绝大多数内容立即进入 msgChunks
  const emittedContent = threadState.onGoingConv.msgChunks['msg-catchup']
    .map((c) => c.content || '')
    .join('')

  assert.ok(emittedContent.length >= 190, `预期立即放行历史绝大多数内容，实际长度: ${emittedContent.length}`)
})

test('flushThread 立即将所有缓冲区内容同步清空', () => {
  const threadState = {
    onGoingConv: {
      msgChunks: {}
    }
  }

  const smoother = useStreamSmoother({
    getThreadState: () => threadState
  })

  smoother.pushChunk(
    {
      id: 'msg-flush',
      type: 'ai',
      content: 'Thinking and answering smoothly'
    },
    'thread-flush'
  )

  smoother.flushThread('thread-flush')

  const emittedContent = threadState.onGoingConv.msgChunks['msg-flush']
    .map((c) => c.content || '')
    .join('')

  assert.strictEqual(emittedContent, 'Thinking and answering smoothly')
})

test('resetThread 清除待处理任务且不再发射延迟事件', async () => {
  const threadState = {
    onGoingConv: {
      msgChunks: {}
    }
  }

  const smoother = useStreamSmoother({
    getThreadState: () => threadState
  })

  smoother.pushChunk(
    {
      id: 'msg-reset',
      type: 'ai',
      content: 'This should be aborted'
    },
    'thread-reset'
  )

  smoother.resetThread('thread-reset')
  const countAtReset = (threadState.onGoingConv.msgChunks['msg-reset'] || []).length

  await new Promise((resolve) => setTimeout(resolve, 50))

  const countLater = (threadState.onGoingConv.msgChunks['msg-reset'] || []).length
  assert.strictEqual(countLater, countAtReset)
})
