import assert from 'node:assert/strict'
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

function jsonResponse(payload) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'content-type': 'application/json' }
  })
}

async function withServer(run) {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom',
    ssr: { noExternal: ['ant-design-vue'] },
    plugins: [
      {
        name: 'test-message-api',
        enforce: 'pre',
        resolveId(id) {
          return id === 'ant-design-vue' ? '\0test-message-api' : null
        },
        load(id) {
          if (id !== '\0test-message-api') return null
          return 'export const message = { error() {}, success() {}, warning() {} }'
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
  userStore.token = 'dashboard-test-token'
  userStore.userId = 1
  userStore.userRole = 'superadmin'
  return userStore
}

test('dashboardApi.getThreadStats 正确拼接时间范围与智能体过滤参数', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return jsonResponse({
        summary: { total_threads: 10, active_threads: 5 },
        daily_trends: [],
        depth_distribution: {},
        agent_distribution: [],
        top_users: [],
        status_distribution: {}
      })
    }

    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')

    const res1 = await dashboardApi.getThreadStats({ timeRange: '14days' })
    assert.equal(requests[0], '/api/dashboard/stats/threads?time_range=14days')
    assert.equal(res1.summary.total_threads, 10)

    const res2 = await dashboardApi.getThreadStats({ timeRange: '30days', agentId: 'agent-coder' })
    assert.equal(requests[1], '/api/dashboard/stats/threads?time_range=30days&agent_id=agent-coder')
    assert.equal(res2.summary.active_threads, 5)
  })
})

test('dashboardApi.getConversationFilterOptions 请求会话审计筛选项', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return jsonResponse({ users: [], agents: [] })
    }

    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')
    const result = await dashboardApi.getConversationFilterOptions()

    assert.equal(requests[0], '/api/dashboard/conversations/options')
    assert.deepEqual(result, { users: [], agents: [] })
  })
})

test('dashboardApi.getConversations 正确拼接 search 搜索关键词与分页参数', async () => {
  await withServer(async (server) => {
    storageValues.clear()
    const requests = []
    globalThis.fetch = async (input) => {
      requests.push(String(input))
      return jsonResponse({
        items: [
          {
            thread_id: 'thread-123',
            title: 'Search match',
            status: 'active'
          }
        ],
        total: 41,
        limit: 20,
        offset: 40
      })
    }

    await prepareStores(server)
    const { dashboardApi } = await server.ssrLoadModule('/src/apis/dashboard_api.js')

    const result = await dashboardApi.getConversations({
      status: 'active',
      search: 'search term',
      limit: 20,
      offset: 40
    })

    assert.equal(
      requests[0],
      '/api/dashboard/conversations?status=active&search=search+term&limit=20&offset=40'
    )
    assert.equal(result.total, 41)
    assert.equal(result.items.length, 1)
    assert.equal(result.items[0].thread_id, 'thread-123')
  })
})
