import assert from 'node:assert/strict'
import test, { before, after } from 'node:test'
import { createSSRApp, h, reactive } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createTestServer } from '../helpers/createTestServer.js'

let server
let component
before(async () => {
  server = await createTestServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  ;({ default: component } = await server.ssrLoadModule('/src/shared/ui/WidgetNode.vue'))
})
after(async () => { await server?.close() })

/** 在真实组件的 Vue 生命周期上下文中读取节点投影。 */
async function setupStyle(node) {
  const props = reactive({ node })
  let state
  await renderToString(createSSRApp({ setup() {
    state = component.setup(props, { expose() {}, emit() {} })
    return () => h('div')
  } }))
  return { props, style: state.nodeStyle, isFormCard: state.isFormCard }
}

test('等分列保留容器的换行基准，不把 basis 覆盖成零', async () => {
  const { style } = await setupStyle({ type: 'Col', flex: 1 })
  assert.equal(style.value.flexGrow, 1)
  assert.equal(style.value.flexShrink, 1)
  assert.equal(style.value.flexBasis, undefined)
  assert.equal(style.value.flex, undefined)
})

test('非扩展列按内容占宽，auto 列允许扩展', async () => {
  const { props, style } = await setupStyle({ type: 'Col', flex: 0 })
  assert.equal(style.value.flexGrow, 0)
  assert.equal(style.value.flexBasis, 'auto')
  props.node.flex = 'auto'
  assert.equal(style.value.flexGrow, 1)
  assert.equal(style.value.flexBasis, 'auto')
  delete props.node.flex
  assert.deepEqual(style.value, {})
})

test('表单外观属于最近的卡片，布局容器透传归属', async () => {
  const { props, isFormCard } = await setupStyle({ type: 'Card', children: [] })
  const form = { type: 'Form', children: [] }
  assert.equal(isFormCard.value, false)
  props.node.children = [{ type: 'Row', children: [{ type: 'Col', children: [form] }] }]
  assert.equal(isFormCard.value, true)
  props.node.children = [{ type: 'Card', children: [form] }]
  assert.equal(isFormCard.value, false)
  props.node.children.push(form)
  assert.equal(isFormCard.value, true)
  props.node.type = 'Col'
  assert.equal(isFormCard.value, false)
})
