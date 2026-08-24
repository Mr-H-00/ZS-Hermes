import assert from 'node:assert/strict'
import test from 'node:test'

import { isLogContainerAtBottom } from '../../src/utils/logScroll.js'

test('日志容器离开底部后停止跟随', () => {
  assert.equal(
    isLogContainerAtBottom({ scrollHeight: 1000, clientHeight: 300, scrollTop: 500 }),
    false
  )
})

test('日志容器回到底部附近后恢复跟随', () => {
  assert.equal(
    isLogContainerAtBottom({ scrollHeight: 1000, clientHeight: 300, scrollTop: 697 }),
    true
  )
})
