import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import { compileTemplate, parse } from 'vue/compiler-sfc'
import { computed, reactive, ref } from 'vue'
import { formatModifiedAt, normalizeWPSFiles, safeCloudUrl, wpsAuthorizationOrigin, wpsCallbackUrl } from '../../src/utils/cloudDrive.js'

const read = (path) => readFileSync(new URL(`../../src/${path}`, import.meta.url), 'utf8')

/** 执行组件真实脚本，仅替换网络、浏览器与生命周期。 */
function setup(path, bindings, context) {
  const { descriptor, errors } = parse(read(path))
  assert.deepEqual(errors, [])
  const script = descriptor.scriptSetup.content.replace(/import\s+[\s\S]*?from\s+['"][^'"]+['"]/g, '')
  return runInNewContext(`${script}\n;({ ${bindings.join(', ')} })`, {
    ref, reactive, computed, wpsAuthorizationOrigin, wpsCallbackUrl, onMounted: () => {}, onUnmounted: () => {}, onActivated: () => {},
    setTimeout: () => 1, clearTimeout: () => {},
    message: { success: () => {}, error: () => {} }, URL,
    window: { location: { origin: 'http://localhost:5173', hostname: 'localhost', assign: () => {} } },
    ...context
  })
}

test('云盘设置位于基本设置与 OCR 之间，桌面与移动端一致且无 AnyShare', () => {
  const source = read('components/SettingsModal.vue')
  for (const navigation of [source.split('<!-- 移动端')[0], source.slice(source.indexOf('class="settings-mobile-nav"'))]) {
    const base = navigation.indexOf("activeTab = 'base'")
    const cloud = navigation.indexOf("activeTab = 'cloudDrive'")
    const ocr = navigation.indexOf("activeTab = 'ocr'")
    assert.ok(base >= 0 && base < cloud && cloud < ocr)
  }
  for (const path of ['components/CloudDriveSettingsSection.vue', 'components/workspace/WPSCloudDrive.vue', 'components/workspace/SpacePlaceholder.vue']) {
    const source = read(path)
    assert.doesNotMatch(source, /AnyShare/i)
    const { descriptor, errors } = parse(source)
    assert.deepEqual(errors, [])
    assert.deepEqual(compileTemplate({ source: descriptor.template.content, id: path, filename: path }).errors, [])
  }
})

test('本机空配置不自动填 localhost，公开回调保存回读并清空密钥', async () => {
  const requests = []
  const component = setup('components/CloudDriveSettingsSection.vue', ['applyConfig', 'dirty', 'draft', 'saveConfig', 'connection'], {
    useUserStore: () => ({ isAdmin: true }),
    wpsCloudApi: {
      saveConfig: async (payload) => { requests.push(payload); return { ...payload, secret_configured: true } },
      getConnection: async () => ({ enabled: true, configured: true, connected: false })
    }
  })
  component.applyConfig({ enabled: false, app_id: '', redirect_uri: '', secret_configured: false })
  assert.equal(component.draft.redirect_uri, '')
  assert.equal(component.dirty.value, false)
  component.draft.enabled = true
  component.draft.app_id = 'example-app'
  component.draft.app_secret = 'example-secret'
  component.draft.redirect_uri = 'http://[::1]:5173/auth/wps/callback'
  assert.equal(component.dirty.value, true)
  await component.saveConfig()
  assert.equal(requests.length, 0)
  component.draft.redirect_uri = 'https://example.com/auth/wps/callback'
  await component.saveConfig()
  assert.equal(requests[0].app_secret, 'example-secret')
  assert.equal(component.draft.app_secret, '')
  assert.equal(component.dirty.value, false)
  assert.equal(component.connection.value.connected, false)
  component.draft.redirect_uri = 'http://example.com/auth/wps/callback'
  await component.saveConfig()
  assert.equal(requests.length, 1)
})

