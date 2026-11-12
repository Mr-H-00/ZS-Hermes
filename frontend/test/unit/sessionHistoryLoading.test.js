import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { nextTick } from 'vue'
import { mountSessionWorkspace } from '../helpers/sessionWorkspace.js'

/** 用真实页面状态与可观察消息位置验证历史追加。 */
async function setup(t, fetch) {
  let anchorPosition = 120
  const container = {
    addEventListener() {}, removeEventListener() {},
    scrollTop: 800,
    scrollHeight: 4000,
    getBoundingClientRect: () => ({ top: 0 }),
    querySelectorAll: () => [anchor]
  }
  const anchor = {
    isConnected: true,
    dataset: { historyKey: 'message-1' },
    getBoundingClientRect: () => ({
      top: anchorPosition - container.scrollTop,
      bottom: anchorPosition - container.scrollTop + 1000
    })
  }
  const { state } = await mountSessionWorkspace(t, fetch)
  state.currentThreadId.value = 'thread'
  state.chatMainRef.value = container
  state.historyPages.value.thread = { hasMore: true, loading: false }
  for (const method of ['disableAutoScroll', 'cancelPendingScrolls', 'handleScroll']) {
    t.mock.method(state.scrollController, method, () => {})
  }
  return {
    ...state,
    workspaceActive: state.workspaceActivated,
    currentChatId: state.currentThreadId,
    container,
    prepend(height) {
      anchorPosition += height
      container.scrollHeight += height
    }
  }
}

test('向上接近顶部自动加载，向下或远离顶部不加载', async (t) => {
  let reads = 0
  const h = await setup(t, async () => {
    reads++
  })
  h.handleWorkspaceScroll()
  h.container.scrollTop = 700
  h.handleWorkspaceScroll()
  h.container.scrollTop = 750
  h.handleWorkspaceScroll()
  assert.equal(reads, 0)
  h.container.scrollTop = 590
  h.handleWorkspaceScroll()
  await nextTick()
  assert.equal(reads, 1)
  assert.match(readFileSync(new URL('../../src/modules/session/ui/SessionWorkspace.vue', import.meta.url), 'utf8'), /@click="loadEarlierMessages"/)
})

test('追加保持可见消息位置，保留请求期间的用户滚动且锁定重复加载', async (t) => {
  let finish,
    reads = 0
  const h = await setup(t, () => {
    reads++
    return new Promise((resolve) => {
      finish = resolve
    })
  })
  const loading = h.loadEarlierMessages()
  assert.equal(h.earlierMessagesLabel.value, '正在加载更早消息…')
  await h.loadEarlierMessages()
  assert.equal(reads, 1)
  h.container.scrollTop = 650
  h.prepend(1600)
  finish()
  await loading
  assert.equal(h.container.scrollTop, 2250, '1600px 追加高度 + 请求期间 650px 阅读位置')
  assert.equal(h.earlierMessagesLoading.value, false)
})

for (const blocked of [
  'workspaceActive',
  'pageVisible',
  'isLoadingMessages',
  'hasMore',
  'loading'
]) {
  test(`${blocked} 不满足读取条件时不请求历史`, async (t) => {
    let reads = 0
    const h = await setup(t, async () => {
      reads++
    })
    if (blocked === 'hasMore') h.historyPages.value.thread.hasMore = false
    else if (blocked === 'loading') h.historyPages.value.thread.loading = true
    else h[blocked].value = blocked === 'isLoadingMessages'
    h.handleWorkspaceScroll()
    h.container.scrollTop = 0
    h.handleWorkspaceScroll()
    await h.loadEarlierMessages()
    assert.equal(reads, 0)
  })
}

test('失败停止自动重试，手动重试恢复且保持滚动位置', async (t) => {
  let reads = 0
  const h = await setup(t, async () => {
    if (++reads === 1) throw new Error('offline')
    h.prepend(500)
  })
  await h.loadEarlierMessages()
  assert.equal(h.earlierMessagesLabel.value, '加载失败，点击重试')
  assert.equal(h.container.scrollTop, 800)
  h.handleWorkspaceScroll()
  h.container.scrollTop = 300
  h.handleWorkspaceScroll()
  assert.equal(reads, 1)
  await h.loadEarlierMessages()
  assert.equal(h.earlierMessagesError.value, false)
  assert.equal(h.container.scrollTop, 800)
})

for (const changed of ['thread', 'container', 'visibility']) {
  test(`请求期间改变 ${changed} 不移动新视图`, async (t) => {
    let finish
    const h = await setup(t,
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    const loading = h.loadEarlierMessages()
    h.prepend(1600)
    if (changed === 'thread') h.currentChatId.value = 'other'
    if (changed === 'container') h.chatMainRef.value = { addEventListener() {}, removeEventListener() {} }
    if (changed === 'visibility') h.workspaceActive.value = false
    finish()
    await loading
    assert.equal(h.container.scrollTop, 800)
  })
}

test('同一 Run 内追加旧消息或 continuation 重挂载时，以同 key 的可见消息锚定', async (t) => {
  let finish
  const h = await setup(t, () => new Promise(resolve => { finish = resolve }))
  const loading = h.loadEarlierMessages()
  h.prepend(1000)
  const oldAnchor = h.container.querySelectorAll()[0]
  oldAnchor.isConnected = false
  const newAnchor = { ...oldAnchor, isConnected: true }
  h.container.querySelectorAll = selector => {
    assert.equal(selector, '.history-display-item', '消息组顶部不能表示组内阅读位置')
    return [newAnchor]
  }
  finish()
  await loading
  assert.equal(h.container.scrollTop, 1800)
})
