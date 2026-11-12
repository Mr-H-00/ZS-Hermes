/** 在已登录的知识库详情页运行，暂缓真实面板下载并检查内容区域居中。 */
async (page) => {
  const results = []
  for (const [tab, module] of [
    ['知识图谱', 'KnowledgeGraphSection'],
    ['评估', 'KnowledgeEvaluationWorkspace']
  ]) {
    let release
    const pending = new Promise(resolve => { release = resolve })
    const pattern = `**/${module}.vue*`
    await page.route(pattern, async route => {
      await pending
      await route.continue()
    })
    try {
      await page.reload()
      await page.getByRole('tab', { name: tab, exact: true }).click()
      const loading = page.getByRole('status', { name: '正在加载面板' })
      await loading.waitFor()
      const measure = async () => loading.evaluate(el => {
        const panel = el.parentElement.getBoundingClientRect()
        const indicator = el.querySelector('.ant-spin').getBoundingClientRect()
        return {
          dx: Math.abs(indicator.x + indicator.width / 2 - panel.x - panel.width / 2),
          dy: Math.abs(indicator.y + indicator.height / 2 - panel.y - panel.height / 2)
        }
      })
      const centered = await measure()
      if (centered.dx > 2 || centered.dy > 2) throw new Error(`${tab}加载未居中：${JSON.stringify(centered)}`)
      // 恢复旧布局，确认同一个几何 oracle 能识别左上角缺陷。
      await loading.evaluate(el => { el.classList.remove('async-panel-loading') })
      const oldLayout = await measure()
      await loading.evaluate(el => { el.classList.add('async-panel-loading') })
      if (oldLayout.dx <= 2 && oldLayout.dy <= 2) throw new Error('居中检查未识别旧布局')
      await page.locator('.knowledge-detail-layout').screenshot({ path: `/tmp/yuxi-${module}-loading.png` })
      await page.evaluate(() => document.documentElement.classList.add('dark'))
      await page.setViewportSize({ width: 720, height: 800 })
      const compact = await measure()
      if (compact.dx > 2 || compact.dy > 2) throw new Error(`${tab}窄屏暗色加载未居中`)
      results.push({ tab, centered, oldLayout, compact })
    } finally {
      release()
      await page.unrouteAll({ behavior: 'wait' })
      await page.evaluate(() => document.documentElement.classList.remove('dark'))
      await page.setViewportSize({ width: 1440, height: 900 })
    }
  }
  return results
}
