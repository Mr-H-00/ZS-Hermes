export default async function verifyExtensionTabNavigation(page, origin, screenshotPath) {
  await page.addInitScript(() => localStorage.setItem('user_token', 'fixture-token'))
  await page.route('**/api/**', async (route) => {
    const { pathname } = new URL(route.request().url())
    const respond = (body) =>
      route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    if (pathname === '/api/auth/me') {
      return respond({ id: 1, uid: 'fixture', username: 'Fixture user', role: 'superadmin' })
    }
    if (pathname === '/api/system/mcp-servers/fixture') {
      return respond({ success: true, data: { name: 'Fixture MCP', slug: 'fixture', enabled: true } })
    }
    if (pathname === '/api/v1/agents/sessions') return respond({ data: [], has_more: false })
    if (pathname === '/api/workspace/tree') return respond({ entries: [], path: '/' })
    return respond([])
  })

  const tabs = page.locator('.page-header-tabs')
  const notice = page.getByText('该扩展标签不可用，已切换到可访问的标签', { exact: true })
  await page.goto(`${origin}/capabilities`)
  for (const [label, key] of [['工具', 'tools'], ['MCP', 'mcp']]) {
    await tabs.getByRole('button', { name: label, exact: true }).click()
    await page.waitForURL(`${origin}/capabilities?tab=${key}`)
  }

  for (const [entry, path, label, key, panel] of [
    ['API服务', '/api-services', 'API集成', 'api', '.api-integration-panel'],
    ['网盘', '/workspace', '我的成果', 'deliverables', '.deliverables-browser'],
    ['网盘', '/workspace', '我的云盘', 'cloud', '.wps-cloud-drive'],
    ['数据总览', '/dashboard', '智能体反馈', 'feedback', '.feedback-list']
  ]) {
    await page.getByRole('link', { name: entry, exact: true }).click()
    await tabs.getByRole('button', { name: label, exact: true }).click()
    const content = page.locator(panel)
    await content.or(notice).first().waitFor()
    if (await notice.isVisible()) {
      throw new Error(`${entry} → ${label} 触发了扩展标签不可用提示`)
    }
    await page.waitForURL(`${origin}${path}?tab=${key}`)
    await tabs.locator('.active').getByText(label, { exact: true }).waitFor()
    await content.waitFor()
  }

  if (screenshotPath) await page.screenshot({ path: screenshotPath })

  const navigate = (path) => page.evaluate((target) => {
    const router = document.querySelector('#app').__vue_app__.config.globalProperties.$router
    return router.push(target).then(() => undefined)
  }, path)

  for (const path of ['/capabilities/mcp/fixture?tab=api', '/extensions/mcp/fixture?tab=api']) {
    await navigate('/capabilities?tab=mcp')
    await navigate(path)
    await page.getByRole('navigation', { name: 'MCP 详情导航' }).getByText('Fixture MCP').waitFor()
    await page.waitForURL(`${origin}${path}`)
    if (await notice.isVisible()) throw new Error(`${path} 触发了列表的标签校验`)
  }

  await navigate('/capabilities?tab=api')
  await notice.waitFor()
  await page.waitForURL(`${origin}/capabilities`)
  await tabs.locator('.active').getByText('技能', { exact: true }).waitFor()
}
