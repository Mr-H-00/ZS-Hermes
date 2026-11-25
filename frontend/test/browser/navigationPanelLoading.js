// 在实际前端入口观察模块请求和交互结果。
export default async function verifyNavigationPanelLoading(page, origin) {
  const requests = new Set()
  const chatErrors = []
  page.on('console', (event) => {
    if (event.type() === 'error' && event.text().includes('加载对话失败')) chatErrors.push(event.text())
  })
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname
    requests.add(path.split('/').at(-1))
  })
  await page.addInitScript(() => localStorage.setItem('user_token', 'fixture-token'))
  const sessionId = '00000000-0000-4000-8000-000000000091'
  const sessionUrl = `/api/v1/agents/sessions/${sessionId}`
  const agent = {
    agent_id: 'navigation-agent',
    name: '验证智能体',
    can_run: true,
    config_json: { context: { model: 'replay:chat' } },
    configurable_items: {}
  }
  const run = { id: 'navigation-run', turn_id: 'navigation-turn', status: 'completed' }
  const turn = {
    id: run.turn_id,
    status: 'completed',
    yuxi: { current_run_id: run.id, result_run_id: run.id, runs: [run], output: [] }
  }
  const session = {
    id: sessionId,
    agent: { id: agent.agent_id, model: 'replay:chat' },
    status: 'completed',
    created_at: 1793404800,
    yuxi: { title: '导航测试会话', current_turn: { id: turn.id, status: turn.status, current_run_id: run.id } }
  }
  const items = {
    data: [{
      id: 'navigation-reply',
      type: 'message',
      role: 'assistant',
      status: 'completed',
      turn_id: turn.id,
      phase: 'final_answer',
      content: [{ type: 'output_text', text: '历史回答已加载' }],
      yuxi: { run_id: run.id, message_id: 1, output_index: 0 }
    }],
    has_more: false,
    yuxi: { runs: [run] }
  }
  const responses = {
    '/api/auth/me': { id: 1, uid: 'fixture', username: 'Fixture user', role: 'superadmin' },
    '/api/agent': { agents: [agent] },
    '/api/agent/default-chatbot': agent,
    [`/api/agent/${agent.agent_id}`]: agent,
    '/api/system/health': { status: 'ok' },
    '/api/system/info': { success: true, data: { branding: { name: '智枢' } } },
    '/api/v1/agents/sessions': { data: [session], has_more: false },
    [sessionUrl]: session,
    [`${sessionUrl}/viewed`]: session,
    [`${sessionUrl}/turns/navigation-turn`]: turn,
    [`${sessionUrl}/items`]: items,
    [`${sessionUrl}/turns/${turn.id}/items`]: items,
    [`${sessionUrl}/queue`]: { inputs: [], status: 'idle' },
    [`${sessionUrl}/attachments`]: { attachments: [] },
    [`${sessionUrl}/state`]: { agent_state: {} },
    '/api/workspace/tree': { entries: [{ path: '/fixture.txt', name: 'fixture.txt', is_dir: false }], path: '/' },
    '/api/workspace/file': { content: '网盘文件按需预览', preview_type: 'text' },
    '/api/workspace/deliverables': { items: [], total: 0 }
  }
  await page.route('**/api/**', async (route) => {
    const { pathname } = new URL(route.request().url())
    await route.fulfill({ json: responses[pathname] ?? [] })
  })

  const requestedModule = (name) => [...requests].some(
    (file) => file === `${name}.vue` || (file.startsWith(`${name}-`) && file.endsWith('.js'))
  )

  const chatError = page.getByText(/^加载对话失败/)
  const waitForChat = async (content) => {
    await content.or(chatError).first().waitFor()
    if (chatErrors.length) throw new Error(chatErrors[0])
    if (await chatError.isVisible()) throw new Error(await chatError.innerText())
  }
  const workspace = page.locator('.agent-session-workspace')
  const draft = workspace.locator('.chat-greeting-input')
  const reply = workspace.getByText('历史回答已加载', { exact: true })

  await page.goto(origin)
  await page.getByRole('button', { name: '去对话', exact: true }).click()
  await waitForChat(draft)

  await page.getByRole('link', { name: '网盘', exact: true }).click()
  await page.getByRole('heading', { name: '网盘', exact: true }).waitFor()
  await page.getByText('fixture.txt', { exact: true }).click()
  await page.getByText('网盘文件按需预览', { exact: true }).waitFor()

  for (const [label, panel, module] of [
    ['我的成果', '.deliverables-browser', 'DeliverablesBrowser'],
    ['我的云盘', '.wps-cloud-drive', 'WPSCloudDrive']
  ]) {
    if (requestedModule(module)) throw new Error(`${label} 在打开前已请求模块`)
    await page.getByRole('button', { name: label, exact: true }).click()
    await page.locator(panel).waitFor()
    if (!requestedModule(module)) throw new Error(`${label} 打开时未单独加载模块`)
  }

  await page.getByText('导航测试会话', { exact: true }).first().click()
  await waitForChat(reply)
  await page.getByRole('button', { name: '打开侧边栏' }).click()
  await page.getByRole('tablist', { name: '侧边栏内容' }).waitFor()
  await page.getByRole('button', { name: '隐藏侧边栏' }).click()
  await page.getByRole('button', { name: '打开侧边栏' }).click()
  await page.getByRole('tablist', { name: '侧边栏内容' }).waitFor()
  await page.getByRole('button', { name: '最大化面板' }).click()
  await page.getByRole('tab', { name: '当前对话', exact: true }).click()
  await page.locator('[id^="main-chat-panel-"]').getByText('历史回答已加载', { exact: true }).waitFor()

  const newChat = page.locator('.nav').getByRole('link', { name: '新对话', exact: true })
  await newChat.hover()
  if (await page.locator('.nav').getByRole('link', { name: '新建对话', exact: true }).count()) {
    throw new Error('主导航仍显示独立的新建对话按钮')
  }
  await newChat.click()
  await page.waitForURL(`${origin}/agent`)
  await waitForChat(draft)
}