test('公网 HTTPS 页面生成同源回调，复制登记值并阻止异域发起授权', async () => {
  const copied = []
  const navigated = []
  let calls = 0
  const component = setup('components/CloudDriveSettingsSection.vue', ['applyConfig', 'draft', 'connection', 'originMismatch', 'connect', 'copyCallback'], {
    useUserStore: () => ({ isAdmin: true }),
    window: { location: { origin: 'https://example.com', assign: (url) => navigated.push(url) } },
    navigator: { clipboard: { writeText: async (url) => copied.push(url) } },
    wpsCloudApi: { authorize: async () => { calls++; return { authorization_url: 'https://openapi.wps.cn/oauth2/auth' } } }
  })
  component.applyConfig({ enabled: false, app_id: '', redirect_uri: '', secret_configured: false })
  assert.equal(component.draft.redirect_uri, 'https://example.com/auth/wps/callback')
  await component.copyCallback()
  assert.equal(copied[0], 'https://example.com/auth/wps/callback')
  component.connection.value = { authorization_origin: 'https://other.example.com' }
  assert.equal(component.originMismatch.value, true)
  await component.connect()
  assert.equal(calls, 0)
  component.connection.value.authorization_origin = 'https://example.com'
  await component.connect()
  assert.equal(calls, 1)
  assert.equal(navigated[0], 'https://openapi.wps.cn/oauth2/auth')
  const drive = setup('components/workspace/WPSCloudDrive.vue', ['connection', 'connect'], {
    inject: () => ({}), wpsCloudApi: { authorize: () => { throw new Error('异域不能发起授权') } }
  })
  drive.connection.value = { authorization_origin: 'https://example.com' }
  await drive.connect()
})

test('开发联调使用独立地址；启动和停止不覆盖正式回调', async () => {
  const requests = []
  const tunnelStates = [
    { enabled: true, state: 'starting', running: false, callback_url: '', message: 'starting' },
    { enabled: true, state: 'running', running: true, public_url: 'https://zs-dev.zs-wps.com', callback_url: 'https://zs-dev.zs-wps.com/auth/wps/callback', message: 'ready' }
  ]
  const component = setup('components/CloudDriveSettingsSection.vue', ['applyConfig', 'draft', 'connection', 'tunnel', 'startDevelopmentTunnel', 'stopDevelopmentTunnel', 'copyDevelopmentCallback', 'authorizationOrigin'], {
    useUserStore: () => ({ isAdmin: true }),
    window: { location: { origin: 'http://localhost:5173', hostname: 'localhost', assign: () => {} } },
    navigator: { clipboard: { writeText: async (url) => requests.push(url) } },
    wpsCloudApi: {
      startDevelopmentTunnel: async () => tunnelStates[0],
      getDevelopmentTunnel: async () => tunnelStates[1],
      getConnection: async () => ({ enabled: true, configured: true, authorization_origin: null, development_tunnel_enabled: true, development_tunnel_active: true, development_callback_origin: 'https://zs-dev.zs-wps.com', development_callback_url: tunnelStates[1].callback_url })
    }
  })
  component.applyConfig({ enabled: false, app_id: 'example-app', redirect_uri: 'https://formal.example.com/auth/wps/callback', secret_configured: true })
  component.connection.value = { authorization_origin: 'https://formal.example.com', development_tunnel_enabled: true }
  const formalCallback = component.draft.redirect_uri
  await component.startDevelopmentTunnel()
  assert.equal(component.tunnel.value.callback_url, tunnelStates[1].callback_url)
  assert.equal(component.draft.redirect_uri, formalCallback)
  assert.equal(component.authorizationOrigin.value, 'https://formal.example.com')
  assert.equal(wpsAuthorizationOrigin({ development_tunnel_active: true, development_callback_origin: 'https://zs-dev.zs-wps.com' }, 'https://zs-dev.zs-wps.com'), 'https://zs-dev.zs-wps.com')
  await component.copyDevelopmentCallback()
  assert.deepEqual(requests, [tunnelStates[1].callback_url])
  await component.stopDevelopmentTunnel()
  assert.equal(component.draft.redirect_uri, formalCallback)
})

test('WPS 回调拒绝 localhost、IP、HTTP、多余参数，保留合法路径和端口', () => {
  for (const value of [
    'http://localhost:5173/auth/wps/callback', 'https://localhost/auth/wps/callback',
    'https://app.localhost./auth/wps/callback', 'https://app.local/auth/wps/callback',
    'https://127.0.0.1/auth/wps/callback', 'https://[::1]/auth/wps/callback',
    'http://example.com/auth/wps/callback', 'https://example.com/other',
    'https://user:secret@example.com/auth/wps/callback',
    'https://example.com/auth/wps/callback?code=secret', 'https://example.com/auth/wps/callback#fragment'
  ]) assert.equal(wpsCallbackUrl(value), '')
  assert.equal(wpsCallbackUrl('https://example.com:8443/auth/wps/callback'), 'https://example.com:8443/auth/wps/callback')
})

