// 已登录的独立测试环境执行：playwright-cli -s=<session> run-code --filename=frontend/test/browser/sessionMentions.js
// prettier-ignore
async page => {
  const check = (condition, message) => { if (!condition) throw new Error(message) }
  const fixture = await page.evaluate(async () => {
    const ids = []
    const titles = ['发布方案 · 灰度验证', '发布方案 · 灰度验证', '发布方案 · 跨团队设计评审与完整验收记录的长标题展示', '对话引用演示']
    for (const title of titles) {
      const response = await fetch('/api/v1/agents/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('user_token')}`, 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ agent_id: 'default-chatbot', title })
      })
      if (response.status !== 201) throw new Error(`创建测试对话失败：${response.status}`)
      ids.push((await response.json()).id)
    }
    return { ids, titles }
  })
  const editor = page.locator('.mention-editor[contenteditable="true"]:visible')
  const rows = page.locator('.session-mention-item')
  const screenshots = []
  let releaseSearch = () => {}
  try {
    await page.goto(`${page.url().split('/').slice(0, 3).join('/')}/agent/${fixture.ids[3]}`)
    await page.locator('main').getByText('对话引用演示', { exact: true }).waitFor()
    await editor.waitFor()
    // 控制真实接口返回时序，证明后来到达的对话不会改变已选 Skill。
    let searchStarted
    const started = new Promise(resolve => { searchStarted = resolve })
    const gate = new Promise(resolve => { releaseSearch = resolve })
    await page.route('**/api/mention/sessions?*', async route => {
      const response = await route.fetch()
      searchStarted()
      await gate
      await route.fulfill({ response })
    })
    await editor.fill('@')
    await started
    await page.getByText('正在查找对话…').waitFor()
    await editor.press('ArrowDown')
    await editor.press('ArrowDown')
    const selectedResource = await page.locator('.mention-item.active').innerText()
    check((await page.locator('.mention-item.active').getAttribute('class')).includes('resource-item'), '延迟场景没有选中已有资源')
    releaseSearch()
    await rows.nth(2).waitFor()
    check(await page.locator('.mention-item.active').innerText() === selectedResource, '异步对话结果改变了键盘选中的资源')
    await editor.press('Enter')
    check((await editor.locator('[data-mention-raw]').getAttribute('data-mention-raw')).startsWith('@skill:'), '延迟场景插入了错误类型的引用')
    await page.unroute('**/api/mention/sessions?*')
    await editor.fill('@发布')
    await rows.nth(2).waitFor()
    check(await rows.count() === 3, '标题候选没有完整显示')
    check(!(await page.locator('.session-mention-group').innerText()).includes(fixture.ids[0]), '候选泄露内部 ID')
    await editor.press('ArrowDown')
    await editor.press('Enter')
    const token = editor.locator('[data-mention-raw]')
    check(await token.count() === 1, '键盘未插入引用')
    check((await token.getAttribute('data-mention-raw')).includes(fixture.ids[1]), '键盘没有选中第二个稳定 ID')
    check(await token.innerText() === fixture.titles[1], '输入框没有显示对话标题')
    check(await token.getAttribute('title') === fixture.titles[1], '引用提示显示了内部 ID')
    await editor.press('Backspace')
    await editor.press('Backspace')
    check(await token.count() === 0, 'Backspace 没有整体删除引用')

    for (const dark of [false, true]) {
      await page.evaluate(async value => {
        const { useThemeStore } = await import('/src/shared/model/theme.js')
        useThemeStore().setTheme(value ? 'dark' : 'light')
      }, dark)
      for (const width of [1440, 768]) {
        await page.setViewportSize({ width, height: 900 })
        await editor.fill('@发布')
        await rows.nth(2).waitFor()
        check(await page.locator('.mention-popup').evaluate(node => node.scrollWidth <= node.clientWidth), '引用候选横向溢出')
        const path = `/tmp/yuxi-session-mentions-${dark ? 'dark' : 'light'}-${width}.png`
        await page.screenshot({ path })
        screenshots.push(path)
      }
    }
    await editor.fill('@没有这个标题')
    await page.getByText('未找到标题匹配的对话，试试其他关键词').waitFor()
    await page.route('**/api/mention/sessions?*', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"测试服务不可用"}' }))
    await editor.fill('@失败')
    await page.getByText('对话加载失败', { exact: false }).waitFor()
    await page.unroute('**/api/mention/sessions?*')
    await page.getByRole('button', { name: '重试', exact: true }).click()
    await page.getByText('未找到标题匹配的对话，试试其他关键词').waitFor()
    await editor.fill('@发布')
    await rows.nth(2).waitFor()
    await rows.first().click()
    const raw = await token.getAttribute('data-mention-raw')
    // 使用真实历史组件模拟刷新后的持久文本渲染，不依赖候选缓存。
    const label = await page.evaluate(async raw => {
      const main = await (await fetch('/src/app/main.js')).text()
      const { createApp, h } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
      const { default: Renderer } = await import('/src/modules/session/ui/MentionTextRenderer.vue')
      const host = document.createElement('div')
      document.body.append(host)
      const app = createApp({ render: () => h(Renderer, { content: raw }) })
      app.mount(host)
      const label = host.querySelector('.mention-ref-label')?.textContent
      const title = host.querySelector('.mention-ref-token')?.getAttribute('title')
      app.unmount()
      host.remove()
      return { label, title }
    }, raw)
    check(label.label === fixture.titles[2] && label.title === fixture.titles[2], '没有候选缓存时标题或提示未持久展示')
    return { passed: ['真实标题搜索', '同名稳定 ID', '键盘选择', '整体删除', '异步结果保持选择', '加载反馈', '长标题与响应式', '空状态', '失败重试', '独立历史渲染'], screenshots }
  } finally {
    releaseSearch()
    await page.unroute('**/api/mention/sessions?*')
    await page.evaluate(async ids => {
      document.documentElement.classList.remove('dark')
      for (const id of ids) {
        const response = await fetch(`/api/v1/agents/sessions/${id}/archive`, { method: 'POST', headers: { Authorization: `Bearer ${localStorage.getItem('user_token')}` } })
        if (!response.ok) throw new Error(`测试对话清理失败：${response.status}`)
      }
    }, fixture.ids)
  }
}
