// 在独立浏览器中运行，API 使用合成数据且拒绝写入。
async page => {
  const requests = []
  const departments = Array.from({length: 12}, (_, i) => ({id: i + 1, name: i === 0 ? 'agent-real-0123456789abcdef0123456789abcdef' : `测试部门${i + 1}`, user_count: 1}))
  await page.route('**/api/**', async route => {
    const req = route.request(); const full = req.url(); const path = '/' + full.split('/').slice(3).join('/').split('?')[0]; const query = full.split('?')[1] || ''
    if (req.method() !== 'GET') throw new Error('Unexpected write')
    let body = {}
    if (path === '/api/auth/users/page') {
      requests.push(query)
      const empty = query.includes('roles=admin')
      body = {items: empty ? [] : [{id: 1, uid: 'fixture', username: '测试用户', role: 'user', user_kind: 'human', department_id: 1, department_name: departments[0].name}], total: empty ? 0 : 1}
    } else if (path === '/api/departments') body = departments
    else if (path === '/api/system/config/options') body = {options: []}
    await route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(body)})
  })
  await page.goto(`${page.url().split('/').slice(0, 3).join('/')}/test/browser/settingsStyles.html`)
  await page.waitForFunction(() => window.settingsStyles)
  await page.evaluate(() => window.settingsStyles.mount('standalone', 'user'))
  const role = page.getByRole('button', {name: /筛选角色/})
  await role.waitFor()
  await page.locator('.ant-spin-spinning').waitFor({state:'hidden'})
  await role.click()
  const panel = page.locator('.yuxi-header-filter-panel:visible')
  await panel.getByText('管理员', {exact: true}).click()
  await panel.getByText('普通用户', {exact: true}).click()
  if (!await panel.isVisible() || requests.some(x => x.includes('roles='))) throw new Error('勾选时关闭或提前应用')
  await page.screenshot({path: '/tmp/yuxi-role-filter.png', animations: 'disabled'})
  await panel.getByRole('button', {name: /取\s*消/}).click()
  await role.click()
  if (await panel.getByRole('checkbox', {name:'管理员', exact:true}).isChecked()) throw new Error('取消未丢弃草稿')
  await panel.getByText('管理员', {exact:true}).click()
  const roleApplied = page.waitForResponse(response => response.url().includes('roles=admin'))
  await panel.getByRole('button', {name:/确\s*认/}).click()
  await roleApplied
  if (!await role.isVisible()) throw new Error('空结果丢失筛选入口')
  await role.click()
  if (!await panel.getByRole('checkbox', {name:'管理员', exact:true}).isChecked()) throw new Error('确认未保存选择')
  await panel.getByRole('button', {name:'清空'}).click()
  const roleCleared = page.waitForResponse(response => response.url().includes('/api/auth/users/page') && !response.url().includes('roles='))
  await panel.getByRole('button', {name:/确\s*认/}).click()
  await roleCleared
  await page.getByRole('button', {name:/筛选部门/}).click()
  await panel.getByText(departments[0].name, {exact:true}).click()
  await panel.getByText('测试部门2', {exact:true}).click()
  if (!await panel.isVisible()) throw new Error('部门勾选后关闭')
  const bounds = await panel.evaluate(el => { const list = el.querySelector('.header-filter-options'); return {width:el.getBoundingClientRect().width, scrollWidth:list.scrollWidth, clientWidth:list.clientWidth, scrollHeight:list.scrollHeight, clientHeight:list.clientHeight} })
  if (bounds.width > 300 || bounds.scrollWidth > bounds.clientWidth || bounds.scrollHeight <= bounds.clientHeight) throw new Error(JSON.stringify(bounds))
  await page.screenshot({path:'/tmp/yuxi-department-filter.png', animations: 'disabled'})
  await page.setViewportSize({width: 768, height: 768})
  await page.evaluate(() => window.settingsStyles.setTheme('dark'))
  await page.screenshot({path:'/tmp/yuxi-department-filter-dark.png', animations: 'disabled'})
  await page.getByText('用户管理', {exact:true}).click()
  await panel.waitFor({state:'hidden'})
  await page.getByRole('button', {name:/筛选部门/}).click()
  await panel.waitFor({state:'visible'})
  if (await panel.getByRole('checkbox', {name: departments[0].name}).isChecked()) throw new Error('点击外部未丢弃部门草稿')
  await panel.getByText(departments[0].name, {exact:true}).click()
  await panel.getByText('测试部门2', {exact:true}).click()
  const departmentApplied = page.waitForResponse(response => response.url().includes('department_ids=1%2C2'))
  await panel.getByRole('button', {name:/确\s*认/}).click()
  await page.waitForFunction(() => document.querySelector('[aria-label="筛选部门（已选 2 项）"]'))
  await departmentApplied
  if (!requests.some(query => query.includes('department_ids=1%2C2'))) throw new Error('确认未提交部门筛选')
  await page.getByRole('button', {name:/筛选部门/}).click()
  await panel.getByRole('button', {name:'清空'}).click()
  const search = panel.getByPlaceholder('搜索部门')
  await search.fill('测试部门3')
  await panel.getByRole('checkbox', {name:'测试部门3', exact:true}).check()
  await search.fill('测试部门4')
  await panel.getByRole('checkbox', {name:'测试部门4', exact:true}).check()
  await search.fill('')
  for (const name of ['测试部门3', '测试部门4']) {
    if (!await panel.getByRole('checkbox', {name, exact:true}).isChecked()) throw new Error('搜索切换丢失已选部门')
  }
  const applied = page.waitForResponse(response => response.url().includes('department_ids=3%2C4'))
  await panel.getByRole('button', {name:/确\s*认/}).click()
  await applied
  return {requests, bounds}
}
