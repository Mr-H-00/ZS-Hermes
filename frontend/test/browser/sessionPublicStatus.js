// 使用已登录环境与确定性 replay Agent：playwright-cli run-code --filename=frontend/test/browser/sessionPublicStatus.js
// 在页面注入 window.__YUXI_SESSION_STATUS_TEST__ = { questionAgent, approvalAgent, output }。
// 两个 Agent 分别启用 ask_user_question 和 DETERMINISTIC_LARGE_TOOL_RESULT；仅创建测试会话。
// prettier-ignore
async (page) => {
  const settings = await page.evaluate(() => window.__YUXI_SESSION_STATUS_TEST__)
  const check = (value, message) => { if (!value) throw new Error(message) }
  check(settings?.questionAgent && settings?.approvalAgent && settings?.output, '缺少确定性 Agent 配置')
  const origin = await page.evaluate(() => window.location.origin)
  const read = async (path, body) => page.evaluate(async ({ path, body }) => {
    const response = await fetch(`/api/v1/agents${path}`, {
      method: body ? 'POST' : 'GET',
      headers: {
        Authorization: `Bearer ${localStorage.getItem('user_token')}`,
        'Content-Type': 'application/json',
        ...(body ? { 'Idempotency-Key': crypto.randomUUID() } : {})
      },
      ...(body ? { body: JSON.stringify(body) } : {})
    })
    if (!response.ok) throw new Error(`Public API 返回 ${response.status}`)
    return response.json()
  }, { path, body })
  const sessions = []
  for (const kind of ['question', 'approval']) {
    const session = await read('/sessions', {
      agent_id: settings[`${kind}Agent`], title: `YUXI_TEST_SESSION_public_status_${kind}`
    })
    sessions.push(session.id)
    const loaded = page.waitForResponse(response =>
      response.url().endsWith(`/sessions/${session.id}/queue`) && response.ok()
    )
    await page.goto(`${origin}/agent/${session.id}`)
    await loaded
    await page.locator('.chat-loading').waitFor({ state: 'hidden' })
    const input = page.locator('[role="textbox"][contenteditable="true"]')
    await input.fill(`${settings.output} ${kind} browser` + (kind === 'question' ? ' DETERMINISTIC_ASK_USER' : ''))
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.waitFor({ timeout: 60000 })
    const waiting = await read(`/sessions/${session.id}`)
    check(waiting.status === 'requires_action', '人工等待没有公开为 requires_action')
    const turn = await read(`/sessions/${session.id}/turns/${waiting.yuxi.current_turn.id}`)
    check(turn.status === 'requires_action', 'Turn 与 Session 的等待状态不一致')
    const resumed = page.waitForRequest(request =>
      request.method() === 'POST' && request.url().endsWith(`/sessions/${session.id}/events`) &&
      request.postDataJSON()?.events?.some(event => event.type === 'yuxi.session.input.resume')
    )
    if (kind === 'question') {
      await dialog.locator('textarea').fill('第一题回答')
      await dialog.getByRole('button', { name: '下一步', exact: true }).click()
      await dialog.locator('textarea').fill('第二题回答')
      await dialog.getByRole('button', { name: '提交', exact: true }).click()
    } else {
      await dialog.getByRole('button', { name: '允许', exact: true }).click()
    }
    const request = await resumed
    const response = request.postDataJSON().events.find(event => event.type === 'yuxi.session.input.resume')
    check(response.turn_id === turn.id, '恢复请求指向了其他 Turn')
    check(response.waitpoint_id === turn.yuxi.waitpoint.id, '恢复请求指向了其他 waitpoint')
    check(turn.yuxi.waitpoint.run_id === turn.yuxi.current_run_id, '等待内容不属于当前 Run')
    await page.getByText(settings.output, { exact: true }).waitFor({ timeout: 60000 })
    let completed
    const deadline = Date.now() + 60000
    do {
      completed = await read(`/sessions/${session.id}/turns/${turn.id}`)
      if (['completed', 'failed', 'cancelled'].includes(completed.status)) break
      await page.waitForTimeout(200)
    } while (Date.now() < deadline)
    check(completed.status === 'completed', '恢复后没有得到持久最终结果')
    check(completed.yuxi.result_run_id !== turn.yuxi.current_run_id, '恢复没有创建新的执行段')
    check(completed.yuxi.output.some(item => item.yuxi.run_id === completed.yuxi.result_run_id), '最终输出不属于 result_run_id')
    await page.getByRole('button', { name: '停止回答', exact: true }).waitFor({ state: 'hidden', timeout: 60000 })
  }
  return { sessions, question: 'completed', approval: 'completed' }
}
