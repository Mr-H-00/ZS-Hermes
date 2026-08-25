import assert from 'node:assert/strict'
import test from 'node:test'

import {
  getDockedStatePanelMaxHeight,
  getFloatingStatePanelMaxHeight
} from '../../src/utils/statePanelLayout.js'

test('固定状态面板只限制容器内最大高度', () => {
  assert.equal(getDockedStatePanelMaxHeight({ height: 720 }), 702)
  assert.equal(getDockedStatePanelMaxHeight({ height: 720 }, 12, 2), 692)
  assert.equal(getDockedStatePanelMaxHeight({ height: 12 }), 0)
  assert.equal(getDockedStatePanelMaxHeight(null), null)
})

test('悬浮状态面板只限制最大高度并保留输入区间距', () => {
  const containerRect = { top: 32 }

  assert.equal(getFloatingStatePanelMaxHeight(containerRect, { top: 620 }), 572)
  assert.equal(getFloatingStatePanelMaxHeight(containerRect, { top: 540 }), 492)
})

test('缺少布局测量时不强制面板高度', () => {
  assert.equal(getFloatingStatePanelMaxHeight(null, null), null)
})

test('输入区占满容器时最大高度不会变成负数', () => {
  assert.equal(getFloatingStatePanelMaxHeight({ top: 32 }, { top: 40 }), 0)
})
