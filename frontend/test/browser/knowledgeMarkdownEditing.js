// 先在该浏览器会话打开 Vite 开发页面；截图写入当前工作目录的 tmp/。
// 在仓库根目录运行：playwright-cli -s=markdown-review run-code --filename=frontend/test/browser/knowledgeMarkdownEditing.js
// 原生刷新确认可能让 CLI 提前返回；脚本自动取消刷新，最终结果可从 window.markdownUi.verification 回读。
async page => {
  try {
    await page.evaluate(() => { if (window.markdownUi) window.markdownUi.open.value = false })
    await page.waitForTimeout(100)
    await page.unrouteAll({ behavior: 'wait' })
    const origin = await page.evaluate(() => location.origin)
    await page.goto(origin)
    const errors = []
    page.on('pageerror', error => errors.push(error.message))
    const requests = []
    const jobs = new Map()
    let readFailure = false, saveFailure = false, missingJob = false, loseResponse = false
    let hasParsed = true, status = 'indexed', revision = 'revision-original'
    let content = '# 完整 Markdown\n\n这是知识文档的正文，编辑后可以切换预览核对格式。\n\n- 保留原始 Markdown\n- 保存前检查内容\n\n```python\nprint("knowledge")\n```'
    await page.route('**/api/**', async route => {
      const request = route.request()
      const path = request.url().replace(/^https?:\/\/[^/]+/, '').split('?')[0]
      if (path.endsWith('/markdown')) {
        if (request.method() === 'GET') {
          if (readFailure) return route.fulfill({ status: 503, json: { detail: '完整 Markdown 对象不可读' } })
          return route.fulfill({ json: { content, revision, has_parsed_markdown: hasParsed } })
        }
        requests.push(request.postDataJSON())
        if (saveFailure) return route.fulfill({ status: 409, json: { detail: '修订已变更' } })
        if (loseResponse) {
          content = request.postDataJSON().content
          revision = 'published-without-response'
          return route.abort('failed')
        }
        const id = `save-${requests.length}`
        jobs.set(id, { id, type: 'knowledge_document_reset', name: '保存 Markdown', status: 'pending' })
        return route.fulfill({ json: { job_id: id, status: 'queued' } })
      }
      if (path.endsWith('/basic')) return route.fulfill({ json: { meta: { file_id: 'file-a', filename: '知识文档.pdf', status, active_generation: 1, chunk_count: status === 'indexed' ? 2 : 0, has_original_file: false, has_parsed_markdown: hasParsed } } })
      if (path.endsWith('/content')) return route.fulfill({ json: { content: '# 阅读内容', lines: [{ id: 'chunk-old', content: '片段不能用于编辑' }] } })
      if (path.startsWith('/api/background-jobs/')) {
        if (missingJob) return route.fulfill({ status: 404, json: { detail: '任务不存在' } })
        return route.fulfill({ json: { job: jobs.get(path.split('/').pop()) } })
      }
      if (path === '/api/background-jobs') return route.fulfill({ json: { jobs: missingJob ? [] : [...jobs.values()], summary: { status_counts: { pending: [...jobs.values()].filter(job => job.status === 'pending').length } } } })
      return route.fulfill({ json: {} })
    })
    await page.evaluate(async () => {
      const main = await (await fetch('/src/app/main.js')).text()
      const { createApp, h, ref } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
      const { createPinia } = await import(main.match(/from "([^"]*\/pinia\.js[^"']*)"/)[1])
      const routerSource = await (await fetch('/src/app/router/index.js')).text()
      const { createRouter, createMemoryHistory, RouterView } = await import(routerSource.match(/from "([^"]*\/vue-router\.js[^"']*)"/)[1])
      const { default: Detail } = await import('/src/modules/knowledge/ui/FileDetailModal.vue')
      const { useBackgroundJobsStore } = await import('/src/modules/background-jobs/model/jobs.js')
      const { useUserStore } = await import('/src/modules/identity/model/user.js')
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      const { configureApiAuth } = await import('/src/apis/auth_context.js')
      configureApiAuth({ getToken: () => 'markdown-browser-fixture', isAdmin: () => true })
      const root = document.querySelector('#app').__vue_app__
      const pinia = createPinia()
      const user = useUserStore(pinia)
      user.token = 'markdown-browser-fixture'
      user.userRole = 'superadmin'
      const theme = useThemeStore(pinia)
      const host = document.createElement('main')
      host.id = 'markdown-ui'
      document.querySelector('#app').style.display = 'none'
      document.body.append(host)
      const open = ref(true), editable = ref(true), editRequested = ref(true)
      const events = []
      const EditorPage = { render: () => h(Detail, {
        open: open.value, editable: editable.value, editRequested: editRequested.value,
        kbId: router.currentRoute.value.params.kbId, fileId: 'file-a',
        'onUpdate:open': value => { open.value = value }, onSaved: () => events.push('saved')
      }) }
      const router = createRouter({ history: createMemoryHistory(), routes: [
        { path: '/', redirect: '/kb/kb-a' },
        { path: '/kb/:kbId', component: EditorPage },
        { path: '/away', component: { render: () => h('p', '已离开编辑器') } }
      ] })
      const app = createApp({ render: () => h(root._context.components.AConfigProvider, { theme: theme.currentTheme }, () => h(RouterView)) })
      app.use(pinia)
      app.use(router)
      await router.isReady()
      Object.assign(app._context.components, root._context.components)
      app.mount(host)
      window.markdownUi = { router, open, editable, editRequested, events, theme, jobs: useBackgroundJobsStore(pinia) }
    })

    const textarea = page.locator('.file-edit-textarea')
    const toolbar = page.locator('.markdown-editor-toolbar')
    const panel = page.locator('.markdown-editor-panel')
    const save = panel.getByRole('button', { name: /^保\s*存$/ })
    const assert = (value, reason) => { if (!value) throw new Error(reason) }
    const confirmExit = page.getByRole('dialog').filter({ hasText: '放弃未保存的修改？' })
    const reopen = async () => {
      await page.evaluate(() => { window.markdownUi.open.value = false })
      await page.waitForTimeout(350)
      await page.evaluate(() => { window.markdownUi.open.value = true })
      await textarea.waitFor()
    }
    await textarea.waitFor()
    assert(await textarea.inputValue() === content, 'must edit complete object')
    assert(await save.isDisabled(), 'unchanged parsed content disabled')
    await page.evaluate(() => window.markdownUi.router.push('/away'))
    await page.getByText('已离开编辑器', { exact: true }).waitFor()
    await page.evaluate(() => window.markdownUi.router.push('/kb/kb-a'))
    await textarea.waitFor()
    assert(await page.locator('.edit-floating-actions').count() === 0, 'no duplicate editing controls')
    assert(!await page.evaluate(() => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented }), 'clean document does not intercept reload')
    const draft = '# 人工修订\n\n编辑与预览之间切换，草稿应完整保留。\n\n## 核对清单\n\n- **重点内容**\n- [引用链接](https://example.org/document)\n\n```python\nprint("draft")\n```'
    await textarea.fill(draft)
    await page.getByText('未保存', { exact: true }).waitFor()
    assert(await save.isEnabled(), 'changed draft must enable save')
    for (const [theme, width] of [['light', 1440], ['dark', 1024], ['dark', 768]]) {
      await page.setViewportSize({ width, height: 900 })
      await page.evaluate(theme => window.markdownUi.theme.setTheme(theme), theme)
      await page.waitForTimeout(100)
      assert(await textarea.evaluate(el => el.getBoundingClientRect().width > 400 && el.getBoundingClientRect().bottom <= innerHeight), `editor fits ${width}`)
      await page.screenshot({ path: `tmp/knowledge-markdown-${theme}-${width}.png` })
    }
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.evaluate(() => window.markdownUi.theme.setTheme('light'))
    await page.waitForTimeout(250)
    await toolbar.getByText('预览', { exact: true }).click()
    await page.locator('.markdown-draft-preview').getByRole('heading', { name: '人工修订' }).waitFor()
    assert(await page.locator('.markdown-draft-preview').getByRole('link', { name: '引用链接' }).getAttribute('href') === 'https://example.org/document', 'preview consumes current draft')
    await page.screenshot({ path: 'tmp/knowledge-markdown-preview.png' })
    await toolbar.getByRole('radio', { name: '预览', exact: true }).press('ArrowLeft')
    await textarea.waitFor({ state: 'visible' })
    assert(await toolbar.getByRole('radio', { name: '编辑', exact: true }).isChecked(), 'native radio arrows switch view accessibly')
    assert(await textarea.inputValue() === draft, 'preview round trip retains exact draft')
    await panel.getByRole('button', { name: '取消编辑' }).click()
    await confirmExit.waitFor()
    await confirmExit.getByRole('button', { name: '继续编辑' }).click()
    assert(await textarea.inputValue() === draft, 'canceling discard retains draft')
    await page.getByRole('button', { name: '关闭预览', exact: true }).click()
    await confirmExit.waitFor()
    await confirmExit.getByRole('button', { name: '继续编辑' }).click()
    await textarea.press('Escape')
    await confirmExit.waitFor()
    await confirmExit.getByRole('button', { name: '继续编辑' }).click()
    await page.locator('.ant-modal-wrap').first().click({ position: { x: 1, y: 1 } })
    await confirmExit.waitFor()
    await confirmExit.getByRole('button', { name: '继续编辑' }).click()
    assert(requests.length === 0 && await textarea.inputValue() === draft, 'all discard paths retain draft without POST')
    assert(await page.evaluate(() => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented }), 'dirty draft intercepts beforeunload')
    let unloadDialogType = ''
    page.once('dialog', async dialog => { unloadDialogType = dialog.type(); await dialog.dismiss() })
    await page.reload({ timeout: 1000 }).catch(() => {})
    assert(unloadDialogType === 'beforeunload' && await textarea.inputValue() === draft, 'real reload prompts and cancelling retains current draft')
    await page.evaluate(() => { void window.markdownUi.router.push('/away') })
    await confirmExit.waitFor()
    await confirmExit.getByRole('button', { name: '继续编辑' }).click()
    assert(await page.evaluate(() => window.markdownUi.router.currentRoute.value.path) === '/kb/kb-a' && await textarea.inputValue() === draft, 'route leave cancellation preserves draft')
    await confirmExit.waitFor({ state: 'hidden' })
    await page.evaluate(() => window.markdownUi.router.push('/kb/kb-a?section=graph'))
    assert(await page.evaluate(() => window.markdownUi.router.currentRoute.value.query.section) === 'graph' && !await confirmExit.isVisible() && await textarea.inputValue() === draft, 'same knowledge query change retains editor without prompting')
    await page.evaluate(() => window.markdownUi.router.push('/kb/kb-a'))
    await page.evaluate(() => { void window.markdownUi.router.push('/kb/kb-b') })
    await confirmExit.waitFor()
    await confirmExit.getByRole('button', { name: '继续编辑' }).click()
    assert(await page.evaluate(() => window.markdownUi.router.currentRoute.value.path) === '/kb/kb-a' && await textarea.inputValue() === draft, 'same-route knowledge change cannot discard draft')
    await page.evaluate(() => { void window.markdownUi.router.push('/away') })
    await confirmExit.waitFor()
    await confirmExit.getByRole('button', { name: '放弃修改' }).click()
    await page.getByText('已离开编辑器', { exact: true }).waitFor()
    await page.evaluate(() => window.markdownUi.router.push('/kb/kb-a'))
    await textarea.waitFor()
    assert(await textarea.inputValue() === content, 'explicit route discard remounts from authoritative object')
    await textarea.fill(draft)
    const saveConfirm = page.getByRole('dialog').filter({ hasText: '保存将撤销此文件' })
    await save.click()
    await saveConfirm.waitFor()
    await page.waitForTimeout(250)
    await page.screenshot({ path: 'tmp/knowledge-markdown-save-confirm.png' })
    saveFailure = true
    await saveConfirm.getByRole('button', { name: /^保\s*存$/ }).click()
    await page.getByRole('button', { name: '查看最新版本' }).waitFor()
    assert(await save.isDisabled(), 'conflict prevents blindly resending stale revision')
    content = '# 他人刚更新的最新内容\n\n这是另一位管理员的修改，覆盖前必须先核对。'
    revision = 'revision-latest'
    await page.getByRole('button', { name: '查看最新版本' }).click()
    const latestDialog = page.getByRole('dialog').filter({ hasText: '核对最新 Markdown' })
    await latestDialog.waitFor()
    assert((await latestDialog.textContent()).includes('另一位管理员'), 'latest body visible before adopting revision')
    await page.waitForTimeout(250)
    await page.screenshot({ path: 'tmp/knowledge-markdown-conflict.png' })
    await latestDialog.getByRole('button', { name: '返回编辑' }).click()
    assert(await save.isDisabled() && await textarea.inputValue() === draft, 'cancel conflict resolution keeps original revision and draft')
    await page.getByRole('button', { name: '查看最新版本' }).click()
    await latestDialog.getByRole('button', { name: '保留草稿并继续' }).click()
    assert(await textarea.inputValue() === draft, 'adopting baseline must retain draft')
    saveFailure = false
    await save.click()
    await saveConfirm.waitFor()
    await saveConfirm.getByRole('button', { name: /^保\s*存$/ }).click()
    await page.getByText('正在保存…', { exact: true }).waitFor()
    assert(await textarea.isDisabled(), 'pending prevents concurrent draft edits')
    assert(requests.at(-1).expected_revision === 'revision-latest' && requests.at(-1).content === draft, 'explicit retry uses accepted latest revision with original draft')
    missingJob = true
    await page.evaluate(() => window.markdownUi.jobs.loadJobs())
    await page.getByText('暂时无法确认保存结果。可重试查询，或下载草稿后关闭。').waitFor()
    await panel.getByRole('button', { name: /^关\s*闭$/ }).click()
    const savingExit = page.getByRole('dialog').filter({ hasText: '保存仍在进行' })
    await savingExit.waitFor()
    const downloadPromise = page.waitForEvent('download')
    await savingExit.getByRole('button', { name: '下载草稿' }).click()
    const download = await downloadPromise
    const stream = await download.createReadStream()
    let downloaded = ''
    for await (const chunk of stream) downloaded += chunk.toString()
    assert(downloaded === draft, 'downloaded bytes independently recover exact pending draft')
    await page.waitForTimeout(250)
    await page.screenshot({ path: 'tmp/knowledge-markdown-pending-exit.png' })
    await savingExit.getByRole('button', { name: '关闭编辑器' }).click()
    await page.waitForFunction(() => !window.markdownUi.open.value)
    jobs.get([...jobs.keys()].at(-1)).status = 'failed'
    missingJob = false
    await reopen()
    assert(await textarea.inputValue() === content, 'reopen reads actual latest object after failed save')
    assert(await page.evaluate(() => window.markdownUi.events.length) === 0, 'unknown or failed save never emits success')
    await textarea.fill(draft)
    await save.click()
    await saveConfirm.waitFor()
    await saveConfirm.getByRole('button', { name: /^保\s*存$/ }).click()
    await page.getByText('正在保存…', { exact: true }).waitFor()
    const job = jobs.get([...jobs.keys()].at(-1))
    job.status = 'failed'; job.error = '存储写入失败，请重试'
    await page.evaluate(() => window.markdownUi.jobs.loadJobs())
    await page.getByText('保存结果需确认：存储写入失败，请重试。草稿已保留，请核对最新版本。', { exact: true }).waitFor()
    assert(await textarea.inputValue() === draft && await save.isDisabled(), 'worker failure requires latest content review')
    await page.getByRole('button', { name: '查看最新版本' }).click()
    await latestDialog.getByRole('button', { name: '保留草稿并继续' }).click()
    await save.click()
    await saveConfirm.waitFor()
    await saveConfirm.getByRole('button', { name: /^保\s*存$/ }).click()
    await page.getByText('正在保存…', { exact: true }).waitFor()
    jobs.get([...jobs.keys()].at(-1)).status = 'cancelled'
    jobs.get([...jobs.keys()].at(-1)).error = '已取消'
    content = draft
    await page.evaluate(() => window.markdownUi.jobs.loadJobs())
    await page.getByRole('button', { name: '查看最新版本' }).waitFor()
    assert(await save.isDisabled() && await textarea.inputValue() === draft, 'cancelled after publication keeps result uncertain')
    assert(await page.evaluate(() => window.markdownUi.events.length) === 0, 'cancelled job alone cannot prove saved')
    await page.getByRole('button', { name: '查看最新版本' }).click()
    await latestDialog.getByText('文件已包含当前草稿，无需再次保存。').waitFor()
    await page.waitForTimeout(250)
    await page.screenshot({ path: 'tmp/knowledge-markdown-published-cancelled.png' })
    const postCount = requests.length
    await latestDialog.getByRole('button', { name: '确认文件内容' }).click()
    await page.waitForFunction(() => !window.markdownUi.open.value)
    assert(await page.evaluate(() => window.markdownUi.events.length) === 1 && requests.length === postCount, 'verified published body refreshes without another POST')
    await reopen()
    await textarea.fill(`${draft}\n\n响应丢失后的内容`)
    loseResponse = true
    await save.click()
    await saveConfirm.waitFor()
    await saveConfirm.getByRole('button', { name: /^保\s*存$/ }).click()
    await page.getByRole('button', { name: '查看最新版本' }).waitFor()
    assert(await save.isDisabled(), 'response lost requires checking latest object')
    const lostPostCount = requests.length
    loseResponse = false
    await page.getByRole('button', { name: '查看最新版本' }).click()
    await latestDialog.getByText('文件已包含当前草稿，无需再次保存。').waitFor()
    await latestDialog.getByRole('button', { name: '确认文件内容' }).click()
    await page.waitForFunction(() => !window.markdownUi.open.value)
    assert(await page.evaluate(() => window.markdownUi.events.length) === 2 && requests.length === lostPostCount, 'published response lost recovered from body with no duplicate write')
    await latestDialog.waitFor({ state: 'hidden' })
    readFailure = true
    await page.evaluate(() => { window.markdownUi.open.value = true })
    await page.getByRole('button', { name: '重试读取' }).waitFor()
    assert(await textarea.count() === 0 && await save.isDisabled(), 'strict read failure cannot use empty or chunks')
    await page.waitForTimeout(250)
    assert(!await latestDialog.isVisible(), 'read error screenshot excludes closing verification dialog')
    await page.waitForFunction(() => !document.querySelector('.ant-message-notice'))
    await page.screenshot({ path: 'tmp/knowledge-markdown-read-error.png' })
    readFailure = false; status = 'uploaded'; hasParsed = false; content = ''
    await reopen()
    assert(await textarea.inputValue() === '' && await save.isEnabled(), 'missing parsed result can explicitly save empty Markdown')
    await page.waitForFunction(() => !document.querySelector('.ant-zoom-enter-active, .ant-zoom-appear-active, .ant-zoom-leave-active, .ant-message-notice'))
    await page.screenshot({ path: 'tmp/knowledge-markdown-empty.png' })
    loseResponse = true
    await save.click()
    await page.getByRole('button', { name: '查看最新版本' }).waitFor()
    assert(await textarea.inputValue() === '' && await save.isDisabled(), 'unknown empty save has no dirty bytes but remains uncertain')
    assert(await page.evaluate(() => { const event = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(event); return event.defaultPrevented }), 'unknown empty save intercepts reload')
    await page.evaluate(() => { void window.markdownUi.router.push('/kb/kb-b') })
    const uncertainExit = page.getByRole('dialog').filter({ hasText: '保存结果尚未确认。关闭后将丢弃此处的草稿' })
    await uncertainExit.waitFor()
    await uncertainExit.getByRole('button', { name: '继续编辑' }).click()
    assert(await page.evaluate(() => window.markdownUi.router.currentRoute.value.path) === '/kb/kb-a', 'unknown empty save prevents route update')
    loseResponse = false
    hasParsed = true
    await page.getByRole('button', { name: '查看最新版本' }).click()
    await latestDialog.getByRole('button', { name: '确认文件内容' }).click()
    await page.waitForFunction(() => !window.markdownUi.open.value)
    hasParsed = false
    await reopen()
    await save.click()
    await page.getByText('正在保存…', { exact: true }).waitFor()
    assert(!await saveConfirm.isVisible() && requests.at(-1).content === '', 'no fabricated downstream cleanup for new empty content')
    jobs.get([...jobs.keys()].at(-1)).status = 'success'
    await page.evaluate(() => window.markdownUi.jobs.loadJobs())
    await page.waitForFunction(() => !window.markdownUi.open.value)
    assert(errors.length === 0, `page errors: ${errors.join(';')}`)
    const verification = { pageErrors: errors, requests: requests.length, routerViewGuards: ['clean leave', 'cancel leave', 'confirm leave', 'cancel knowledge update', 'same knowledge query allowed'], nativeBeforeUnload: unloadDialogType, downloadMatchesDraft: downloaded === draft, widths: [1440, 1024, 768], evidence: '真实组件、Pinia 与 HTTP 夹具；后端持久化另行验证' }
    await page.evaluate(result => { window.markdownUi.verification = result }, verification)
    return verification
  } catch (error) {
    await page.evaluate(message => { window.markdownUi.verification = { error: message } }, error.message)
    throw error
  }
}
