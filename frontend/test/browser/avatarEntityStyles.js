// 已登录的 Vite 页面执行：playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/avatarEntityStyles.js
// prettier-ignore
async page => {
  const check = (value, message) => { if (!value) throw Error(message) }
  const origin = await page.evaluate(() => location.origin)
  await page.goto(`${origin}/agent-manage`)
  await page.locator('.agent-card .fallback-avatar img').first().waitFor()
  const defaultAgentSeed = await page.evaluate(async () => {
    const { agentApi } = await import('/src/apis/agent_api.js')
    const { agents } = await agentApi.getAgents()
    return agents.find(agent => !String(agent.icon || '').trim())?.agent_id
  })
  check(defaultAgentSeed, '探针前置条件不满足：目录至少需要一个没有自定义图片的智能体')
  const actualAgent = await page.locator('.agent-card .fallback-avatar').evaluateAll(async (avatars, seed) => {
    const img = avatars.find(avatar => avatar.dataset.avatarSeed === seed)?.querySelector('img')
    if (!img) throw Error('目录缺少指定智能体的头像')
    await img.decode()
    return decodeURIComponent(img.src)
  }, defaultAgentSeed)
  check(actualAgent.includes('Lisa Wischofsky'), '智能体目录没有使用 Lorelei')
  const softBackgrounds = ['e1e9f2', 'e9e1f0', 'f5e5da', 'e3ecdf']
  check(softBackgrounds.some(color => actualAgent.includes(`fill="#${color}"`)), '智能体目录没有默认使用 soft 彩色背景')
  await page.evaluate(async () => {
    const main = await (await fetch('/src/app/main.js')).text()
    const { createApp, h, reactive } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
    const [{ default: Avatar }, { default: Tree }, { default: Tool }, { default: Panel }, { default: Resource }, { default: Token }, { useThemeStore }] = await Promise.all([
      import('/src/shared/ui/FallbackAvatar.vue'),
      import('/src/modules/session/ui/CooperationTree.vue'),
      import('/src/modules/session/ui/tools/renderers/CooperationTool.vue'),
      import('/src/modules/session/ui/workspace/AgentPanel.vue'),
      import('/src/modules/session/ui/MentionResourceAvatar.vue'),
      import('/src/modules/session/ui/MentionToken.vue'),
      import('/src/shared/model/theme.js')
    ])
    const host = document.createElement('div')
    host.id = 'avatar-entity-check'
    host.style = 'position:fixed;inset:0;z-index:99999;background:var(--gray-0);color:var(--gray-900);padding:24px;overflow:auto'
    document.body.append(host)
    const sessions = [
      { session_id: 'avatar-root', name: '主会话', path: '/main', turn_status: 'completed' },
      { session_id: 'avatar-research', name: '资料整理', path: '/main/research', turn_status: 'completed' }
    ]
    const state = reactive({ sessions, src: '', style: '' })
    const view = { sessions, tasks: new Map(), openSession() {} }
    const card = (title, content) => h('section', { style: 'margin-bottom:20px' }, [h('h3', title), content])
    const app = createApp({ render: () => h('div', [
      card('智能体 · Lorelei · soft', h('div', { style: 'display:flex;gap:16px' }, ['research', 'writer', 'assistant', 'coder'].map(seed => h(Avatar, { kind: 'agent', seed, size: 64 })))),
      card('用户 · Notionists', h('div', { id: 'user-palettes', style: 'display:flex;gap:24px;flex-wrap:wrap' }, ['default', 'studio', 'heritage', 'soft'].map(preset => h('div', [h('p', preset), h('div', { style: 'display:flex;gap:8px' }, ['one', 'two', 'three'].map(seed => h(Avatar, { kind: 'user', seed, preset, shape: 'square', size: 48 }))) ])))),
      card('协作会话 · Gaze', h('div', { style: 'height:145px' }, [h(Tree, { sessions: state.sessions, currentId: 'avatar-root' })])),
      card('对话中的协作工具', h(Tool, { toolCall: { name: 'create_session', args: {}, tool_call_result: { content: { session_id: 'avatar-research', name: '资料整理' } } }, cooperationView: view })),
      card('会话标签', h('div', { style: 'height:65px' }, [h(Panel, { sections: [{ key: 'session:avatar-research', type: 'session', title: '资料整理', avatarSeed: 'avatar-research' }], activeSectionKey: 'session:avatar-research' })])),
      card('会话提及与候选', h('div', { style: 'display:flex;gap:20px;align-items:center' }, [h(Token, { type: 'session', value: 'avatar-research', label: '资料整理' }), h(Resource, { item: { type: 'session', value: 'avatar-research', label: '资料整理' } })])),
      card('备用图标', h(Avatar, { kind: 'session', seed: 'failure', style: state.style, src: state.src, size: 32 }))
    ]) })
    const root = document.querySelector('#app').__vue_app__
    app.use(root.config.globalProperties.$pinia)
    Object.assign(app._context.components, root._context.components)
    app.mount(host)
    const theme = useThemeStore()
    window.__avatarEntity = { host, app, state, theme, originalTheme: theme.themeMode }
  })
  try {
    for (const dark of [false, true]) {
      await page.evaluate(value => window.__avatarEntity.theme.setTheme(value), dark)
      await page.locator('#avatar-entity-check img').evaluateAll(images => Promise.all(images.map(img => img.decode())))
      const facts = await page.locator('#avatar-entity-check .fallback-avatar').evaluateAll(avatars => avatars.map(avatar => {
        const img = avatar.querySelector('img')
        const canvas = document.createElement('canvas'); canvas.width = canvas.height = 256
        const ctx = canvas.getContext('2d'); ctx.drawImage(img, 0, 0, 256, 256)
        return { kind: avatar.dataset.avatarKind, seed: avatar.dataset.avatarSeed, src: img.src, alpha: ctx.getImageData(0, 0, 1, 1).data[3] }
      }))
      const users = facts.filter(item => item.kind === 'user')
      const agents = facts.filter(item => item.kind === 'agent')
      check(agents.length === 4 && agents.every(item => softBackgrounds.some(color => decodeURIComponent(item.src).includes(`fill="#${color}"`))), '智能体默认背景不是 soft 预设')
      check(users.length === 12 && users.every(item => item.alpha === 255 && decodeURIComponent(item.src).includes('Zoish')), '用户预设背景未渲染')
      const sessions = facts.filter(item => item.kind === 'session')
      check(sessions.length === 5 && sessions.every(item => item.alpha === 0 && decodeURIComponent(item.src).includes('Gaze')), `会话入口没有使用透明 Gaze：${JSON.stringify(sessions.map(({seed,alpha,src}) => ({seed,alpha,gaze:decodeURIComponent(src).includes('Gaze')})))}`)
      check(new Set(sessions.filter(item => item.seed === 'avatar-research').map(item => item.src)).size === 1, '同一会话在不同入口头像不一致')
      check(await page.locator('#avatar-entity-check .mention-ref-token .lucide-message-circle').count() === 1 && await page.locator('#avatar-entity-check .mention-ref-token img').count() === 0, '会话引用没有使用描边对话气泡')
      check(await page.locator('#avatar-entity-check .resource-avatar .lucide-message-circle').count() === 1, '会话候选没有使用对话气泡')
      check(await page.locator('#avatar-entity-check .session-row .fallback-avatar, #avatar-entity-check .event-target .fallback-avatar, #avatar-entity-check .section-tab .fallback-avatar').evaluateAll(nodes => nodes.length === 4 && nodes.every(node => node.getBoundingClientRect().width === 18)), '会话树、协作结果或标签的图标尺寸改变')
      if (dark) {
        check(JSON.stringify(users.map(item => item.src)) === await page.evaluate(() => window.__avatarEntity.lightUsers), '用户头像随暗色主题变化')
      } else await page.evaluate(value => { window.__avatarEntity.lightUsers = value }, JSON.stringify(users.map(item => item.src)))
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 1000 })
        check(await page.locator('#avatar-entity-check').evaluate(node => node.scrollWidth === node.clientWidth), '头像区域横向溢出')
        if (width !== 1024) await page.locator('#avatar-entity-check').screenshot({ path: `/tmp/yuxi-avatar-entities-${dark ? 'dark' : 'light'}-${width}.png` })
      }
    }
    await page.evaluate(() => { window.__avatarEntity.state.style = 'invalid' })
    await page.locator('#avatar-entity-check .lucide-workflow').waitFor()
    check(await page.locator('#avatar-entity-check [data-avatar-state=fallback]').getAttribute('aria-label') === '会话图标', '会话备用图标语义不正确')
    await page.evaluate(() => { window.__avatarEntity.state.style = ''; window.__avatarEntity.state.sessions = [] })
    await page.locator('#avatar-entity-check .empty-state').waitFor()
    await page.locator('#avatar-entity-check img').last().evaluate(img => img.decode())
    return { actualAgentDirectory: true, userPalettes: 4, sessionEntrypoints: 3, sessionReferenceBubble: true, gazeTransparent: true, userThemeStable: true, widths: [1440, 1024, 768], fallbackAndEmpty: true }
  } finally {
    await page.evaluate(() => {
      const probe = window.__avatarEntity
      probe.theme.setTheme(probe.originalTheme)
      probe.app.unmount(); probe.host.remove(); delete window.__avatarEntity
    })
  }
}
