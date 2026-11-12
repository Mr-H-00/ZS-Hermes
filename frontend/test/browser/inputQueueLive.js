// 已登录隔离环境中设置 window.__YUXI_INPUT_QUEUE_TEST__ = { threadId }。
// 该 Session 的 A 请求由确定性模型阻塞；看到 queue-live-pending.png 后从测试端释放模型。
// 执行：playwright-cli -s=input-queue-live run-code --filename=frontend/test/browser/inputQueueLive.js
// prettier-ignore
async (page) => {
  const settings = await page.evaluate(() => window.__YUXI_INPUT_QUEUE_TEST__)
  const check = (value, message) => { if (!value) throw new Error(message) }
  check(settings?.threadId, '缺少隔离测试 Session')
  const origin = await page.evaluate(() => location.origin)
  const read = async (suffix) => page.evaluate(async (path) => {
    const response = await fetch(path, { headers: { Authorization: `Bearer ${localStorage.getItem('user_token')}` } })
    if (!response.ok) throw new Error(`Public API 返回 ${response.status}`)
    return response.json()
  }, `/api/v1/agents/sessions/${settings.threadId}${suffix}`)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${origin}/agent/${settings.threadId}`)
  const editor = page.locator('[role="textbox"][contenteditable="true"]')
  const rows = page.locator('.queued-request-row')
  const b = '浏览器待处理消息 B'
  const c = '浏览器待处理消息 C'
  for (const text of [b, c]) {
    await editor.fill(text)
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await rows.filter({ hasText: text }).waitFor()
  }
  let queue = await read('/queue')
  const input = queue.inputs.find(item => item.content === b)
  check(input?.kind === 'follow_up', '普通发送没有进入 FIFO')
  check((await read(`/inputs/${input.input_id}`)).items.length === 0, '接收即生成正式消息')
  await rows.filter({ hasText: b }).getByRole('button', { name: '转为引导', exact: true }).click()
  await rows.filter({ hasText: b }).getByText('等待引导').waitFor()
  await rows.filter({ hasText: c }).getByRole('button', { name: `取消排队输入：${c}`, exact: true }).click()
  await rows.filter({ hasText: c }).waitFor({ state: 'detached' })
  await page.reload()
  await rows.filter({ hasText: b }).getByText('等待引导').waitFor()
  queue = await read('/queue')
  check(queue.inputs.length === 1 && queue.inputs[0].input_id === input.input_id && queue.inputs[0].kind === 'steer', '刷新改变队列身份')
  check(await page.locator('.history-display-item').getByText(b, { exact: true }).count() === 0, 'pending 进入历史')
  await page.screenshot({ path: '/tmp/yuxi-input-queue-live-pending.png' })
  const projected = page.locator('.history-display-item').filter({ hasText: b })
  await projected.waitFor({ timeout: 120000 })
  const consumed = await read(`/inputs/${input.input_id}`)
  check(consumed.status === 'consumed' && consumed.items.length === 1, '消费没有生成真实投影')
  check(consumed.items[0].yuxi.input_id === input.input_id, '正式消息失去原 Input 归属')
  await page.reload()
  await projected.waitFor()
  check(await projected.count() === 1 && await rows.count() === 0, '消费刷新后重复或仍在队列')
  check(await page.locator('.history-display-item').getByText(c, { exact: true }).count() === 0, '取消输入进入历史')
  await page.screenshot({ path: '/tmp/yuxi-input-queue-live-consumed.png' })
  return { liveBackend: true, sent: 2, promoted: 1, cancelled: 1, consumedMessageId: consumed.items[0].id, refreshedWithoutDuplicate: true }
}
