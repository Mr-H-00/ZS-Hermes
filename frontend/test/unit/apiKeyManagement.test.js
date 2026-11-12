import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { webcrypto } from 'node:crypto'
import { setImmediate } from 'node:timers'
import test from 'node:test'
import { compileScript, parse } from 'vue/compiler-sfc'
import { createRenderer, h, nextTick } from 'vue'
import { createTestServer } from '../helpers/createTestServer.js'

const storageKey = 'yuxi_pending_api_key_request_id'

/** 完整编译组件，在 API 和浏览器边界替换依赖，通过表单事件观察实际提交。 */
async function setupComponent({ storage = new Map(), keys = [], create, update } = {}) {
  const errors = []
  globalThis.__apiKeyTest = {
    api: { list: async () => ({ api_keys: keys }), create, update },
    message: { error: (text) => errors.push(text), success: () => {} }
  }
  globalThis.crypto ??= webcrypto
  globalThis.sessionStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key)
  }
  const server = await createTestServer({
    server: { middlewareMode: true },
    appType: 'custom',
    ssr: { noExternal: ['ant-design-vue'] },
    plugins: [
      {
        name: 'api-key-form-test',
        enforce: 'pre',
        resolveId(id) {
          if (['virtual:api-key-form', 'ant-design-vue', '@/apis/apikey_api'].includes(id))
            return '\0' + id
        },
        load(id) {
          if (id === '\0ant-design-vue')
            return 'export const message = globalThis.__apiKeyTest.message'
          if (id === '\0@/apis/apikey_api' || id.endsWith('/src/apis/apikey_api.js'))
            return 'export const apikeyApi = globalThis.__apiKeyTest.api'
          if (id === '\0virtual:api-key-form') {
            const source = readFileSync(
              new URL(
                '../../src/modules/settings/ui/ApiKeyManagementComponent.vue',
                import.meta.url
              ),
              'utf8'
            )
            return compileScript(parse(source).descriptor, {
              id: 'api-key-form',
              inlineTemplate: true
            }).content
          }
        }
      }
    ]
  })
  const node = (type) => ({ type, children: [], props: {}, parent: null })
  const renderer = createRenderer({
    createElement: node,
    createText: (text) => ({ ...node('text'), text }),
    createComment: () => node('comment'),
    insert(child, parent, anchor) {
      if (child.parent) child.parent.children.splice(child.parent.children.indexOf(child), 1)
      child.parent = parent
      const index = anchor ? parent.children.indexOf(anchor) : -1
      if (index < 0) parent.children.push(child)
      else parent.children.splice(index, 0, child)
    },
    remove(child) {
      child.parent.children.splice(child.parent.children.indexOf(child), 1)
    },
    setText: (node, text) => {
      node.text = text
    },
    setElementText: (node, text) => {
      node.text = text
      node.children = []
    },
    parentNode: (node) => node.parent,
    nextSibling: (node) => node.parent?.children[node.parent.children.indexOf(node) + 1],
    patchProp: (node, key, old, value) => {
      node.props[key] = value
    }
  })
  const { default: Component } = await server.ssrLoadModule('virtual:api-key-form')
  const app = renderer.createApp(Component)
  for (const name of [
    'a-button',
    'a-tooltip',
    'a-alert',
    'a-spin',
    'a-empty',
    'a-popconfirm',
    'a-switch',
    'a-form',
    'a-form-item',
    'a-input',
    'a-select',
    'a-select-option',
    'a-date-picker',
    'a-radio-group',
    'a-radio',
    'a-tag'
  ]) {
    app.component(name, {
      setup:
        (_, { attrs, slots }) =>
        () =>
          h(name, attrs, slots.default?.())
    })
  }
  app.component('a-modal', {
    props: ['open'],
    setup:
      (props, { attrs, slots }) =>
      () =>
        props.open ? h('modal', attrs, slots.default?.()) : null
  })
  app.component('a-table', {
    props: ['dataSource', 'columns'],
    setup:
      (props, { slots }) =>
      () =>
        h(
          'table',
          props.dataSource.flatMap((record) =>
            props.columns.map((column) => slots.bodyCell({ record, column }))
          )
        )
  })
  const host = node('root')
  const find = (node, predicate) =>
    predicate(node) ? node : node.children.map((child) => find(child, predicate)).find(Boolean)
  const settle = async () => {
    await new Promise((resolve) => setImmediate(resolve))
    await nextTick()
  }
  app.mount(host)
  await settle()
  const view = {
    storage,
    errors,
    settle,
    find: (predicate) => find(host, predicate),
    modal: () => find(host, (node) => node.type === 'modal'),
    field: (type) => find(host, (node) => node.type === type),
    openCreate: async () => {
      await find(
        host,
        (node) => node.type === 'a-button' && textOf(node).includes('创建 API Key')
      ).props.onClick()
      await settle()
    },
    openEdit: async () => {
      const tooltip = find(host, (node) => node.props.title === '调整权限和来源')
      await find(tooltip, (node) => node.type === 'a-button').props.onClick()
      await settle()
    },
    set: async (type, value) => {
      view.field(type).props['onUpdate:value'](value)
      await settle()
    },
    save: async () => {
      await view.modal().props.onOk()
      await settle()
    },
    cancel: async () => {
      view.modal().props.onCancel?.()
      view.modal().props['onUpdate:open'](false)
      await settle()
    },
    close: async () => {
      app.unmount()
      await server.close()
      delete globalThis.__apiKeyTest
      delete globalThis.sessionStorage
    }
  }
  return view
}

