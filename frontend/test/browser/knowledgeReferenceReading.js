// 在真实 Vite 页面执行；HTTP 夹具只验证 UI 协议，权限另由 integration 验证。
async page => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const viewport = page.viewportSize()
  const originalTheme = await page.evaluate(() => localStorage.getItem('theme'))
  const requests = []
  let mode = 'content'
  let releaseInitialRead
  let holdInitialRead = true
  const handler = async route => {
    const request = route.request()
    const path = request.url().replace(/^https?:\/\/[^/]+/, '').split('?')[0]
    requests.push(path)
    if (path !== '/api/v1/knowledge/tools/open_kb_document') return route.fulfill({ status: 403, json: { detail: '管理操作禁止' } })
    if (mode === 'denied') return route.fulfill({ status: 404, json: { detail: '无权读取文档' } })
    if (holdInitialRead) {
      holdInitialRead = false
      await new Promise(resolve => { releaseInitialRead = resolve })
    }
    const { offset } = request.postDataJSON()
    const count = mode === 'empty' ? 0 : 8
    return route.fulfill({ json: {
      kb_id: 'reference-kb', file_id: 'reference-file', offset, window_size: 200,
      start_line: count ? offset + 1 : 0, end_line: count ? offset + count : 0,
      total_lines: count ? 440 : 0, has_more_before: !!count && offset > 0,
      has_more_after: !!count && offset < 239, next_offset: offset + 200,
      content: Array.from({ length: count }, (_, index) => `${offset + index + 1}\t  引用原文第 ${offset + index + 1} 行，保留缩进和普通用户阅读权限。`).join('\n')
    } })
  }
  await page.route('**/api/**', handler)
  await page.evaluate(async () => {
    const main = await (await fetch('/src/app/main.js')).text()
    const { createApp, h } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
    const { createPinia } = await import(main.match(/from "([^"]*\/pinia\.js[^"']*)"/)[1])
    const { default: Answer } = await import('/src/modules/session/ui/ReferenceAnnotatedAnswer.vue')
    const { useTurnReferencesStore } = await import('/src/modules/session/model/turnReferences.js')
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    const { useUserStore } = await import('/src/modules/identity/model/user.js')
    const { configureApiAuth } = await import('/src/apis/auth_context.js')
    configureApiAuth({ getToken: () => 'ordinary-reference-fixture', isAdmin: () => false })
    const pinia = createPinia()
    const entry = useTurnReferencesStore(pinia).getEntry('reference-thread', 'reference-turn')
    entry.references = {
      sources: [{ id: 'source-1', kind: 'knowledge', title: '普通用户可读的飞书引用', kb_id: 'reference-kb', file_id: 'reference-file', supports_documents: false, supports_document_read: true }],
      citations: [{ source_id: 'source-1', answer_start_line: 1, answer_end_line: 1, source_start_line: 40, source_end_line: 41, quote: '引用原文' }]
    }
    const theme = useThemeStore(pinia)
    const root = document.querySelector('#app').__vue_app__
    const host = document.createElement('div')
    host.id = 'reference-reading-check'
    Object.assign(host.style, { position: 'fixed', inset: '24px', zIndex: 1000, padding: '24px', background: 'var(--gray-0)' })
    document.body.append(host)
    const app = createApp({ render: () => h(root._context.components.AConfigProvider, { theme: theme.currentTheme }, () => h(Answer, { content: '原文说明。', threadId: 'reference-thread', message: { turn_id: 'reference-turn', phase: 'final_answer' } })) })
    app.use(pinia)
    Object.assign(app._context.components, root._context.components)
    app.mount(host)
    const user = useUserStore(root.config.globalProperties.$pinia)
    window.referenceReading = { app, host, theme, restore: () => configureApiAuth({ getToken: () => user.token, isAdmin: () => user.isAdmin, isSuperAdmin: () => user.isSuperAdmin }) }
  })
  const reader = page.locator('.external-document-reader')
  const open = async () => {
    await page.locator('#reference-reading-check .reference-citation').click()
    if (!await reader.isVisible()) await page.getByRole('button', { name: /打开原文|查看原文/ }).click()
    await reader.waitFor()
  }
  try {
    await page.locator('#reference-reading-check .reference-citation').focus()
    await page.getByRole('button', { name: '查看原文', exact: true }).waitFor()
    await page.getByRole('button', { name: '查看原文', exact: true }).click()
    await reader.getByRole('status').waitFor()
    await page.locator('.ant-modal-content:visible').screenshot({ path: '/tmp/yuxi-knowledge-reference-loading.png' })
    releaseInitialRead()
    await reader.locator('.reader-line-highlight').first().waitFor()
    check(await reader.locator('.reader-line-highlight').count() === 2, '引用行未高亮')
    check((await reader.locator('.reader-line-text').first().textContent()).startsWith('  引用原文第 40'), '行定位或缩进错误')
    await reader.getByRole('button', { name: '下一页' }).click()
    await reader.getByText('第 240–247 行，共 440 行').waitFor()
    await reader.getByRole('button', { name: '上一页' }).click()
    await reader.locator('.reader-line-highlight').first().waitFor()
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => window.referenceReading.theme.setTheme(value), theme)
      for (const width of [1440, 1024, 768]) {
        await page.setViewportSize({ width, height: 900 })
        check(await reader.evaluate(el => el.scrollWidth <= el.clientWidth), '阅读组件横向溢出')
        await page.locator('.ant-modal-content:visible').screenshot({ path: `/tmp/yuxi-knowledge-reference-${theme}-${width}.png` })
      }
    }
    await reader.getByRole('button', { name: '关闭文档' }).click()
    await reader.waitFor({ state: 'detached' })
    mode = 'denied'
    await open()
    await reader.getByRole('alert').waitFor()
    await page.locator('.ant-modal-content:visible').screenshot({ path: '/tmp/yuxi-knowledge-reference-error.png' })
    check(await reader.locator('.reader-line').count() === 0, '权限错误后保留原文')
    mode = 'empty'
    await reader.getByRole('button', { name: '重新读取' }).click()
    await reader.getByText('文档正文为空').waitFor()
    check(requests.every(path => path === '/api/v1/knowledge/tools/open_kb_document'), '普通用户引用请求了管理接口')
    return { ordinaryUserReadOnly: true, lineOffset: 39, highlightedLines: 2, paging: true, errorAndRetry: true, empty: true, screenshots: 8, requests }
  } finally {
    await page.evaluate(previous => {
      window.referenceReading.theme.setTheme(previous || 'light')
      if (previous === null) localStorage.removeItem('theme')
      window.referenceReading.app.unmount(); window.referenceReading.host.remove(); window.referenceReading.restore(); delete window.referenceReading
    }, originalTheme)
    await page.setViewportSize(viewport)
    await page.unroute('**/api/**', handler)
  }
}
