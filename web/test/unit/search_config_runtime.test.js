import assert from 'node:assert/strict'
import { readFileSync, rmSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'

import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick } from 'vue'

const componentSourcePath = fileURLToPath(
  new URL('../../src/components/SearchConfigPanel.vue', import.meta.url)
)
const compiledComponentPath = fileURLToPath(
  new URL(`../../.search-config-test-${pid}.mjs`, import.meta.url)
)
const stubModulePath = fileURLToPath(
  new URL(`../../.search-config-stubs-${pid}.mjs`, import.meta.url)
)
const stubModuleName = `./.search-config-stubs-${pid}.mjs`

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

function findFormItem(container, label) {
  return findNodes(
    container,
    (node) => node.type === 'form-item' && node.props.label === label
  )[0]
}

function findSelect(formItem) {
  return findNodes(formItem, (node) => node.type === 'select-control')[0]
}

function compileComponent() {
  const { descriptor } = parse(readFileSync(componentSourcePath, 'utf8'))
  let content = compileScript(descriptor, {
    id: 'search-config-runtime-test',
    inlineTemplate: true
  }).content

  const replacements = [
    ["import { useDatabaseStore } from '@/stores/database'", `import { useDatabaseStore } from '${stubModuleName}'`],
    ["import { message } from 'ant-design-vue'", `import { message } from '${stubModuleName}'`],
    ["import { queryApi } from '@/apis/knowledge_api'", `import { queryApi } from '${stubModuleName}'`],
    ["from '@/utils/searchConfig'", "from './src/utils/searchConfig.js'"]
  ]

  for (const [source, replacement] of replacements) {
    assert.ok(content.includes(source), `编译结果缺少依赖: ${source}`)
    content = content.replace(source, replacement)
  }
  writeFileSync(compiledComponentPath, content)
}

function writeStubs() {
  writeFileSync(
    stubModulePath,
    `
export const message = { error() {}, success() {} }
export const useDatabaseStore = () => ({ meta: {} })
export const queryApi = {
  async getKnowledgeBaseQueryParams() {
    return {
      params: {
        options: [
          { key: 'search_mode', label: '检索模式', type: 'select', default: 'keyword', options: [] },
          { key: 'use_graph_retrieval', label: '图检索', type: 'boolean', default: false },
          { key: 'use_vector_score_fusion', label: '向量分数融合', type: 'boolean', default: false },
          {
            key: 'any_controlled',
            label: '满足任一条件',
            type: 'number',
            default: 1,
            visible_when: { any: [{ search_mode: 'vector' }, { use_graph_retrieval: true }] }
          },
          {
            key: 'all_controlled',
            label: '满足全部条件',
            type: 'number',
            default: 1,
            visible_when: { all: [{ search_mode: 'vector' }, { use_graph_retrieval: true }] }
          },
          {
            key: 'fusion_weight',
            label: '融合权重',
            type: 'number',
            default: 0.5,
            depend_on: ['use_vector_score_fusion', true]
          }
        ]
      }
    }
  },
  async updateKnowledgeBaseQueryParams() {
    return { message: 'success' }
  }
}
`
  )
}

const FormItemStub = {
  inheritAttrs: false,
  props: { label: String },
  setup(props, { slots }) {
    return () => h('form-item', { label: props.label }, slots.default?.())
  }
}

const SelectStub = {
  inheritAttrs: false,
  props: { value: [String, Number, Boolean] },
  emits: ['update:value'],
  setup(props, { emit, slots }) {
    return () =>
      h(
        'select-control',
        {
          value: props.value,
          onChange: (value) => emit('update:value', value)
        },
        slots.default?.()
      )
  }
}

const PassiveStub = {
  setup(_props, { slots }) {
    return () => h('passive-control', slots.default?.())
  }
}

async function flushLoad() {
  await Promise.resolve()
  await nextTick()
}

test('完整检索配置面板仅渲染满足 visible_when 和 depend_on 的字段', async () => {
  writeStubs()
  compileComponent()

  try {
    const { default: SearchConfigPanel } = await import(
      `${pathToFileURL(compiledComponentPath).href}?test=${Date.now()}`
    )
    const container = createHostNode('root')
    const app = renderer.createApp(SearchConfigPanel, { kbId: 'kb-visible' })
    app.component('a-form-item', FormItemStub)
    app.component('a-select', SelectStub)
    app.component('a-form', PassiveStub)
    app.component('a-row', PassiveStub)
    app.component('a-col', PassiveStub)
    app.component('a-select-option', PassiveStub)
    app.component('a-input-number', PassiveStub)
    app.component('a-input', PassiveStub)
    app.component('a-empty', PassiveStub)
    app.component('a-spin', PassiveStub)
    app.component('a-result', PassiveStub)
    app.component('a-button', PassiveStub)
    app.mount(container)
    await flushLoad()

    assert.equal(findFormItem(container, '满足任一条件'), undefined)
    assert.equal(findFormItem(container, '满足全部条件'), undefined)
    assert.equal(findFormItem(container, '融合权重'), undefined)

    findSelect(findFormItem(container, '检索模式')).props.onChange('vector')
    await nextTick()
    assert.ok(findFormItem(container, '满足任一条件'))
    assert.equal(findFormItem(container, '满足全部条件'), undefined)

    findSelect(findFormItem(container, '图检索')).props.onChange('true')
    await nextTick()
    assert.ok(findFormItem(container, '满足全部条件'))
    assert.equal(findFormItem(container, '融合权重'), undefined)

    findSelect(findFormItem(container, '向量分数融合')).props.onChange('true')
    await nextTick()
    assert.ok(findFormItem(container, '融合权重'))
    app.unmount()
  } finally {
    for (const path of [compiledComponentPath, stubModulePath]) {
      rmSync(path, { force: true })
    }
  }
})
