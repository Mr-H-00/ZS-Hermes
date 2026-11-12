// 独立浏览器：playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/composerSurfaces.js
// 使用真实组件和合成数据，所有 API 写入都被拒绝。
async page => {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const origin = page.url().split('/').slice(0, 3).join('/')
  const failures = []
  page.on('pageerror', error => failures.push(error.message))
  await page.route('**/api/**', async route => {
    check(route.request().method() === 'GET', '样式验收不得写入业务数据')
    const options = route.request().url().includes('/system/config/options')
    await route.fulfill({ json: options ? { options: [] } : {} })
  })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`${origin}/test/browser/settingsStyles.html`)
  await page.waitForFunction(() => window.settingsStyles)
  await page.evaluate(() => window.settingsStyles.mount('standalone', 'account'))
  await page.evaluate(async () => {
    const source = await (await fetch('/src/shared/model/theme.js')).text()
    const dependency = name => source.match(new RegExp(`from "([^"]*/${name}\\.js[^"]*)"`))[1]
    const { createApp, h, reactive, nextTick } = await import(dependency('vue'))
    const { default: Antd, ConfigProvider, Modal } = await import(source.match(/from "([^"]*ant-design-vue\.js[^"]*)"/)[1])
    const { default: Composer } = await import('/src/modules/session/ui/MessageInputComponent.vue')
    const { default: InfoCard } = await import('/src/shared/ui/InfoCard.vue')
    const { default: Metric } = await import('/src/modules/dashboard/ui/DashboardMetricCard.vue')
    const { default: Chart } = await import('/src/modules/dashboard/ui/DashboardChartCard.vue')
    const { useThemeStore } = await import('/src/shared/model/theme.js')
    const root = document.querySelector('#app')
    const pinia = root.__vue_app__.config.globalProperties.$pinia
    const theme = useThemeStore(pinia)
    const state = reactive({ text: '整理资料并提炼关键结论', disabled: false, loading: false, sends: 0, modal: false })
    const host = document.createElement('main')
    host.id = 'composer-review'
    host.style.cssText = 'position:fixed;inset:0;z-index:900;overflow:auto;background:var(--bg-page);color:var(--gray-900);padding:48px 24px'
    document.body.append(host)
    root.style.display = 'none'
    const app = createApp({ render: () => h(ConfigProvider, { theme: theme.currentTheme }, { default: () =>
      h('section', { style: 'max-width:800px;margin:auto;display:grid;gap:24px' }, [
        h('h1', { style: 'font-size:24px;margin:0' }, '背景分层与输入体验'),
        h('p', { style: 'margin:0;color:var(--gray-500)' }, '页面、卡片、输入框使用不同灰阶，保留清晰的交互状态。'),
        h('div', { style: 'display:grid;grid-template-columns:1fr 1fr;gap:16px' }, [
          h(InfoCard, { title: '知识检索', description: '根据当前权限查找资料与来源' }),
          h(Metric, { label: '示例任务', value: 12 })
        ]),
        h(Chart, { title: '嵌套指标' }, { summary: () => h(Metric, { compact: true, label: '示例指标', value: 3 }) }),
        h(Composer, {
          modelValue: state.text, 'onUpdate:modelValue': value => { state.text = value },
          disabled: state.disabled, sendButtonDisabled: state.disabled, isLoading: state.loading,
          enableUploads: true, onSend: () => { state.sends++ }
        }),
        h(Modal, { open: state.modal, title: '背景分层', footer: null }, { default: () => '浮层通过背景亮度区分。' })
      ])
    }) })
    app.use(pinia)
    app.use(Antd)
    app.mount(host)
    window.composerReview = { state, theme, nextTick }
  })
  /** 等待背景过渡和有限动画结束，避免采集过渡帧。 */
  const settle = async () => {
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    await page.waitForFunction(() => document.getAnimations().every(animation =>
      animation.effect.getTiming().iterations === Infinity || animation.playState !== 'running'
    ))
  }
  const sample = () => page.locator('#composer-review .input-box').evaluate(element => {
    const input = getComputedStyle(element)
    const send = getComputedStyle(element.querySelector('.send-button'))
    const cards = [...document.querySelectorAll('#composer-review .info-card, #composer-review .dashboard-metric-card')]
    return {
      background: input.backgroundColor, border: input.borderTopWidth, shadow: input.boxShadow,
      send: { background: send.backgroundColor, color: send.color, shadow: send.boxShadow, opacity: send.opacity },
      cards: cards.map(card => ({ border: getComputedStyle(card).borderColor, shadow: getComputedStyle(card).boxShadow }))
    }
  })
  const observations = []
  for (const dark of [false, true]) {
    await page.evaluate(async value => {
      window.composerReview.theme.setTheme(value)
      await window.composerReview.nextTick()
      document.activeElement.blur()
    }, dark)
    await page.mouse.move(0, 0)
    await settle()
    const normal = await sample()
    await page.evaluate(async () => { window.composerReview.state.text = ''; await window.composerReview.nextTick() })
    const editor = page.locator('#composer-review [role="textbox"]')
    for (const focused of [false, true]) {
      if (focused) await editor.focus()
      await settle()
      const placeholder = await editor.evaluate(element => {
        const reference = document.createElement('span')
        reference.style.color = 'var(--gray-300)'
        element.parentElement.appendChild(reference)
        const result = {
          empty: element.matches(':empty'),
          content: getComputedStyle(element, '::before').content,
          color: getComputedStyle(element, '::before').color,
          expectedColor: getComputedStyle(reference).color
        }
        reference.remove()
        return result
      })
      check(placeholder.empty && placeholder.content.length > 2, '空输入框没有显示占位提示')
      check(placeholder.color === placeholder.expectedColor, '空输入框占位文字必须使用 gray-300')
    }
    await page.evaluate(async () => {
      window.composerReview.state.text = '整理资料并提炼关键结论'
      document.activeElement.blur()
      await window.composerReview.nextTick()
    })
    await settle()

    const nested = await page.locator('#composer-review .dashboard-metric-card.compact').evaluate(element => ({
      background: getComputedStyle(element).backgroundColor,
      parent: getComputedStyle(element.closest('.dashboard-chart-card')).backgroundColor,
      parentBorder: getComputedStyle(element.closest('.dashboard-chart-card')).borderColor
    }))
    if (dark) {
      check(normal.border === '0px' && normal.shadow === 'none', '暗色输入框仍有描边或阴影')
      check(normal.send.background === 'rgb(245, 247, 247)' && normal.send.color === 'rgb(20, 22, 25)' && normal.send.shadow === 'none', '暗色发送按钮应为无阴影的白底深色图标')
      check(normal.cards.every(card => card.border === 'rgb(61, 66, 71)' && card.shadow === 'none'), '暗色普通卡片应保留深灰细边框且无装饰阴影')
      check(nested.background !== nested.parent, '嵌套指标卡与父卡片缺少背景阶梯')
      check(nested.parentBorder === 'rgb(61, 66, 71)', '暗色图表卡片缺少深灰边界')
    } else {
      check(normal.background === 'rgb(255, 255, 255)' && normal.border === '1px', '浅色输入框发生回归')
      check(normal.send.background === 'rgb(57, 150, 174)', '浅色发送按钮配色发生回归')
    }
    for (const width of [1280, 1024, 768]) {
      await page.setViewportSize({ width, height: 900 })
      check(await page.locator('#composer-review').evaluate(element => element.scrollWidth <= innerWidth), '输入区域横向溢出')
    }
    await page.locator('#composer-review .input-box').hover()
    await settle()
    const hovered = await sample()
    await page.locator('#composer-review [role="textbox"]').focus()
    await settle()
    const focused = await sample()
    if (dark) check(normal.background !== hovered.background && hovered.background !== focused.background, '输入框悬停与焦点应通过背景区分')
    await page.keyboard.press('Tab')
    if (dark) check(await page.locator('#composer-review .send-button').evaluate(element => {
      const style = getComputedStyle(element)
      return element === document.activeElement && element.matches(':focus-visible') && style.outlineStyle === 'solid' && parseFloat(style.outlineWidth) >= 2
    }), '发送按钮缺少键盘焦点反馈')
    await page.evaluate(async () => { window.composerReview.state.disabled = true; await window.composerReview.nextTick() })
    const disabledButton = page.locator('#composer-review .send-button')
    check(await disabledButton.isDisabled(), '禁用按钮仍可点击')
    const before = await page.evaluate(() => window.composerReview.state.sends)
    await disabledButton.evaluate(element => element.click())
    check(await page.evaluate(() => window.composerReview.state.sends) === before, '禁用按钮触发发送')
    check(Number((await sample()).send.opacity) < 1, '禁用状态缺少视觉区分')
    await page.evaluate(async () => { window.composerReview.state.disabled = false; await window.composerReview.nextTick() })
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await page.evaluate(async () => { window.composerReview.state.loading = true; await window.composerReview.nextTick() })
    await page.getByRole('button', { name: '停止回答', exact: true }).click()
    check(await page.evaluate(() => window.composerReview.state.sends) === before + 2, '发送或停止行为发生回归')
    await page.evaluate(async () => { window.composerReview.state.loading = false; document.activeElement.blur(); await window.composerReview.nextTick() })
    await page.mouse.move(0, 0)
    await settle()
    await page.locator('#composer-review > section').screenshot({ path: `/tmp/yuxi-composer-${dark ? 'dark' : 'light'}.png`, animations: 'disabled' })
    observations.push({ dark, normal, hovered: hovered.background, focused: focused.background })
  }
  const transfer = await page.evaluateHandle(() => {
    const data = new DataTransfer()
    data.items.add(new File(['fixture'], 'fixture.txt', { type: 'text/plain' }))
    return data
  })
  const box = page.locator('#composer-review .input-box')
  await box.dispatchEvent('dragenter', { dataTransfer: transfer })
  await page.waitForFunction(() => document.querySelector('#composer-review .input-box').classList.contains('dragging-files'))
  const dragHint = await box.evaluate(element => {
    const style = getComputedStyle(element, '::after')
    return { content: style.content, border: style.borderTopStyle, width: style.borderTopWidth }
  })
  check(dragHint.content.includes('释放以上传附件') && dragHint.border === 'dashed' && parseFloat(dragHint.width) > 0, '去掉装饰边框后丢失拖拽提示')
  await box.dispatchEvent('dragleave', { dataTransfer: transfer })
  await transfer.dispose()
  await page.getByRole('button', { name: '添加内容', exact: true }).click()
  await page.locator('.mention-popup').waitFor()
  const mention = await page.locator('.mention-popup').evaluate(element => ({ border: getComputedStyle(element).borderTopWidth, shadow: getComputedStyle(element).boxShadow }))
  check(mention.border === '0px' && mention.shadow === 'none', '暗色添加菜单仍有描边或阴影')
  await page.evaluate(async () => { window.composerReview.state.modal = true; await window.composerReview.nextTick() })
  await page.getByRole('dialog', { name: '背景分层', exact: true }).waitFor()
  await settle()
  check(await page.locator('.ant-modal-content').evaluateAll(elements => elements.every(element => getComputedStyle(element).boxShadow === 'none')), '暗色弹窗仍使用通用装饰阴影')
  check(failures.length === 0, failures.join('\n'))
  return { observations, mention, sends: await page.evaluate(() => window.composerReview.state.sends) }
}
