import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test, { before, after, afterEach } from 'node:test'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick, reactive, ref, ssrContextKey, watch } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

let server, UploadModal, FileTable, Selector, ClientUpload, ClientTable
const mounted = []
const node = (type) => ({ type, children: [], props: {}, style: {}, parent: null, text: '' })
const renderer = createRenderer({
  createElement: node,
  createText: (text) => ({ ...node('text'), text }),
  createComment: (text) => ({ ...node('comment'), text }),
  insert(child, parent, anchor) {
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(child)
    else parent.children.splice(index, 0, child)
  },
  remove(child) {
    const index = child.parent?.children.indexOf(child) ?? -1
    if (index >= 0) child.parent.children.splice(index, 1)
  },
  setText(item, text) { item.text = text },
  setElementText(item, text) { item.text = text; item.children = [] },
  parentNode: (item) => item.parent,
  nextSibling: (item) => item.parent?.children[item.parent.children.indexOf(item) + 1] || null,
  patchProp(item, key, _old, value) { item.props[key] = value }
})

before(async () => {
  server = await createTestServer({
    server: { middlewareMode: true },
    ssr: { noExternal: ['ant-design-vue'] },
    plugins: [{
      name: 'ocr-selection-boundaries', enforce: 'pre',
      resolveId(id, importer) {
        if (id === 'ant-design-vue') return '\0ocr-selection-ant'
        if (id.startsWith('virtual:ocr-client-')) {
          const name = id.slice('virtual:ocr-client-'.length)
          return fileURLToPath(new URL(`../../src/modules/knowledge/ui/${name}.vue.client.js`, import.meta.url)).replaceAll('\\', '/')
        }
        if (id.endsWith('/OCRSelector.vue') && importer?.endsWith('.vue.client.js')) return '\0ocr-selection-control'
      },
      load(id) {
        if (id.endsWith('.vue.client.js')) {
          const source = readFileSync(id.slice(0, -'.client.js'.length), 'utf8')
          return compileScript(parse(source).descriptor, { id, inlineTemplate: true }).content
        }
        if (id === '\0ocr-selection-control') return `
          import { h, onMounted } from 'vue'
          export default {
            props: ['modelValue'], emits: ['update:modelValue', 'change', 'options-loaded'],
            setup(props, { emit }) {
              const load = async () => emit('options-loaded', await globalThis.__ocrSelection.ocrApi.getOptions())
              onMounted(load)
              return () => h('div', { 'data-selector': true, 'data-selected': props.modelValue }, [
                h('button', { 'data-select-engine': true, onClick: () => {
                  emit('update:modelValue', 'rapid_ocr'); emit('change', 'rapid_ocr')
                } }, '选择 RapidOCR'),
                h('button', { 'data-refresh-options': true, onClick: load }, '刷新选项')
              ])
            }
          }`
        if (id === '\0ocr-selection-ant') return 'export const message = new Proxy({}, { get: (_, key) => (...args) => globalThis.__ocrSelection.message[key](...args) }); export const Modal = {}; export const Upload = { LIST_IGNORE: "ignore" }'
        if (id.endsWith('/src/modules/knowledge/model/knowledgeBase.js')) return 'export const useKnowledgeBaseStore = () => globalThis.__ocrSelection.store'
        if (id.endsWith('/src/modules/background-jobs/index.js')) return 'export const useBackgroundJobsStore = () => ({ createJobRegistration: () => () => {} })'
        if (id.endsWith('/src/modules/identity/index.js')) return 'export const useUserStore = () => ({ token: "fixture", isSuperAdmin: true, getAuthHeaders: () => ({}) })'
        if (id.endsWith('/src/modules/settings/index.js')) return 'export const useConfigStore = () => ({ config: { default_ocr_engine: "rapid_ocr" } })'
        if (id.endsWith('/src/apis/knowledge_api.js')) return 'export const fileApi = new Proxy({}, { get: (_, key) => (...args) => globalThis.__ocrSelection.fileApi[key](...args) }); export const documentApi = new Proxy({}, { get: (_, key) => (...args) => globalThis.__ocrSelection.documentApi[key](...args) }); export const graphBuildApi = {}'
        if (id.endsWith('/src/apis/system_api.js')) return 'export const ocrApi = new Proxy({}, { get: (_, key) => (...args) => globalThis.__ocrSelection.ocrApi[key](...args) })'
        if (id.endsWith('.vue') && !['/FileUploadModal.vue', '/FileTable.vue', '/OCRSelector.vue'].some((name) => id.endsWith(name))) return '<script>export default { render() { return null } }</script>'
      }
    }]
  })
  UploadModal = (await server.ssrLoadModule('/src/modules/knowledge/ui/FileUploadModal.vue')).default
  FileTable = (await server.ssrLoadModule('/src/modules/knowledge/ui/FileTable.vue')).default
  Selector = (await server.ssrLoadModule('/src/modules/settings/ui/OCRSelector.vue')).default
  ClientUpload = (await server.ssrLoadModule('virtual:ocr-client-FileUploadModal')).default
  ClientTable = (await server.ssrLoadModule('virtual:ocr-client-FileTable')).default
})

