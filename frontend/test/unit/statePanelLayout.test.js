import assert from 'node:assert/strict'
import test from 'node:test'
import { getStatePanelPlacement } from '../../src/modules/session/model/statePanelLayout.js'

const workspace = { top: 60, bottom: 900, left: 200, right: 1400 }
const trigger = { bottom: 100, right: 1392 }

test('面板贴右显示且不能覆盖底部输入区', () => {
  const { mode, style: placement } = getStatePanelPlacement(workspace, trigger, { top: 700 })
  assert.equal(mode, 'floating')
  assert.deepEqual(placement, { top: '56px', right: '8px', width: '340px', maxHeight: '576px' })
  assert.equal(workspace.top + 56, trigger.bottom + 16)
  assert.equal(workspace.top + 56 + 576, 700 - 8)
})

test('分栏时面板贴主对话右侧，宽度受主对话区域限制', () => {
  const mainArea = { left: 200, right: 800 }
  const { style: placement } = getStatePanelPlacement(workspace, trigger, null, null, mainArea)
  assert.equal(placement.right, '608px')
  assert.equal(placement.width, '340px')
  const narrow = getStatePanelPlacement(workspace, trigger, null, null, { left: 200, right: 500 })
  assert.equal(narrow.style.right, '908px')
  assert.equal(narrow.style.width, '284px')
})

test('文件或子标签下主输入区不可见时，面板仍能使用完整工作区高度', () => {
  assert.equal(getStatePanelPlacement(workspace, trigger).style.maxHeight, '776px')
})

test('输入区域超出工作区或占满工作区，面板不能溢出或产生负高度', () => {
  assert.equal(getStatePanelPlacement(workspace, trigger, { top: 1000 }).style.maxHeight, '776px')
  assert.equal(getStatePanelPlacement(workspace, trigger, { top: 80 }).style.maxHeight, '0px')
})

test('消息右侧有足够留白时嵌入，保持消息和输入框的位置', () => {
  const placement = getStatePanelPlacement(
    { ...workspace, right: 1800 },
    { ...trigger, right: 1792 },
    { top: 700 },
    { left: 1416, right: 1792 }
  )
  assert.equal(placement.mode, 'embedded')
  assert.deepEqual(placement.style, { top: '56px', right: '44px', width: '340px', maxHeight: '576px' })
})

test('右侧不足一个完整面板时悬浮，临界宽度足够时嵌入', () => {
  assert.equal(getStatePanelPlacement(workspace, trigger, null, { left: 1053, right: 1392 }).mode, 'floating')
  assert.equal(getStatePanelPlacement(workspace, trigger, null, { left: 1052, right: 1392 }).mode, 'embedded')
})
