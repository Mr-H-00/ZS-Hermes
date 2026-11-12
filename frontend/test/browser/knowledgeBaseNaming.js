// 已登录的 Vite 应用：playwright-cli -s=<session> run-code --filename=frontend/test/browser/knowledgeBaseNaming.js
// prettier-ignore
async (page) => {
  /** 验证真实创建、文件夹与详情路由，并清理本探针资源。 */
  const check = (value, message) => { if (!value) throw Error(message) }
  const origin = await page.evaluate(() => window.location.origin)
  const name = `naming-browser-${Date.now()}`
  let kbId
  let originalDark
  try {
    await page.goto(`${origin}/extensions?tab=knowledge`)
    await page.getByRole('button', { name: '新建知识库' }).click()
    const modal = page.locator('.ant-modal')
    await modal.locator('#knowledge-base-create-name').fill(name)
    await modal.getByRole('button', { name: '下一步' }).click()
    await modal.locator('.config-dropdown-trigger').click()
    await page.locator('.model-option').first().click()
    await modal.getByRole('button', { name: '下一步' }).click()
    const creation = page.waitForResponse(r => r.url().endsWith('/api/knowledge/knowledge-bases') && r.request().method() === 'POST', { timeout: 30000 })
    await modal.getByRole('button', { name: '创建知识库' }).click()
    const created = await creation
    const payload = await created.json()
    kbId = payload.kb_id
    check(created.ok() && kbId, '真实知识库创建失败')
    const body = created.request().postDataJSON()
    check(body.name === name && !('database_name' in body), '创建请求没有使用 name')
    await modal.waitFor({ state: 'hidden' })
    await page.getByPlaceholder('搜索知识库...').fill(name)
    await page.getByText(name, { exact: true }).click()
    await page.waitForURL(`**/extensions/knowledge-bases/${kbId}*`)
    await page.getByRole('button', { name: '上传', exact: true }).click()
    await page.getByRole('button', { name: '新建文件夹', exact: true }).click()
    await page.locator('.ant-modal input').fill('naming-folder')
    const folderCreation = page.waitForResponse(r => r.url().endsWith(`/knowledge-bases/${kbId}/folders`) && r.request().method() === 'POST', { timeout: 30000 })
    await page.locator('.ant-modal').getByRole('button', { name: '确 定' }).click()
    check((await folderCreation).ok(), '真实文件夹创建失败')
    await page.getByText('naming-folder', { exact: true }).waitFor()
    const facts = await page.evaluate(async (id) => {
      const { knowledgeBaseApi, documentApi } = await import('/src/apis/knowledge_api.js')
      const detail = await knowledgeBaseApi.getKnowledgeBaseInfo(id)
      const files = await documentApi.listDocuments(id)
      return { name: detail.name, folder: files.items.some(item => item.filename === 'naming-folder') }
    }, kbId)
    check(facts.name === name && facts.folder, '重新读取持久化结果不一致')
    await page.goto(`${origin}/extensions/knowledge-bases/${kbId}?section=evaluation`)
    await page.getByText('暂无评估基准', { exact: true }).waitFor()
    originalDark = await page.evaluate(async () => {
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      const theme = useThemeStore()
      const dark = theme.isDark
      theme.setTheme(false)
      return dark
    })
    await page.locator('.knowledge-base-info-container').screenshot({ path: '/tmp/yuxi-knowledge-naming-light.png' })
    await page.evaluate(async () => { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme(true) })
    await page.locator('.knowledge-base-info-container').screenshot({ path: '/tmp/yuxi-knowledge-naming-dark.png' })
    await page.setViewportSize({ width: 375, height: 812 })
    await page.locator('.knowledge-base-info-container').screenshot({ path: '/tmp/yuxi-knowledge-naming-mobile.png' })
    await page.setViewportSize({ width: 1280, height: 900 })
    return { create: true, nameField: true, detailRoute: true, persistedFolder: true, evaluationEmpty: true, screenshots: ['light', 'dark', 'mobile'] }
  } finally {
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.evaluate(async ({ id, dark }) => {
      if (dark !== undefined) { const { useThemeStore } = await import('/src/shared/model/theme.js'); useThemeStore().setTheme(dark) }
      if (id) {
        const { knowledgeBaseApi } = await import('/src/apis/knowledge_api.js')
        await knowledgeBaseApi.deleteKnowledgeBase(id)
        const listed = await knowledgeBaseApi.getKnowledgeBases()
        if (listed.knowledge_bases.some(item => item.kb_id === id)) throw Error('探针知识库未清理')
      }
    }, { id: kbId, dark: originalDark })
  }
}