after(async () => { await server?.close() })
afterEach(() => {
  for (const app of mounted.splice(0)) app.unmount()
  delete globalThis.__ocrSelection
})

/** 提供协议结果和业务提交边界，组件逻辑及 Vue watch 保持真实。 */
function fixture() {
  const submissions = [], errors = []
  const store = reactive({
    kbId: 'kb-fixture', knowledgeBase: { kb_type: 'milvus' },
    state: { lock: false, chunkLoading: false }, fileBrowser: {},
    documentFiles: [], folderBreadcrumbs: [], selectedRowKeys: [],
    resetFileBrowser() {}, loadDocumentFiles: async () => {},
    parseFiles: async (ids, params) => { submissions.push({ ids, params }); return true },
    parsePendingFiles: async (params) => { submissions.push({ params }); return true },
    addFiles: async (payload) => { submissions.push(payload); return true }
  })
  const boundaries = {
    store,
    message: { error: (text) => errors.push(text), warning() {}, info() {}, success() {} },
    fileApi: { getSupportedFileTypes: async () => ({ file_types: ['.pdf'] }), getUploadUrl: () => '/fixture-upload' },
    documentApi: { getDocumentInfo: async () => ({ meta: { processing_params: {} } }) },
    ocrApi: { getOptions: async () => catalog('mineru_ocr'), getHealth: async () => ({ health: {} }) }
  }
  globalThis.__ocrSelection = boundaries
  return { ...boundaries, submissions, errors }
}

/** 装配完整 SFC 的 setup；不截取或改写生产代码。 */
function mount(component, props, emit = () => {}) {
  let state
  const app = renderer.createApp({
    setup() {
      state = component.setup(props, { expose() {}, emit })
      return () => h('div')
    }
  })
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount(node())
  mounted.push(app)
  return state
}

/** 服务端目录包含两个可选引擎，默认值不由测试中的 UI 常量推导。 */
function catalog(defaultEngine) {
  return {
    default_engine: defaultEngine,
    engines: ['mineru_ocr', 'rapid_ocr'].map((engine_id) => ({ engine_id, display_name: engine_id }))
  }
}

/** 模拟上传对话框可见性变化，触发真实 watch。 */
async function openUpload() {
  const props = reactive({ visible: false, folderTree: [], currentFolderId: null, isFolderMode: false, mode: 'file' })
  const state = mount(UploadModal, props)
  props.visible = true
  await nextTick()
  return { state, props }
}

/** 查找实际客户端模板渲染出的控件。 */
function find(root, predicate) {
  if (predicate(root)) return root
  return root.children.map((child) => find(child, predicate)).find(Boolean)
}

/** 保留 Modal 首次打开后的内容，验证由生产 v-if 自己负责选择器重挂载。 */
function mountClient(component, props) {
  const instance = ref(null)
  const app = renderer.createApp(() => h(component, { ...props, ref: instance }))
  app.provide(ssrContextKey, { modules: new Set() })
  const passthrough = { setup: (_props, { slots }) => () => h('div', {}, slots.default?.()) }
  const source = readFileSync(new URL(`../../src/modules/knowledge/ui/${component === ClientTable ? 'FileTable' : 'FileUploadModal'}.vue`, import.meta.url), 'utf8')
  for (const tag of new Set([...source.matchAll(/<(a-[a-z-]+)/g)].map((match) => match[1]))) {
    if (!['a-modal', 'a-button'].includes(tag)) app.component(tag, passthrough)
  }
  app.component('a-modal', {
    inheritAttrs: false, props: ['open', 'title'],
    setup(modal, { slots }) {
      const opened = ref(modal.open)
      watch(() => modal.open, (open) => { if (open) opened.value = true })
      return () => opened.value ? h('section', { 'data-modal': modal.title, 'data-open': modal.open }, [slots.default?.(), slots.footer?.()]) : null
    }
  })
  app.component('a-button', { setup: (_props, { attrs, slots }) => () => h('button', attrs, slots.default?.()) })
  const host = node('root')
  app.mount(host)
  mounted.push(app)
  return { host, instance }
}

