import assert from 'node:assert/strict'
import test from 'node:test'
import { createSSRApp, h, nextTick, unref } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { mountWorkspaceView } from '../helpers/workspaceView.js'

/** 通过完整页面模板读取预览弹窗实际绑定。 */
async function isModalOpen(component, state) {
  const app = createSSRApp({ ...component, setup: () => state })
  app.config.warnHandler = () => {}
  app.component('a-modal', {
    props: ['open', 'wrapClassName'],
    setup: (props) => () => h('marker', { 'data-open': String(props.open), 'data-wrap': props.wrapClassName })
  })
  const html = await renderToString(app)
  const modal = html.match(/<marker[^>]*data-wrap="workspace-file-preview-modal"[^>]*>/)
  assert.ok(modal, '页面必须渲染文件预览弹窗')
  return modal[0].includes('data-open="true"')
}

async function setupWorkspace(t, width) {
  const { state, instance, component } = await mountWorkspaceView(t, { width })
  const hooks = {
    activated: async () => { for (const hook of instance.a || []) await hook() },
    deactivated: () => { for (const hook of instance.da || []) hook() }
  }
  assert.ok(instance.a?.length && instance.da?.length, '页面必须注册激活和停用生命周期')
  const view = new Proxy(state, { get: (target, key) => unref(target[key]) })
  return { state, hooks, view, isModalOpen: () => isModalOpen(component, state) }
}

test('侧栏预览离开缓存页面后不因零宽度变成弹窗，返回时恢复侧栏', async (t) => {
  const { state, hooks, view, isModalOpen } = await setupWorkspace(t, 1200)
  state.startPreviewRequest({ path: '/agents/MEMORY.md', name: 'MEMORY.md' })
  await nextTick()
  assert.equal(view.showInlinePreview, true)
  assert.equal(await isModalOpen(), false)

  hooks.deactivated?.()
  state.workspaceMainWidth.value = 0
  await nextTick()
  assert.equal(await isModalOpen(), false, '离开个人空间后不得出现预览弹窗')
  assert.equal(view.inlinePreviewVisible, true, '隐藏容器的零宽度不得改变侧栏预览模式')
  assert.equal(view.previewModalVisible, false)
  assert.equal(view.selectedPreviewPath, '/agents/MEMORY.md')

  await hooks.activated()
  await nextTick()
  assert.equal(view.showInlinePreview, true)
  assert.equal(await isModalOpen(), false)

  state.workspaceMainWidth.value = 700
  await nextTick()
  assert.equal(await isModalOpen(), true, '激活页面缩窄时仍可切换到弹窗')

  state.workspaceMainWidth.value = 1200
  await nextTick()
  state.activeSourceKey.value = 'knowledge-base:test-kb'
  hooks.deactivated?.()
  state.workspaceMainWidth.value = 0
  state.workspaceMainRef.value.clientWidth = 700
  await nextTick()
  await hooks.activated()
  await nextTick()
  assert.equal(await isModalOpen(), true, '后台缩窄后返回也必须恢复预览')
})

test('窄屏预览弹窗离开缓存页面后隐藏，返回仍保留选中文件', async (t) => {
  const { state, hooks, view, isModalOpen } = await setupWorkspace(t, 700)
  state.startPreviewRequest({ path: '/agents/MEMORY.md', name: 'MEMORY.md' })
  await nextTick()
  assert.equal(await isModalOpen(), true)
  hooks.deactivated?.()
  await nextTick()
  assert.equal(await isModalOpen(), false)
  await hooks.activated()
  await nextTick()
  assert.equal(await isModalOpen(), true)
  assert.equal(view.selectedPreviewPath, '/agents/MEMORY.md')
})
