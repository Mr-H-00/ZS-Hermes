// 使用超级管理员会话运行，验证单页导航时缓存的扩展页不会改写其他标签。
async (page) => {
  const origin = new URL(page.url()).origin
  const notice = page.getByText('该扩展标签不可用，已切换到可访问的标签', { exact: true })
  const tabs = page.locator('.page-header-tabs')
  const navigate = (path) => page.evaluate((path) => {
    const router = document.querySelector('#app').__vue_app__.config.globalProperties.$router
    return router.push(path).then(() => undefined)
  }, path)

  await page.goto(`${origin}/capabilities`)
  for (const [label, key] of [['工具', 'tools'], ['MCP', 'mcp']]) {
    await tabs.getByRole('button', { name: label, exact: true }).click()
    await page.waitForURL(`${origin}/capabilities?tab=${key}`)
    await tabs.locator('.active').getByText(label, { exact: true }).waitFor()
  }

  for (const [entry, path, key, label, panel] of [
    ['模型服务', '/api-services', 'api', 'API集成', '.api-integration-panel'],
    ['网盘', '/workspace', 'deliverables', '我的成果', '.deliverables-browser'],
    ['驾驶舱', '/dashboard', 'feedback', '智能体反馈', '.feedback-list']
  ]) {
    if (entry !== '网盘') {
      await page.getByRole('link', { name: entry, exact: true }).click()
    } else {
      await page.locator('.user-info-dropdown').click()
      await page.getByRole('menuitem', { name: entry, exact: true }).click()
    }
    await tabs.getByRole('button', { name: label, exact: true }).click()
    const content = page.locator(panel)
    await content.or(notice).first().waitFor()
    if (!(await content.isVisible()) || (await notice.isVisible()) || page.url() !== `${origin}${path}?tab=${key}`) {
      throw new Error(`${label} 被缓存的扩展页切回了默认标签，当前地址为 ${page.url()}`)
    }
  }

  await navigate('/capabilities?tab=api')
  await notice.waitFor()
  await page.waitForURL(`${origin}/capabilities`)
  await tabs.locator('.active').getByText('技能', { exact: true }).waitFor()
}
