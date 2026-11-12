// 已登录开发环境执行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/sessionStartLayout.js
// 暂缓真实发送请求，验证路由接管前的首条消息布局；仅创建测试会话。
// prettier-ignore
async (page) => {
  await page.goto('http://localhost:5173/agent')
  await page.setViewportSize({ width: 1440, height: 900 })
  const dock = page.locator('.agent-view .bottom')
  await page.locator('.agent-view .chat-greeting-input').waitFor({ state: 'visible' })
  if (!(await dock.evaluate(el => el.classList.contains('start-screen'))))
    throw new Error('新会话没有欢迎布局')
  await page.evaluate(async () => {
    const { agentApi } = await import('/src/apis/agent_api.js')
    const original = agentApi.sendThreadMessage
    window.__layoutSendStarted = false
    window.__layoutThreadId = null
    window.__restoreLayoutSend = () => { agentApi.sendThreadMessage = original }
    agentApi.sendThreadMessage = async (...args) => {
      window.__layoutThreadId = args[0]
      window.__layoutSendStarted = true
      const send = await new Promise(resolve => { window.__releaseLayoutSend = resolve })
      if (!send) throw new Error('布局验证失败，取消发送')
      return original(...args)
    }
  })
  try {
    const editor = page.locator('.agent-view [contenteditable="true"]')
    await editor.fill('只回复：布局测试完成')
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await page.waitForFunction(() => window.__layoutSendStarted)
    await page.locator('.agent-view .group-box').filter({ hasText: '只回复：布局测试完成' }).waitFor()
    if ((await page.evaluate(() => location.pathname)) !== '/agent')
      throw new Error('请求暂缓期间路由提前接管')
    if (await page.locator('.agent-view .chat-greeting-input').count())
      throw new Error('消息出现后仍显示欢迎文案')
    const layout = await dock.evaluate(el => ({
      centered: el.classList.contains('start-screen'),
      bottom: el.getBoundingClientRect().bottom,
      viewportHeight: innerHeight
    }))
    if (layout.centered || layout.bottom < layout.viewportHeight - 80)
      throw new Error('消息发送期间输入框仍居中')
    await page.screenshot({ path: '/tmp/yuxi-session-start-layout.png' })
    await page.evaluate(() => window.__releaseLayoutSend?.(true))
    await page.waitForURL(/\/agent\/[^/?]+/, { timeout: 30000 })
    await page.locator('.agent-view [contenteditable="true"]').waitFor()
    if (await page.locator('.agent-view .start-screen, .agent-view .chat-greeting-input').count())
      throw new Error('会话路由重新显示欢迎布局')
    await page.screenshot({ path: '/tmp/yuxi-session-thread-layout.png' })
    await page.evaluate(() => document.documentElement.classList.add('dark'))
    await page.setViewportSize({ width: 390, height: 844 })
    if (await page.locator('.agent-view .start-screen, .agent-view .chat-greeting-input').count())
      throw new Error('窄屏暗色会话重新显示欢迎布局')
    await page.screenshot({ path: '/tmp/yuxi-session-thread-dark-mobile.png' })
    return { pendingSendLayout: layout, threadRouteLayout: true, darkMobileLayout: true }
  } finally {
    await page.evaluate(() => window.__releaseLayoutSend?.(false))
    const threadId = await page.evaluate(() => window.__layoutThreadId)
    await page.evaluate(() => { window.__restoreLayoutSend?.(); delete window.__restoreLayoutSend; delete window.__releaseLayoutSend; delete window.__layoutSendStarted; delete window.__layoutThreadId })
    await page.evaluate(() => document.documentElement.classList.remove('dark'))
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('http://localhost:5173/agent')
    if (threadId) {
      await page.evaluate(async id => {
        const { agentApi } = await import('/src/apis/agent_api.js')
        await agentApi.controlSessionTree(id, true, crypto.randomUUID())
      }, threadId)
      await page.evaluate(async id => {
        const { agentApi, threadApi } = await import('/src/apis/agent_api.js')
        for (let attempt = 0; attempt < 120; attempt++) {
          try {
            await threadApi.archiveThread(id)
            const thread = await agentApi.getPublicThread(id)
            if (thread.status === 'archived') return
          } catch (error) {
            // Run 的执行方确认退出后，归档边界才允许清理。
            if (error.status !== 409) throw error
          }
          await new Promise(resolve => setTimeout(resolve, 250))
        }
        throw new Error('测试会话未归档')
      }, threadId)
    }
  }
}
