// 使用真实页面与确定性 HTTP replay 验证队列、刷新、图片引导及响应式布局。
// 执行：playwright-cli -s=input-queue run-code --filename=frontend/test/browser/inputQueueProjection.js
// prettier-ignore
async (page) => {
  const origin = await page.evaluate(() => location.origin)
  const threadId = '00000000-0000-4000-8000-000000000011'
  const agentId = 'queue-agent'
  const png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=='
  const check = (value, message) => { if (!value) throw new Error(message) }
  const thread = { id: threadId, object: 'agent.session', status: 'in_progress', agent: { id: agentId, model: 'test:chat' },
    created_at: 1791580000, metadata: {}, yuxi: { title: '输入队列验证', current_turn: { id: 'turn-A', status: 'in_progress' }, context: {}, project_id: 'queue-project', config: {}, is_pinned: false } }
  const agent = { agent_id: agentId, name: '队列验证助手', config_json: { context: { model: 'test:chat' } }, configurable_items: {}, metadata: {} }
  let inputs = ['B', 'C', 'D'].map((id) => ({ input_id: id, kind: 'follow_up', status: 'pending', content: `待处理消息 ${id}`, attachment_status: id === 'D' ? 'preparing' : 'ready' }))
  const commands = [], snapshots = []
  const history = [{ id: 'input_1', object: 'agent.session.item', type: 'message', role: 'user', status: 'completed', turn_id: 'turn-A',
    content: [{ type: 'input_text', text: '当前正在执行的消息 A' }], yuxi: { message_id: '1', run_id: 'run-A', created_at: '2026-10-10T01:00:00Z' } }]
  const runs = [{ id: 'run-A', turn_id: 'turn-A', status: 'running', input_ids: ['A'], artifacts: [] }]
  await page.addInitScript(() => localStorage.setItem('user_token', 'queue-replay-token'))
  await page.route('**/api/**', async (route) => {
    const path = route.request().url().replace(origin, '').split('?')[0]
    let json, status = 200
    if (path === '/api/auth/me') json = { id: 1, username: '队列验证', role: 'user' }
    else if (path === '/api/agent') json = { agents: [agent] }
    else if (path === `/api/agent/${agentId}`) json = agent
    else if (path === '/api/agent/images') json = { image_url: png, thumbnail_url: png, width: 1, height: 1, format: 'png', mime_type: 'image/png', size_bytes: 70 }
    else if (path.endsWith('/events') && route.request().method() === 'POST') {
      const event = route.request().postDataJSON().events[0]
      commands.push(event)
      if (event.type === 'yuxi.session.input.promote') inputs.find(item => item.input_id === event.input_id).kind = 'steer'
      else if (event.type === 'yuxi.session.input.cancel_input') inputs = inputs.filter(item => item.input_id !== event.input_id)
      else if (event.type === 'agent.session.input.message') inputs.push({ input_id: 'image-input', kind: event.yuxi.mode, status: 'pending', content: '图片引导', attachment_status: 'ready' })
      status = 202
      json = { type: 'yuxi.session.event.accepted', event_id: 'receipt', input_id: event.input_id || 'image-input', turn_id: null, run_id: null, status: 'accepted' }
    } else if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' })
    else if (path.includes('/inputs/')) json = { status: 'pending', messages: [], items: [] }
    else if (path.endsWith('/queue')) { snapshots.push(inputs.map(item => item.input_id)); json = { inputs: [...inputs].sort((a,b) => Number(b.kind === 'steer') - Number(a.kind === 'steer')), status: 'running', queue_paused: false } }
    else if (path.endsWith('/turns/turn-A')) json = { id: 'turn-A', status: 'in_progress', yuxi: { current_run_id: 'run-A', runs: [], waitpoint: null } }
    else if (path.endsWith('/items')) json = { data: history, has_more: false, last_id: history.at(-1).id, yuxi: { runs } }
    else if (path === '/api/v1/agents/sessions') json = { data: [thread], has_more: false }
    else if (path.endsWith(`/${threadId}`) || path.endsWith('/viewed')) json = thread
    else if (path.endsWith('/state')) json = { agent_state: {}, interrupt: null }
    else if (path.endsWith('/cooperation')) json = { sessions: [], members: [], edges: [] }
    else if (path.endsWith('/attachments')) json = { files: [], attachments: [] }
    else if (path === '/api/projects') json = []
    else if (path.includes('/databases')) json = { databases: [] }
    else if (path.includes('/models')) json = { providers: [], models: [], data: [] }
    else json = { data: [], files: [], skills: [], projects: [], providers: [], config: {}, site_name: '语析', first_run: false }
    await route.fulfill({ status, json })
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${origin}/agent/${threadId}`)
  const rows = page.locator('.queued-request-row')
  await rows.filter({ hasText: '待处理消息 B' }).waitFor()
  check(await rows.count() === 3, '独立队列条目丢失')
  check(await rows.filter({ hasText: '待处理消息 D' }).getByRole('button', { name: '转为引导', exact: true }).count() === 0, '未就绪输入允许转换')
  await rows.filter({ hasText: '待处理消息 B' }).getByRole('button', { name: '转为引导', exact: true }).click()
  await rows.filter({ hasText: '待处理消息 B' }).getByText('等待引导').waitFor()
  await page.screenshot({ path: '/tmp/yuxi-input-queue-desktop.png' })
  await rows.filter({ hasText: '待处理消息 C' }).getByRole('button', { name: '取消排队输入：待处理消息 C', exact: true }).click()
  await rows.filter({ hasText: '待处理消息 C' }).waitFor({ state: 'detached' })
  await page.reload()
  await rows.filter({ hasText: '待处理消息 B' }).getByText('等待引导').waitFor()
  check(await page.locator('.history-display-item').getByText('待处理消息 B', { exact: true }).count() === 0, 'pending 输入进入正式历史')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForFunction(() => document.querySelector('.app-layout.sidebar-collapsed')?.firstElementChild?.getBoundingClientRect().width <= 60)
  await page.waitForTimeout(250)
  await page.screenshot({ path: '/tmp/yuxi-input-queue-mobile.png' })
  const boxes = await rows.evaluateAll(nodes => nodes.map(node => {
    const content = node.querySelector('.queued-request-content').getBoundingClientRect()
    const notice = node.querySelector('.queued-request-status').getBoundingClientRect()
    const actions = node.querySelector('.queued-request-actions').getBoundingClientRect()
    return { contentWidth: content.width, contentRight: content.right, noticeLeft: notice.left, noticeRight: notice.right, actionsLeft: actions.left, actionsRight: actions.right, width: innerWidth }
  }))
  check(boxes.every(box => box.contentWidth > 50 && box.contentRight <= box.noticeLeft && box.noticeRight <= box.actionsLeft && box.actionsRight <= box.width), '队列文字或按钮重叠溢出')
  await page.setViewportSize({ width: 1440, height: 900 })
  const editor = page.locator('[contenteditable="true"]').first()
  await editor.fill('请参考这张图片')
  await editor.evaluate((el, data) => {
    const bytes = Uint8Array.from(atob(data.split(',')[1]), char => char.charCodeAt(0))
    const transfer = new DataTransfer()
    transfer.items.add(new File([bytes], 'guide.png', { type: 'image/png' }))
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true }))
  }, png)
  await page.locator('.image-preview-wrapper').waitFor()
  await page.getByRole('button', { name: '引导', exact: true }).click()
  await rows.filter({ hasText: '图片引导' }).waitFor()
  const sent = commands.find(command => command.type === 'agent.session.input.message')
  check(sent?.yuxi.mode === 'steer' && sent.input[0].content.some(part => part.type === 'input_image' && part.image_url === png), '直接引导遗漏图片')
  check(snapshots.length >= 4, '操作后没有回读队列')
  await page.screenshot({ path: '/tmp/yuxi-input-queue-image-steer.png' })
  inputs = inputs.filter(item => item.input_id !== 'B')
  history.push({ ...history[0], id: 'input_2', content: [{ type: 'input_text', text: '待处理消息 B' }],
    yuxi: { message_id: '2', run_id: 'run-B', input_id: 'B', input_position: 0, created_at: '2026-10-10T01:01:00Z' } })
  runs[0].status = 'yielded'
  runs.push({ id: 'run-B', turn_id: 'turn-A', status: 'running', input_ids: ['B'], artifacts: [] })
  for (let reload = 0; reload < 2; reload += 1) {
    await page.reload()
    const projected = page.locator('.history-display-item').filter({ hasText: '待处理消息 B' })
    await projected.waitFor()
    check(await projected.count() === 1, '消费后的真实消息重复进入历史')
    check(await rows.filter({ hasText: '待处理消息 B' }).count() === 0, '已消费输入仍在队列')
  }
  return { rows: inputs.length, commands: commands.map(item => item.type), refresh: true, pendingOutsideHistory: true, consumedOnce: true, mobileNoOverlap: true, imageSteer: true }
}
