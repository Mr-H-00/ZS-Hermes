import assert from 'node:assert/strict'
import test from 'node:test'
import { createRenderer, h, ssrContextKey, toRaw } from 'vue'
import { message } from 'ant-design-vue'
import { createTestServer } from '../helpers/createTestServer.js'
import {
  normalizeWPSFiles,
  safeCloudUrl,
  wpsAuthorizationOrigin,
  wpsCallbackUrl
} from '../../src/modules/workspace/model/cloudDrive.js'

test('云盘链接和回调只接受对应协议与登记路径', () => {
  assert.equal(safeCloudUrl('https://files.example.com/a'), 'https://files.example.com/a')
  for (const url of ['javascript:alert(1)', 'http://files.example.com/a', 'https://user:secret@files.example.com/a']) {
    assert.equal(safeCloudUrl(url), '')
  }
  assert.equal(wpsCallbackUrl('https://app.example.com/auth/wps/callback'), 'https://app.example.com/auth/wps/callback')
  for (const url of [
    'http://app.example.com/auth/wps/callback', 'https://127.0.0.1/auth/wps/callback',
    'https://app.local/auth/wps/callback', 'https://app.example.com/other',
    'https://app.example.com/auth/wps/callback?extra=1'
  ]) assert.equal(wpsCallbackUrl(url), '')
})

test('联调来源仅在当前联调域名匹配时覆盖正式来源，文件列表兼容搜索包装', () => {
  const connection = {
    development_tunnel_active: true,
    development_callback_origin: 'https://dev.example.com',
    authorization_origin: 'https://app.example.com'
  }
  assert.equal(wpsAuthorizationOrigin(connection, 'https://dev.example.com'), 'https://dev.example.com')
  assert.equal(wpsAuthorizationOrigin(connection, 'https://other.example.com'), 'https://app.example.com')
  assert.deepEqual(normalizeWPSFiles([
    { file: { id: 'search-file', name: '搜索结果' } }, { id: 'listed-file', name: '目录文件' }, { id: 'missing-name' }
  ]).map((file) => file.id), ['search-file', 'listed-file'])
})

test('真实 WPS 设置组件保存后清空密钥草稿，拒绝无效回调与跨来源授权', async (t) => {
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const errors = []
  t.mock.method(message, 'error', (text) => errors.push(text))
  t.mock.method(message, 'success', () => {})
  const saved = []
  const redirects = []
  let authorizeCount = 0
  let config = {
    enabled: true, app_id: 'app', secret_configured: true,
    redirect_uri: 'https://app.example.com/auth/wps/callback'
  }
  const connection = {
    enabled: true, configured: true, connected: false,
    authorization_origin: 'https://app.example.com', development_tunnel_enabled: false
  }
  const boundary = {
    api: {
      getConfig: async () => config,
      getConnection: async () => connection,
      saveConfig: async (payload) => { saved.push(payload); config = { ...config, ...payload, secret_configured: true }; return config },
      authorize: async () => { authorizeCount++; return { authorization_url: 'https://account.wps.cn/authorize' } }
    }
  }
  globalThis.__cloudDriveBoundary = boundary
  globalThis.window = {
    location: { hostname: 'app.example.com', origin: 'https://app.example.com', assign: (url) => redirects.push(url) }
  }
  let app
  const server = await createTestServer({
    server: { middlewareMode: true }, appType: 'custom',
    plugins: [{
      name: 'cloud-drive-boundaries', enforce: 'pre',
      resolveId(id, importer) {
        if (!importer?.includes('/settings/ui/CloudDriveSettingsSection.vue')) return
        if (id.endsWith('/apis/cloud_drive_api') || id.endsWith('/apis/cloud_drive_api.js')) return '\0cloud-api'
        if (id.endsWith('/modules/identity')) return '\0cloud-user'
        if (id === '@lucide/vue') return '\0cloud-icons'
      },
      load(id) {
        if (id === '\0cloud-api') return 'export const wpsCloudApi = globalThis.__cloudDriveBoundary.api'
        if (id === '\0cloud-user') return 'export const useUserStore = () => ({ isAdmin: true })'
        if (id === '\0cloud-icons') return 'export const CheckCircle2 = { render: () => null }'
      }
    }]
  })
  t.after(async () => {
    app?.unmount()
    await server.close()
    if (previousWindow) Object.defineProperty(globalThis, 'window', previousWindow)
    else delete globalThis.window
    delete globalThis.__cloudDriveBoundary
  })
  const { default: component } = await server.ssrLoadModule('/src/modules/settings/ui/CloudDriveSettingsSection.vue')
  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
    parentNode: () => null, nextSibling: () => null
  })
  app = renderer.createApp({ ...component, render: () => h('div') })
  app.provide(ssrContextKey, { modules: new Set() })
  app.mount({})
  const state = toRaw(app._instance.setupState)
  await state.load()
  state.draft.app_secret = 'replacement-secret'
  await state.saveConfig()
  assert.equal(saved[0].app_secret, 'replacement-secret')
  assert.equal(state.draft.app_secret, '')
  assert.equal(state.dirty.value, false)
  state.draft.redirect_uri = 'http://localhost/auth/wps/callback'
  await state.saveConfig()
  assert.equal(saved.length, 1)
  assert.match(errors.at(-1), /HTTPS/)
  state.applyConnection({ ...connection, authorization_origin: 'https://other.example.com' })
  await state.connect()
  assert.equal(authorizeCount, 0)
  assert.deepEqual(redirects, [])
  state.applyConnection(connection)
  await state.connect()
  assert.deepEqual(redirects, ['https://account.wps.cn/authorize'])
})