/** 读取渲染树的可见文案。 */
function textOf(node) {
  return (node.text || '') + node.children.map(textOf).join('')
}

test('本人 Agents Key 无需 APP，失败重试保留请求 ID，成功后清除', async () => {
  const requests = []
  const view = await setupComponent({
    create: async (data) => {
      requests.push({ ...data })
      if (requests.length === 1) throw new Error('响应中断')
      return { secret: 'test-secret' }
    }
  })
  try {
    await view.openCreate()
    const id = view.storage.get(storageKey)
    assert.match(id, /^[0-9a-f]{32}$/)
    assert.equal(view.field('a-radio-group').props.value, 'self')
    assert.equal(
      view.find((node) => node.props.placeholder === '如：customer-service'),
      undefined
    )
    assert.ok(view.find((node) => node.props.message?.includes('不限于此 Key 新建的会话')))
    await view.set('a-input', 'personal-key')
    await view.save()
    assert.equal(view.errors.at(-1), '响应中断')
    assert.equal(view.storage.get(storageKey), id)
    await view.save()
    assert.equal(requests[0].request_id, id)
    assert.equal(requests[1].request_id, id)
    assert.equal(requests[1].access_level, 'agents')
    assert.equal(requests[1].app_id, null)
    assert.equal(view.storage.has(storageKey), false)
    assert.equal(view.modal().props.title, 'API Key 已创建')
    assert.ok(textOf(view.modal()).includes('test-secret'))
  } finally {
    await view.close()
  }
})

test('已有创建 ID 保留，取消后重新打开生成新 ID', async () => {
  const existing = '36b8f84d-df4e-4d49-b662-bcde71a8764f'
  const view = await setupComponent({ storage: new Map([[storageKey, existing]]) })
  try {
    await view.openCreate()
    assert.equal(view.storage.get(storageKey), existing)
    await view.cancel()
    assert.equal(view.storage.has(storageKey), false)
    await view.openCreate()
    const first = view.storage.get(storageKey)
    assert.match(first, /^[0-9a-f]{32}$/)
    await view.cancel()
    await view.openCreate()
    assert.notEqual(view.storage.get(storageKey), first)
  } finally {
    await view.close()
  }
})

test('APP 身份要求有效标识，切回本人不会提交保留的 APP 草稿', async () => {
  const requests = []
  const view = await setupComponent({
    create: async (data) => {
      requests.push(data)
      return { secret: 'test-secret' }
    }
  })
  try {
    await view.openCreate()
    await view.set('a-input', 'app-key')
    await view.set('a-radio-group', 'app')
    await view.save()
    assert.equal(requests.length, 0)
    const appInput = () => view.find((node) => node.props.placeholder === '如：customer-service')
    appInput().props['onUpdate:value'](' invalid ')
    await view.save()
    assert.equal(requests.length, 0)
    appInput().props['onUpdate:value']('service-a')
    await view.set('a-radio-group', 'self')
    await view.save()
    assert.equal(requests[0].app_id, null)
    await view.modal().props['onUpdate:open'](false)
    await view.openCreate()
    await view.set('a-input', 'app-key')
    await view.set('a-radio-group', 'app')
    appInput().props['onUpdate:value']('service-a')
    await view.save()
    assert.equal(requests[1].app_id, 'service-a')
  } finally {
    await view.close()
  }
})

test('编辑保留 APP，切换显示数据范围后果，取消不会改变已保存身份', async () => {
  const changes = []
  const view = await setupComponent({
    keys: [{ id: 7, name: 'Existing', access_level: 'agents', app_id: 'service-a' }],
    update: async (id, data) => changes.push({ id, data })
  })
  try {
    await view.openEdit()
    assert.equal(view.field('a-radio-group').props.value, 'app')
    await view.save()
    assert.equal(changes[0].data.app_id, 'service-a')
    await view.openEdit()
    await view.set('a-radio-group', 'self')
    assert.ok(view.find((node) => node.props.description?.includes('不移动历史数据')))
    assert.ok(view.find((node) => node.props.message === '保存后切换到 Key 所属用户的本人资源'))
    await view.cancel()
    assert.equal(changes.length, 1)
    await view.openEdit()
    assert.equal(view.field('a-radio-group').props.value, 'app')
    await view.set('a-radio-group', 'self')
    await view.save()
    assert.equal(changes[1].id, 7)
    assert.deepEqual(changes[1].data, { access_level: 'agents', app_id: null })
  } finally {
    await view.close()
  }
})
