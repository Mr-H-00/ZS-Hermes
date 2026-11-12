// 已登录页面执行：playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/frontendArchitecture.js
// prettier-ignore
async (page) => {
  const origin = await page.evaluate(() => location.origin)
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto(`${origin}/agent`)
  await page.locator('[contenteditable="true"]').waitFor({ state: 'visible' })
  const agentId = await page.evaluate(async () => {
    const { useAgentStore } = await import('/src/modules/agents/model/agent.js')
    return useAgentStore().selectedAgentId
  })
  if (!agentId) throw new Error('Agent directory is not ready')
  const editorRequestsBefore = await page.evaluate(() => performance.getEntriesByType('resource')
    .filter((entry) => entry.name.includes('/AgentEditModal.vue')).length)
  if (editorRequestsBefore) throw new Error('Agent editor loaded before being opened')
  await page.goto(`${origin}/agent?agent_id=${encodeURIComponent(agentId)}`)
  await page.waitForFunction(() => !new URL(location.href).searchParams.has('agent_id'))
  await page.locator('[contenteditable="true"]').waitFor({ state: 'visible' })
  await page.locator('.agent-view [data-session-agent-picker] .config-dropdown-trigger').first().click()
  await page.getByRole('button', { name: '编辑智能体', exact: true }).click()
  await page.locator('.agent-edit-modal').waitFor({ state: 'visible' })
  if (!(await page.locator('.agent-edit-modal input').count())) throw new Error('Agent editor did not mount its form')
  await page.locator('.ant-modal-wrap').filter({ visible: true }).click({ position: { x: 5, y: 5 } })
  await page.locator('.agent-edit-modal').waitFor({ state: 'hidden' })
  await page.locator('.agent-view [data-session-agent-picker] .config-dropdown-trigger').first().click()
  await page.getByRole('button', { name: '新建智能体', exact: true }).click()
  await page.locator('.agent-edit-modal').waitFor({ state: 'visible' })
  await page.locator('.agent-edit-modal').getByRole('button', { name: /取\s*消/ }).click()
  await page.locator('.agent-edit-modal').waitFor({ state: 'hidden' })
  await page.setViewportSize({ width: 1440, height: 900 })
  const desktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
  await page.setViewportSize({ width: 390, height: 844 })
  const mobile = await page.evaluate(() => ({
    width: innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
    minimumWidth: parseFloat(getComputedStyle(document.querySelector('.app-layout')).minWidth)
  }))
  const mobileOverflow = mobile.scrollWidth > mobile.width
  await page.setViewportSize({ width: 1440, height: 900 })
  if (desktopOverflow || mobile.scrollWidth > Math.max(mobile.width, mobile.minimumWidth))
    throw new Error('Viewport exceeds the application minimum width')
  if (errors.length) throw new Error(`Browser errors: ${errors.join('; ')}`)
  return { agentQueryConsumed: true, editorLazyLoaded: true, editAndCreateFormsMounted: true, desktopOverflow, mobileOverflow, mobileMinimumWidth: mobile.minimumWidth }
}
