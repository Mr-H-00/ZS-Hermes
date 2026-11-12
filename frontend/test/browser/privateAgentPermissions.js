// 以普通用户登录后运行：playwright-cli run-code --filename=frontend/test/browser/privateAgentPermissions.js
// prettier-ignore
async (page) => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const token = await page.evaluate(() => localStorage.getItem('user_token'))
  const headers = { Authorization: `Bearer ${token}` }
  const origin = await page.evaluate(() => location.origin)
  const api = async path => (await page.request.get(`${origin}${path}`, { headers })).json()
  const user = await api('/api/auth/me')
  check(user.role === 'user', '本测试需要真实普通用户身份')
  const backends = await api('/api/agent/backends')
  check(backends.backends.some(item => item.backend_id === 'ChatbotAgent' && item.can_create), '普通用户必须保留主 Agent 建设能力')
  check(!backends.backends.some(item => item.backend_id === 'SubAgentBackend'), '旧 SubAgent 后端不应暴露')
  await page.goto(`${origin}/agent-manage`)
  await page.getByRole('button', {name:'新增智能体',exact:true}).click()
  const modal = page.getByRole('dialog')
  check(await modal.getByText('发布为共享智能体').count() === 0, '普通用户不能发布')
  check(await modal.getByText('共享设置', {exact:true}).count() === 0, '私有创建不能配置共享范围')
  const name = `私有权限验证 ${Date.now()}`
  await modal.getByRole('textbox', {name:'智能体名称',exact:true}).fill(name)
  const received = page.waitForResponse(response => response.url().split('?')[0] === `${origin}/api/agent` && response.request().method() === 'POST')
  await modal.getByRole('button', {name:'创 建',exact:true}).click()
  const response = await received
  check(response.ok(), '创建请求必须成功')
  const agent = (await response.json()).agent
  try {
    const persisted = (await api(`/api/agent/${agent.slug}`)).agent
    check(persisted.visibility === 'private' && persisted.can_manage && persisted.can_run, '回读必须是自己的可管理私有 Agent')
    check(!Object.hasOwn(persisted, 'can_publish') && !persisted.can_share && !persisted.can_transfer, '普通用户不能获得共享写能力')
    await modal.waitFor({state:'hidden'})
    const card = page.locator('.info-card').filter({hasText:name})
    await card.waitFor()
    check(await card.getByText('私有', {exact:true}).count() === 1, '页面必须明确标记私有状态')
    await card.click()
    await modal.waitFor()
    check(await modal.getByText('发布为共享智能体').count() === 0, '编辑时仍不能发布')
    check(await modal.getByText('最大执行步数', {exact:true}).count() === 0, '私有所有权不能解锁高级字段')
    const editedName = `${name} 已编辑`
    await modal.getByRole('textbox', {name:'智能体名称',exact:true}).fill(editedName)
    const saved = page.waitForResponse(response => response.url().split('?')[0] === `${origin}/api/agent/${agent.slug}` && response.request().method() === 'PUT')
    await modal.getByRole('button', {name:'保存（有修改）',exact:true}).click()
    check((await saved).ok(), '真实编辑必须保存成功')
    check((await api(`/api/agent/${agent.slug}`)).agent.name === editedName, '编辑结果必须重新回读')
    await modal.waitFor({state:'hidden'})
    await page.setViewportSize({width:1440,height:1000})
    await page.waitForTimeout(500)
    await page.screenshot({path:'/tmp/yuxi-private-agent-light.png',fullPage:true})
    await page.evaluate(() => localStorage.setItem('theme','dark'))
    await page.reload()
    await page.locator('.info-card').filter({hasText:editedName}).waitFor()
    check(await page.evaluate(() => document.documentElement.classList.contains('dark')), '主题必须由真实主题初始化')
    await page.screenshot({path:'/tmp/yuxi-private-agent-dark.png',fullPage:true})
    await page.setViewportSize({width:390,height:844})
    await page.screenshot({path:'/tmp/yuxi-private-agent-mobile.png',fullPage:true})
    await page.setViewportSize({width:1440,height:1000})
    await page.locator('.info-card').filter({hasText:editedName}).getByRole('button',{name:'更多操作'}).click()
    await page.getByRole('menuitem',{name:'删除智能体'}).click()
    const deletion = page.waitForResponse(response => response.url().split('?')[0] === `${origin}/api/agent/${agent.slug}` && response.request().method() === 'DELETE')
    await page.getByRole('button',{name:'删 除',exact:true}).click()
    check((await deletion).ok(), '真实删除操作必须成功')
    await page.locator('.info-card').filter({hasText:editedName}).waitFor({state:'hidden'})
    check((await page.request.get(`${origin}/api/agent/${agent.slug}`, {headers})).status() === 404, '删除必须回读为不可访问')
  } finally {
    const deleted = await page.request.delete(`${origin}/api/agent/${agent.slug}`, {headers})
    check(deleted.ok() || deleted.status() === 404, '浏览器测试必须清理自己的定义')
  }
}
