import assert from 'node:assert/strict'
import { readFileSync, rmSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'

import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick, ref } from 'vue'

const componentSourcePath = fileURLToPath(
  new URL('../../src/components/knowledge/DatabaseCreateFlowModal.vue', import.meta.url)
)
const compiledComponentPath = fileURLToPath(
  new URL(`../../.database-create-flow-test-${pid}.mjs`, import.meta.url)
)
const stubModulePath = fileURLToPath(
  new URL(`../../.database-create-flow-stubs-${pid}.mjs`, import.meta.url)
)
const stubModuleName = `./.database-create-flow-stubs-${pid}.mjs`

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

function getText(node) {
  return [node.text, ...(node.children || []).map(getText)].filter(Boolean).join(' ')
}

function findControl(container, type, label) {
  return findNodes(
    container,
    (node) => node.type === type && (!label || node.props['aria-label'] === label)
  )[0]
}

function compileComponent() {
  const { descriptor } = parse(readFileSync(componentSourcePath, 'utf8'))
  let content = compileScript(descriptor, {
    id: 'database-create-flow-runtime-test',
    inlineTemplate: true
  }).content

  const replacements = [
    [
      "import AiTextarea from '@/components/AiTextarea.vue'",
      `import { AiTextarea } from '${stubModuleName}'`
    ],
    [
      "import EmbeddingModelSelector from '@/components/EmbeddingModelSelector.vue'",
      `import { EmbeddingModelSelector } from '${stubModuleName}'`
    ],
    [
      "import ShareConfigForm from '@/components/ShareConfigForm.vue'",
      `import { ShareConfigForm } from '${stubModuleName}'`
    ],
    [
      "import { useChunkPresetOptions } from '@/composables/useChunkPresetOptions'",
      `import { useChunkPresetOptions } from '${stubModuleName}'`
    ],
    [
      "import { useConfigStore } from '@/stores/config'",
      `import { useConfigStore } from '${stubModuleName}'`
    ],
    [
      "import { useDatabaseStore } from '@/stores/database'",
      `import { useDatabaseStore } from '${stubModuleName}'`
    ],
    ["from '@/utils/kb_utils'", "from './src/utils/kb_utils.js'"],
    ["from '@/utils/databaseCreateForm'", "from './src/utils/databaseCreateForm.js'"]
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
import { h, reactive, ref } from 'vue'

export const AiTextarea = { setup: () => () => h('ai-textarea') }
export const ShareConfigForm = { setup: () => () => h('share-config') }
export const EmbeddingModelSelector = {
  props: { value: String },
  emits: ['update:value', 'change'],
  setup(props, { emit }) {
    return () => h('button', {
      id: 'embedding-model-selector',
      'data-value': props.value,
      onClick: () => {
        const value = 'provider:text-embedding-3-small'
        emit('update:value', value)
        emit('change', value)
      }
    }, props.value)
  }
}

export const useChunkPresetOptions = () => ({
  chunkPresetSelectOptions: ref([{ value: 'general', label: '通用分块' }]),
  chunkPresetLoading: ref(false),
  loadChunkPresetOptions: async () => {},
  getChunkPresetDescription: () => '通用分块策略'
})

export const useConfigStore = () => ({
  config: { embed_model: 'provider:BAAI/bge-m3' }
})

export const useDatabaseStore = () => ({
  state: reactive({ creating: false }),
  createDatabase: async () => ({ kb_id: 'created-kb' })
})
`
  )
}

const ModalStub = {
  setup(_props, { slots }) {
    return () => h('modal-shell', slots.default?.())
  }
}

const InputStub = {
  inheritAttrs: false,
  props: { value: [String, Number] },
  emits: ['update:value'],
  setup(props, { attrs, emit }) {
    return () =>
      h('input-control', {
        ...attrs,
        value: props.value,
        onInput: (value) => emit('update:value', value)
      })
  }
}

const ButtonStub = {
  inheritAttrs: false,
  setup(_props, { attrs, slots }) {
    return () => h('button-control', attrs, slots.default?.())
  }
}

const SwitchStub = {
  inheritAttrs: false,
  props: { checked: Boolean, disabled: Boolean },
  emits: ['update:checked'],
  setup(props, { attrs, emit }) {
    return () =>
      h('switch-control', {
        ...attrs,
        disabled: props.disabled,
        'aria-checked': String(props.checked),
        onClick: () => {
          if (!props.disabled) emit('update:checked', !props.checked)
        }
      })
  }
}

const SelectStub = {
  inheritAttrs: false,
  props: { value: [String, Number, Boolean], options: Array },
  emits: ['update:value'],
  setup(props, { attrs, emit }) {
    return () =>
      h('select-control', {
        ...attrs,
        value: props.value,
        options: props.options,
        onChange: (value) => emit('update:value', value)
      })
  }
}

test('Parent-Child 关闭时原单层分块参数使用三列布局', () => {
  const source = readFileSync(componentSourcePath, 'utf8')

  assert.match(source, /class="form-grid chunk-parameter-grid legacy-chunk-parameter-grid"/)
  assert.match(
    source,
    /\.legacy-chunk-parameter-grid\s*\{\s*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/
  )
})

test('建库配置按 Parent-Child 状态切换参数并在离开 BGE-M3 后隐藏稀疏选项', async () => {
  writeStubs()
  compileComponent()

  try {
    const { default: DatabaseCreateFlowModal } = await import(
      `${pathToFileURL(compiledComponentPath).href}?test=${Date.now()}`
    )
    const open = ref(false)
    const Root = {
      setup() {
        return () =>
          h(DatabaseCreateFlowModal, {
            open: open.value,
            supportedKbTypes: {
              milvus: {
                name: 'Yuxi',
                requires_embedding_model: true,
                supports_documents: true,
                create_params: { options: [] }
              }
            },
            'onUpdate:open': (value) => {
              open.value = value
            }
          })
      }
    }

    const container = createHostNode('root')
    const app = renderer.createApp(Root)
    app.component('a-modal', ModalStub)
    app.component('a-input', InputStub)
    app.component('a-input-password', InputStub)
    app.component('a-input-number', InputStub)
    app.component('a-button', ButtonStub)
    app.component('a-switch', SwitchStub)
    app.component('a-select', SelectStub)
    app.mount(container)

    open.value = true
    await nextTick()

    findControl(container, 'input-control').props.onInput('模型切换回归库')
    await nextTick()
    findNodes(container, (node) => node.type === 'button-control' && getText(node) === '下一步')[0]
      .props.onClick()
    await nextTick()

    const configText = getText(container)
    assert.match(configText, /最大 Token 数/)
    assert.match(configText, /重叠比例/)
    assert.doesNotMatch(configText, /父块 Token 数/)
    assert.doesNotMatch(configText, /子块 Token 数/)

    const sparseOption = findControl(container, 'select-control', 'BGE-M3 稀疏向量')
    const parentChildOption = findControl(container, 'select-control', 'Parent-Child')
    assert.equal(sparseOption.props.value, false)
    assert.equal(parentChildOption.props.value, false)
    assert.deepEqual(parentChildOption.props.options, [
      { label: '启用', value: true },
      { label: '关闭', value: false }
    ])
    sparseOption.props.onChange(true)
    parentChildOption.props.onChange(true)
    await nextTick()
    assert.equal(
      findControl(container, 'select-control', 'BGE-M3 稀疏向量').props.value,
      true
    )
    const parentChildConfigText = getText(container)
    assert.doesNotMatch(parentChildConfigText, /最大 Token 数/)
    assert.match(parentChildConfigText, /父块 Token 数/)
    assert.match(parentChildConfigText, /子块 Token 数/)
    assert.match(parentChildConfigText, /子块重叠比例/)

    findNodes(container, (node) => node.props.id === 'embedding-model-selector')[0].props.onClick()
    await nextTick()

    const normalizedSparseOption = findControl(
      container,
      'select-control',
      'BGE-M3 稀疏向量'
    )
    assert.equal(normalizedSparseOption, undefined)

    findNodes(container, (node) => node.type === 'button-control' && getText(node) === '下一步')[0]
      .props.onClick()
    await nextTick()

    const summaryText = getText(container)
    assert.doesNotMatch(summaryText, /BGE-M3 稀疏向量/)
    assert.match(summaryText, /Parent-Child\s+已启用 · 父 1000 \/ 子 200 Token/)
    app.unmount()
  } finally {
    for (const path of [compiledComponentPath, stubModulePath]) {
      rmSync(path, { force: true })
    }
  }
})