for (const kind of ['upload', 'table']) {
  test(`${kind} 真实模板接收默认、保护用户选择，重开重新读取目录`, async () => {
    const { ocrApi } = fixture()
    const pending = []
    ocrApi.getOptions = () => new Promise((resolve) => pending.push(resolve))
    const props = reactive(kind === 'upload'
      ? { visible: false, folderTree: [], currentFolderId: null, isFolderMode: false, mode: 'file' }
      : { readonly: false })
    const { host, instance } = mountClient(kind === 'upload' ? ClientUpload : ClientTable, props)
    const open = () => kind === 'upload' ? (props.visible = true) : instance.value.startPendingParse(1)
    const selector = () => find(host, (item) => item.props['data-selector'])
    open()
    await nextTick()
    assert.equal(selector().props['data-selected'], '')
    assert.equal(pending.length, 1)
    pending.shift()(catalog('mineru_ocr'))
    await nextTick()
    await nextTick()
    assert.equal(selector().props['data-selected'], 'mineru_ocr')

    find(host, (item) => item.props['data-select-engine']).props.onClick()
    await nextTick()
    find(host, (item) => item.props['data-refresh-options']).props.onClick()
    pending.shift()(catalog('mineru_ocr'))
    await nextTick()
    await nextTick()
    assert.equal(selector().props['data-selected'], 'rapid_ocr')

    if (kind === 'upload') props.visible = false
    else find(host, (item) => item.type === 'button' && item.children.some((child) => child.text === '取消')).props.onClick()
    await nextTick()
    open()
    await nextTick()
    assert.equal(selector().props['data-selected'], '')
    assert.equal(pending.length, 1, '重新打开必须重新挂载选择器并读取服务端默认')
    pending.shift()(catalog('rapid_ocr'))
    await nextTick()
    await nextTick()
    assert.equal(selector().props['data-selected'], 'rapid_ocr')
  })
}

test('上传目录未加载时拒绝提交，即使已有可提交的文件', async () => {
  const { submissions, errors } = fixture()
  const { state } = await openUpload()
  state.fileList.value = [{ uid: 'file-a', name: 'report.pdf', status: 'done', response: { file_path: 'report.pdf' } }]
  await state.chunkData()
  assert.deepEqual(submissions, [])
  assert.deepEqual(errors, ['OCR 引擎选项尚未加载，请稍后重试'])
})

test('重新解析优先保留文件保存的参数，服务端默认更新不替换它', async () => {
  const { documentApi, submissions } = fixture()
  documentApi.getDocumentInfo = async () => ({ meta: { processing_params: { ocr_engine: 'rapid_ocr' } } })
  const state = mount(FileTable, reactive({ readonly: false }))
  await state.handleParseFile({ file_id: 'file-a', status: 'error_parsing' })
  state.handleOcrOptionsLoaded(catalog('mineru_ocr'))
  assert.equal(state.parseParams.value.ocr_engine, 'rapid_ocr')
  await state.handleParseConfigConfirm()
  assert.deepEqual(submissions, [{ ids: ['file-a'], params: { ocr_engine: 'rapid_ocr' } }])
})

test('文件解析没有默认或明确选择时不登记任务并保留对话框', async () => {
  const { submissions, errors } = fixture()
  const state = mount(FileTable, reactive({ readonly: false }))
  state.startPendingParse(1)
  await state.handleParseConfigConfirm()
  assert.deepEqual(submissions, [])
  assert.equal(state.parseConfigModalVisible.value, true)
  assert.deepEqual(errors, ['OCR 引擎选项尚未加载，请稍后重试'])
})

test('选择器区分凭据已配置和健康检查通过，已配置引擎仍可选择', async () => {
  const { ocrApi } = fixture()
  ocrApi.getHealth = async () => ({ health: { mineru_ocr: { status: 'configured' }, rapid_ocr: { status: 'healthy' } } })
  const events = []
  const state = mount(Selector, reactive({ modelValue: '', allowedEngines: [], includeDisable: false, disabled: false, placeholder: '请选择 OCR 方法' }), (...event) => events.push(event))
  await state.loadOptions()
  await state.refreshHealth()
  assert.equal(state.healthLabel('mineru_ocr'), '已配置，未验证')
  assert.equal(state.healthClass('mineru_ocr'), 'unknown')
  assert.equal(state.healthLabel('rapid_ocr'), '可用')
  assert.equal(state.healthClass('rapid_ocr'), 'healthy')
  assert.deepEqual(state.activeEngines.value.map((engine) => engine.engine_id), ['mineru_ocr', 'rapid_ocr'])
  state.selectEngine('mineru_ocr')
  assert.deepEqual(events.slice(-2), [['update:modelValue', 'mineru_ocr'], ['change', 'mineru_ocr']])
})
