// 在真实 Vite 页面运行，验证文件构建状态失败后的可点击重试。
async page => {
  let attempts = 0
  let allowSuccess = false
  let allowBuildStatus = false
  const routeHandler = async route => {
    const pathname = route.request().url().replace(/^https?:\/\/[^/]+/, '').split('?')[0]
    if (pathname.endsWith('/graph-build/files/file/status')) {
      attempts++
      return !allowSuccess ? route.fulfill({ status: 500, json: { detail: 'fixture failure' } }) : route.fulfill({ json: { pending_chunks: 1 } })
    }
    if (pathname.endsWith('/graph-build/status')) return allowBuildStatus
      ? route.fulfill({ json: { locked: true, pending_chunks: 1 } })
      : route.fulfill({ status: 500, json: { detail: 'fixture failure' } })
    if (pathname.endsWith('/basic')) return route.fulfill({ json: { meta: { file_id: 'file', filename: '样本文件.md' } } })
    if (pathname.endsWith('/subgraph')) return route.fulfill({ json: { success: true, data: { nodes: [], edges: [] } } })
    return route.fulfill({ json: {} })
  }
  await page.route('**/api/**', routeHandler)
  await page.evaluate(async () => {
    const main = await (await fetch('/src/app/main.js')).text()
    const { createApp, h, ref } = await import(main.match(/from "([^"]*\/vue\.js[^"']*)"/)[1])
    const { createPinia } = await import(main.match(/from "([^"]*\/pinia\.js[^"']*)"/)[1])
    const { default: Graph } = await import('/src/modules/knowledge/ui/KnowledgeGraphSection.vue')
    const { useKnowledgeBaseStore } = await import('/src/modules/knowledge/model/knowledgeBase.js')
    const { configureApiAuth } = await import('/src/apis/auth_context.js')
    const { useUserStore } = await import('/src/modules/identity/model/user.js')
    const root = document.querySelector('#app').__vue_app__
    configureApiAuth({ getToken: () => 'test-fixture', isAdmin: () => true })
    const host = document.createElement('div')
    host.id = 'graph-status-check'
    Object.assign(host.style, { position: 'fixed', inset: '20px', zIndex: 1000, background: 'var(--gray-0)' })
    document.body.append(host)
    const pinia = createPinia()
    const readonly = ref(false)
    const app = createApp({ render: () => h(Graph, { visible: true, active: true, readonly: readonly.value, fileId: 'file' }) })
    app.use(pinia)
    app.use(root.config.globalProperties.$router)
    Object.assign(app._context.components, root._context.components)
    app.runWithContext(() => { const store = useKnowledgeBaseStore(pinia); store.kbId = 'kb'; store.knowledgeBase = { kb_id: 'kb', kb_type: 'milvus' } })
    app.mount(host)
    const user = useUserStore(root.config.globalProperties.$pinia)
    window.graphStatusCheck = { app, host, readonly, restoreAuth: () => configureApiAuth({ getToken: () => user.token, isAdmin: () => user.isAdmin, isSuperAdmin: () => user.isSuperAdmin }) }
  })
  try {
    const retry = page.getByRole('button', { name: '文件构建状态加载失败，重试' })
    await retry.waitFor({ state: 'visible', timeout: 10000 })
    const buildRetry = page.getByRole('button', { name: '图谱构建状态加载失败，重试' })
    await buildRetry.waitFor({ state: 'visible' })
    await page.getByRole('button', { name: '全库索引管理' }).click()
    await page.getByText('图谱构建状态加载失败。').waitFor({ state: 'visible' })
    if (await page.getByRole('button', { name: '配置抽取器' }).count()) throw new Error('失败状态误显示为未配置')
    await page.evaluate(() => { window.graphStatusCheck.readonly.value = true })
    await page.getByRole('button', { name: '图谱展示设置' }).click()
    await page.getByText('最大节点数 (limit)').waitFor({ state: 'visible' })
    await page.evaluate(() => { window.graphStatusCheck.readonly.value = false })
    allowBuildStatus = true
    await buildRetry.click()
    allowSuccess = true
    await retry.click()
    await page.getByRole('button', { name: '构建此文件图谱' }).waitFor({ state: 'visible' })
    await page.locator('#graph-status-check').screenshot({ path: '/tmp/yuxi-file-status-retry.png' })
    return { attempts, retryClicked: attempts >= 2 }
  } finally {
    await page.evaluate(() => { window.graphStatusCheck.app.unmount(); window.graphStatusCheck.host.remove(); window.graphStatusCheck.restoreAuth(); delete window.graphStatusCheck })
    await page.unroute('**/api/**', routeHandler)
  }
}
