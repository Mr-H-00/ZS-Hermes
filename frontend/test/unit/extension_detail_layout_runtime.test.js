import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick, ref } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

function createHostNode(type) {
  return { type, props: {}, children: [], parent: null, text: '' }
}

const renderer = createRenderer({
  createElement: createHostNode,
  createText(text) {
    const node = createHostNode('text')
    node.text = text
    return node
  },
  createComment(text) {
    const node = createHostNode('comment')
    node.text = text
    return node
  },
  insert(child, parent, anchor = null) {
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index >= 0) parent.children.splice(index, 0, child)
    else parent.children.push(child)
  },
  remove(child) {
    const index = child.parent?.children.indexOf(child) ?? -1
    if (index >= 0) child.parent.children.splice(index, 1)
  },
  setText(node, text) {
    node.text = text
  },
  setElementText(node, text) {
    node.text = text
    node.children = []
  },
  parentNode(node) {
    return node.parent
  },
  nextSibling(node) {
    const siblings = node.parent?.children || []
    return siblings[siblings.indexOf(node) + 1] || null
  },
  patchProp(node, key, _previous, value) {
    node.props[key] = value
  }
})

function findNodes(node, predicate, result = []) {
  if (predicate(node)) result.push(node)
  for (const child of node.children || []) findNodes(child, predicate, result)
  return result
}

const TabsStub = {
  props: { activeKey: String },
  emits: ['change'],
  setup(_props, { emit, slots, attrs }) {
    return () =>
      h('tabs-shell', { class: attrs.class }, [
        slots.leftExtra?.(),
        h('button', { id: 'switch-tab', onClick: () => emit('change', 'tools') }),
        slots.default?.(),
        slots.rightExtra?.()
      ])
  }
}

const TabPaneStub = {
  props: { forceRender: Boolean },
  setup(props, { slots }) {
    return () =>
      h('tab-pane', { forceRender: props.forceRender }, [slots.tab?.(), slots.default?.()])
  }
}

const EmptyStub = {
  setup() {
    return () => h('empty-state')
  }
}

test('共享详情框架转发 activeKey、响应动态 Tab 并保留 overlays 插槽', async () => {
  const server = await createTestServer({
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    plugins: [{
      name: 'extension-detail-layout-test',
      resolveId(id) {
        if (id === 'virtual:extension-detail-layout-test') return `\0${id}`
      },
      load(id) {
        if (id !== '\0virtual:extension-detail-layout-test') return
        const source = readFileSync(new URL('../../src/shared/ui/ExtensionDetailLayout.vue', import.meta.url), 'utf8')
        return compileScript(parse(source).descriptor, { id: 'extension-detail-layout-test', inlineTemplate: true }).content
      }
    }]
  })

  try {
    const { default: ExtensionDetailLayout } = await server.ssrLoadModule('virtual:extension-detail-layout-test')
    const activeKey = ref('general')
    const tabs = ref([
      { key: 'general', label: '信息' },
      { key: 'tools', label: '工具', forceRender: true }
    ])

    const Root = {
      setup() {
        return () =>
          h(
            ExtensionDetailLayout,
            {
              activeKey: activeKey.value,
              tabs: tabs.value,
              ready: true,
              'onUpdate:activeKey': (value) => {
                activeKey.value = value
              }
            },
            {
              breadcrumb: () => h('nav', { id: 'breadcrumb' }),
              actions: () => h('div', { id: 'actions' }),
              'panel-general': () => h('main', { id: 'general-panel' }),
              'panel-tools': () => h('main', { id: 'tools-panel' }),
              overlays: () => h('div', { id: 'overlay' })
            }
          )
      }
    }

    const container = createHostNode('root')
    const app = renderer.createApp(Root)
    app.component('a-tabs', TabsStub)
    app.component('a-tab-pane', TabPaneStub)
    app.component('a-empty', EmptyStub)
    app.mount(container)

    const tabPanes = findNodes(container, (node) => node.type === 'tab-pane')
    assert.equal(tabPanes.length, 2)
    assert.equal(Boolean(tabPanes[0].props.forceRender), false)
    assert.equal(tabPanes[1].props.forceRender, true)
    assert.equal(findNodes(container, (node) => node.props.id === 'general-panel').length, 1)
    assert.equal(findNodes(container, (node) => node.props.id === 'tools-panel').length, 1)
    assert.equal(findNodes(container, (node) => node.props.id === 'overlay').length, 1)

    const switchButton = findNodes(container, (node) => node.props.id === 'switch-tab')[0]
    switchButton.props.onClick()
    await nextTick()
    assert.equal(activeKey.value, 'tools')

    tabs.value = tabs.value.slice(0, 1)
    await nextTick()
    assert.equal(findNodes(container, (node) => node.type === 'tab-pane').length, 1)
    assert.equal(findNodes(container, (node) => node.props.id === 'general-panel').length, 1)
    assert.equal(findNodes(container, (node) => node.props.id === 'tools-panel').length, 0)
    assert.equal(findNodes(container, (node) => node.props.id === 'overlay').length, 1)

    app.unmount()

    const EmptyActionsRoot = {
      setup() {
        return () =>
          h(
            ExtensionDetailLayout,
            {
              activeKey: 'general',
              tabs: [{ key: 'general', label: '信息' }],
              ready: true
            },
            { breadcrumb: () => h('nav', { id: 'empty-actions-breadcrumb' }) }
          )
      }
    }
    const emptyActionsContainer = createHostNode('root')
    const emptyActionsApp = renderer.createApp(EmptyActionsRoot)
    emptyActionsApp.component('a-tabs', TabsStub)
    emptyActionsApp.component('a-tab-pane', TabPaneStub)
    emptyActionsApp.component('a-empty', EmptyStub)
    emptyActionsApp.mount(emptyActionsContainer)
    assert.equal(findNodes(emptyActionsContainer, (node) => node.type === 'tabs-shell').length, 1)
    assert.equal(findNodes(emptyActionsContainer, (node) => node.props.id === 'actions').length, 0)
    emptyActionsApp.unmount()
  } finally {
    await server.close()
  }
})
