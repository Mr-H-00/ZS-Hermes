import assert from 'node:assert/strict'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

// 在真实路由定义上隔离浏览器 history 和会话边界，保留实际守卫及路由匹配。
test('模块路由保留角色拒绝、LITE 转向与已有链接', async () => {
  const server = await createServer({
    configFile: false,
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    plugins: [{
      name: 'navigation-session-boundaries',
      enforce: 'pre',
      resolveId(id) {
        if (id.startsWith('virtual:navigation-')) return `\0${id}`
        if (id.endsWith('.vue')) return '\0virtual:navigation-view'
      },
      load(id) {
        if (id.endsWith('navigation-view')) return 'export default {}'
        if (id.endsWith('navigation-user')) {
          return `export const user = { token: 'test', userId: 1,
            isLoggedIn: true, isAdmin: false, isSuperAdmin: false };
            export const useUserStore = () => user`
        }
        if (id.endsWith('navigation-agent')) {
          return 'export const useAgentStore = () => ({ isInitialized: true })'
        }
        if (id.endsWith('navigation-capabilities')) {
          return `export const capabilities = { knowledgeEnabled: true, async ensureLoaded() {} };
            export const useRuntimeCapabilitiesStore = () => capabilities`
        }
      },
      transform(code, id) {
        if (!id.endsWith('/src/router/index.js')) return
        return code.replaceAll('createWebHistory', 'createMemoryHistory')
          .replace("'@/stores/user'", "'virtual:navigation-user'")
          .replace("'@/stores/agent'", "'virtual:navigation-agent'")
          .replace("'@/stores/runtimeCapabilities'", "'virtual:navigation-capabilities'")
      }
    }]
  })
  try {
    const { default: router } = await server.ssrLoadModule('/src/router/index.js')
    const { user } = await server.ssrLoadModule('virtual:navigation-user')
    const { capabilities } = await server.ssrLoadModule('virtual:navigation-capabilities')
    await router.push('/schedules')
    assert.equal(router.currentRoute.value.name, 'SchedulesComp')
    await router.push('/capabilities/skill/example')
    assert.equal(router.currentRoute.value.name, 'ExtensionSkillDetail')
    for (const path of ['/api-services', '/capabilities/mcp/example', '/dashboard']) {
      await router.push(path)
      assert.equal(router.currentRoute.value.path, '/agent', path)
    }
    user.isAdmin = true
    await router.push('/api-services')
    assert.equal(router.currentRoute.value.name, 'ApiServicesComp')
    await router.push('/dashboard')
    assert.equal(router.currentRoute.value.path, '/agent')
    user.isSuperAdmin = true
    await router.push('/dashboard')
    assert.equal(router.currentRoute.value.name, 'DashboardComp')
    capabilities.knowledgeEnabled = false
    await router.push('/extensions')
    assert.equal(router.currentRoute.value.path, '/capabilities')
    capabilities.knowledgeEnabled = true
    await router.push('/extensions')
    assert.equal(router.currentRoute.value.name, 'ExtensionsComp')
    for (const [from, target] of [
      ['/agent-manage?tab=schedules', '/schedules'],
      ['/agent-manage?tab=providers', '/api-services'],
      ['/extensions?tab=skills', '/capabilities?tab=skills'],
      ['/extensions?tab=tools', '/capabilities?tab=tools'],
      ['/extensions?tab=mcp', '/capabilities?tab=mcp']
    ]) {
      await router.push(from)
      assert.equal(router.currentRoute.value.fullPath, target)
    }
    await router.push('/extensions/mcp/example')
    assert.equal(router.currentRoute.value.name, 'ExtensionMcpDetail')
    user.isAdmin = false
    await router.push('/extensions/skill/example')
    assert.equal(router.currentRoute.value.name, 'ExtensionSkillDetail')
    await router.push('/extensions/mcp/example')
    assert.equal(router.currentRoute.value.path, '/agent')
  } finally {
    await server.close()
  }
})