test('密钥留空保留、清除须停用、普通用户只读取本人连接', async () => {
  const requests = []
  const component = setup('components/CloudDriveSettingsSection.vue', ['load', 'applyConfig', 'draft', 'saveConfig'], {
    useUserStore: () => ({ isAdmin: false }),
    wpsCloudApi: {
      getConfig: () => { throw new Error('普通用户不应读取应用配置') },
      getConnection: async () => ({ connected: false }),
      saveConfig: async (payload) => { requests.push(payload); return { ...payload, secret_configured: !payload.clear_secret } }
    }
  })
  await component.load()
  component.applyConfig({ enabled: true, app_id: 'example-app', redirect_uri: 'https://example.com/auth/wps/callback', secret_configured: true })
  component.draft.app_id = 'changed-app'
  await component.saveConfig()
  assert.equal(requests[0].app_secret, '')
  assert.equal(requests[0].clear_secret, false)
  component.draft.clear_secret = true
  await component.saveConfig()
  assert.equal(requests.length, 1)
  component.draft.enabled = false
  await component.saveConfig()
  assert.equal(requests[1].clear_secret, true)
})

test('配置未连接或读取失败时清空文件，不请求文档库', async () => {
  const component = setup('components/workspace/WPSCloudDrive.vue', ['refresh', 'files', 'drives', 'connection', 'connectionError'], {
    inject: () => ({}), safeCloudUrl, normalizeWPSFiles, formatModifiedAt,
    wpsCloudApi: {
      checkConnection: async () => ({ connected: false, state: 'disabled' }),
      getDrives: () => { throw new Error('未连接不应读取文件') }
    }
  })
  component.files.value = [{ id: 'old-user-file' }]
  await component.refresh()
  assert.equal(component.files.value.length, 0)
  assert.equal(component.connection.value.state, 'disabled')
  assert.equal(component.connectionError.value, false)
})

test('快速切换目录不会让旧响应覆盖新目录；断线刷新会清空旧账号文件', async () => {
  let resolveOld
  let failConnection = false
  let calls = 0
  const component = setup('components/workspace/WPSCloudDrive.vue', ['refresh', 'selectDrive', 'files', 'connectionError'], {
    inject: () => ({}), safeCloudUrl, normalizeWPSFiles, formatModifiedAt,
    wpsCloudApi: {
      checkConnection: async () => { if (failConnection) throw new Error('offline'); return { connected: true } },
      getDrives: async () => ({ items: [{ id: 'drive-a', name: '文档库' }] }),
      getFiles: () => ++calls === 1 ? new Promise((resolve) => { resolveOld = resolve }) : Promise.resolve({ items: [{ id: 'new', name: '新目录文件' }] })
    }
  })
  await component.refresh()
  const old = component.selectDrive({ id: 'old' })
  await component.selectDrive({ id: 'new' })
  resolveOld({ items: [{ id: 'old', name: '旧目录文件' }] })
  await old
  assert.equal(component.files.value[0].id, 'new')
  failConnection = true
  await component.refresh()
  assert.equal(component.files.value.length, 0)
  assert.equal(component.connectionError.value, true)
})

test('打开下载仅接受 HTTPS 无凭据地址，搜索包装按协议解包', () => {
  for (const value of ['javascript:alert(1)', 'http://example.com/file', 'https://user:secret@example.com/file', 'bad-url']) {
    assert.equal(safeCloudUrl(value), '')
  }
  assert.equal(safeCloudUrl('https://example.com/file'), 'https://example.com/file')
  assert.deepEqual(normalizeWPSFiles([{ file: { id: 'a', name: 'example.docx' } }, { id: 'b', name: '目录' }]), [{ id: 'a', name: 'example.docx' }, { id: 'b', name: '目录' }])
  assert.equal(formatModifiedAt(undefined), '—')
})
