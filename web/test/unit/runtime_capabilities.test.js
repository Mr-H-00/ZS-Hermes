import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

async function withServer(run) {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom',
    ssr: { noExternal: ['ant-design-vue'] },
    plugins: [
      {
        name: 'test-runtime-capability-message-api',
        enforce: 'pre',
        resolveId(id) {
          return id === 'ant-design-vue' ? '\0test-runtime-capability-message-api' : null
        },
        load(id) {
          if (id !== '\0test-runtime-capability-message-api') return null
          return 'export const message = { error() {} }'
        }
      }
    ]
  })
  try {
    await run(server)
  } finally {
    await server.close()
  }
}

async function prepareStores(server) {
  setActivePinia(createPinia())
  const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
  const userStore = useUserStore()
  userStore.token = 'runtime-capability-test-token'
  userStore.userId = 1
  userStore.userRole = 'superadmin'

  const { useRuntimeCapabilitiesStore } = await server.ssrLoadModule(
    '/src/stores/runtimeCapabilities.js'
  )
  return useRuntimeCapabilitiesStore()
}

function jsonResponse(payload) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  })
}

const dashboardResponses = {
  '/api/dashboard/stats': { total_conversations: 3 },
  '/api/dashboard/stats/users': { total_users: 2 },
  '/api/dashboard/stats/tools': { total_calls: 4 },
  '/api/dashboard/stats/agents': { total_agents: 1 },
  '/api/dashboard/stats/knowledge': { total_databases: 5 }
}

test('knowledge capability 关闭时 Dashboard 不请求知识接口且保留核心统计', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      const url = String(input)
      requests.push(url)
      if (url === '/api/system/discovery') {
        return jsonResponse({ capabilities: { features: { knowledge: false } } })
      }
      return jsonResponse(dashboardResponses[url])
    }

    const runtimeCapabilitiesStore = await prepareStores(server)
    await runtimeCapabilitiesStore.ensureLoaded()
    assert.equal(runtimeCapabilitiesStore.knowledgeEnabled, false)

    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')
    const result = await dashboardApi.getAllStats({
      includeKnowledge: runtimeCapabilitiesStore.knowledgeEnabled
    })

    assert.equal(requests.includes('/api/dashboard/stats/knowledge'), false)
    assert.deepEqual(
      requests.filter((url) => url.startsWith('/api/dashboard/')),
      [
        '/api/dashboard/stats',
        '/api/dashboard/stats/users',
        '/api/dashboard/stats/tools',
        '/api/dashboard/stats/agents'
      ]
    )
    assert.deepEqual(result.basic, dashboardResponses['/api/dashboard/stats'])
    assert.deepEqual(result.users, dashboardResponses['/api/dashboard/stats/users'])
    assert.deepEqual(result.tools, dashboardResponses['/api/dashboard/stats/tools'])
    assert.deepEqual(result.agents, dashboardResponses['/api/dashboard/stats/agents'])
    assert.equal(result.knowledge, null)
  })
})

test('knowledge capability 开启时 Dashboard 保留知识统计请求', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      const url = String(input)
      requests.push(url)
      if (url === '/api/system/discovery') {
        return jsonResponse({ capabilities: { features: { knowledge: true } } })
      }
      return jsonResponse(dashboardResponses[url])
    }

    const runtimeCapabilitiesStore = await prepareStores(server)
    await runtimeCapabilitiesStore.ensureLoaded()
    assert.equal(runtimeCapabilitiesStore.knowledgeEnabled, true)

    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')
    const result = await dashboardApi.getAllStats({
      includeKnowledge: runtimeCapabilitiesStore.knowledgeEnabled
    })

    assert.equal(requests.filter((url) => url === '/api/dashboard/stats/knowledge').length, 1)
    assert.deepEqual(result.knowledge, dashboardResponses['/api/dashboard/stats/knowledge'])
  })
})

test('knowledge capability 关闭时 Dashboard 移动端保持单列布局', () => {
  const dashboardSource = readFileSync(
    new URL('../../src/views/DashboardView.vue', import.meta.url),
    'utf8'
  )
  const mobileStyles = dashboardSource.split('@media (max-width: 768px)')[1]

  assert.match(
    mobileStyles,
    /&\.without-knowledge\s+\.grid-item\.tool-stats\s*\{\s*grid-column:\s*1\s*\/\s*2;/
  )
})

test('能力发现失败后保持 fail-closed，并允许下一次调用恢复', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    let attempts = 0
    globalThis.fetch = async (input) => {
      assert.equal(String(input), '/api/system/discovery')
      attempts += 1
      if (attempts === 1) throw new TypeError('temporary network failure')
      return jsonResponse({ capabilities: { features: { knowledge: true } } })
    }

    const store = await prepareStores(server)

    assert.deepEqual(await store.ensureLoaded(), { knowledge: false })
    assert.equal(store.status, 'error')
    assert.equal(store.knowledgeEnabled, false)

    assert.deepEqual(await store.ensureLoaded(), { knowledge: true })
    assert.equal(attempts, 2)
    assert.equal(store.status, 'ready')
    assert.equal(store.error, null)
    assert.equal(store.knowledgeEnabled, true)
  })
})

test('knowledge capability 关闭时 Agent 提及资源不请求知识库', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      const url = String(input)
      requests.push(url)
      if (url === '/api/system/discovery') {
        return jsonResponse({ capabilities: { features: { knowledge: false } } })
      }
      if (url === '/api/system/mcp-servers' || url === '/api/skills/accessible') {
        return jsonResponse({ data: [] })
      }
      return jsonResponse({})
    }

    const runtimeCapabilitiesStore = await prepareStores(server)
    await runtimeCapabilitiesStore.ensureLoaded()
    const { useAgentStore } = await server.ssrLoadModule('/src/stores/agent.js')
    const agentStore = useAgentStore()
    await agentStore.fetchMentionResources()

    assert.equal(requests.some((url) => url.startsWith('/api/knowledge')), false)
    assert.deepEqual(agentStore.availableKnowledgeBases, [])
    assert.equal(requests.includes('/api/system/mcp-servers'), true)
    assert.equal(requests.includes('/api/skills/accessible'), true)
  })
})
