import assert from 'node:assert/strict'
import test from 'node:test'

import ScrollController from '../../src/utils/scrollController.js'

test('ScrollController 在用户向上滚动离开底部时停用自动滚动', () => {
  let scrollTop = 500
  const mockContainer = {
    scrollHeight: 1000,
    clientHeight: 400,
    get scrollTop() {
      return scrollTop
    },
    set scrollTop(val) {
      scrollTop = val
    },
    scrollTo({ top }) {
      scrollTop = top
    }
  }

  const controller = new ScrollController(() => mockContainer, {
    threshold: 80,
    scrollDelay: 20
  })

  // 初始在底部 (1000 - 520 - 400 = 80 <= threshold)
  scrollTop = 520
  assert.equal(controller.isAtBottom(), true)
  assert.equal(controller.shouldAutoScroll, true)

  // 用户向上滚动离开底部 (1000 - 200 - 400 = 400 > 80)
  scrollTop = 200
  controller.handleScroll()
  assert.equal(controller.isAtBottom(), false)
  assert.equal(controller.shouldAutoScroll, false)

  // 用户滚动回到底部附近 (1000 - 550 - 400 = 50 <= 80)
  scrollTop = 550
  controller.handleScroll()
  assert.equal(controller.isAtBottom(), true)
  assert.equal(controller.shouldAutoScroll, true)
})

test('ScrollController 在 shouldAutoScroll 为 false 时 scrollToBottom 不执行滚动', async () => {
  let scrollCalled = false
  let scrollTop = 100
  const mockContainer = {
    scrollHeight: 1000,
    clientHeight: 400,
    get scrollTop() {
      return scrollTop
    },
    set scrollTop(val) {
      scrollTop = val
    },
    scrollTo({ top }) {
      scrollCalled = true
      scrollTop = top
    }
  }

  const controller = new ScrollController(() => mockContainer, {
    threshold: 80,
    scrollDelay: 20,
    retryDelays: []
  })

  // 用户向上滚动，离开底部
  controller.handleScroll()
  assert.equal(controller.shouldAutoScroll, false)

  await controller.scrollToBottom(false)
  assert.equal(scrollCalled, false)
  assert.equal(scrollTop, 100)

  // 强制滚动仍然执行
  await controller.scrollToBottom(true)
  assert.equal(scrollCalled, true)
  assert.equal(scrollTop, 600)
})
