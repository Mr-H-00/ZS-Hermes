import assert from 'node:assert/strict'
import test from 'node:test'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createTestServer } from '../helpers/createTestServer.js'

async function renderHeatmap(props) {
  const server = await createTestServer({ server: { middlewareMode: true, hmr: false } })
  try {
    const { default: component } = await server.ssrLoadModule('/src/modules/dashboard/ui/DashboardActivityHeatmap.vue')
    return await renderToString(createSSRApp(() => h(component, props)))
  } finally {
    await server.close()
  }
}

test('热力图按 valueKey 渲染消息计数与总量', async () => {
  const html = await renderHeatmap({
    data: [{ date: '2026-10-24', message_count: 3 }, { date: '2026-10-25', message_count: 4 }],
    valueKey: 'message_count', valueLabel: '条消息', totalUnit: '条消息', ariaLabel: '近 120 天消息活动'
  })
  assert.match(html, /aria-label="近 120 天消息活动"/)
  assert.match(html, /title="2026-10-25：4 条消息"/)
  assert.match(html, /class="heatmap-total"[^>]*>7 条消息/)
})

test('热力图默认口径保持 Dashboard 活跃用户语义', async () => {
  const html = await renderHeatmap({ data: [{ date: '2026-10-25', active_users: 2 }] })
  assert.match(html, /title="2026-10-25：2 位活跃用户"/)
  assert.match(html, /class="heatmap-total"[^>]*>2 次活跃记录/)
})
