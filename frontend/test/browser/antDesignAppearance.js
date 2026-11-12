// 已加载 Vite 应用后执行：playwright-cli -s=<session> --raw run-code --filename=frontend/test/browser/antDesignAppearance.js
// prettier-ignore
async (page) => {
  /** 让实际 DOM 与独立预期不一致时明确失败。 */
  const check = (value, message) => { if (!value) throw Error(message) }
  const viewport = page.viewportSize()
  const reducedMotion = await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)
  const errors = []
  const onError = () => errors.push('浏览器异常')
  page.on('pageerror', onError)
  const results = {}

  /** 按 sRGB 相对亮度计算正文与背景的独立对比度。 */
  const contrast = (foreground, background) => {
    const luminance = value => {
      const channels = value.match(/[\d.]+/g).slice(0, 3).map(Number).map(channel => {
        const normalized = channel / 255
        return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4
      })
      return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
    }
    const first = luminance(foreground)
    const second = luminance(background)
    return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
  }

  /** 用实际指针悬停测量按钮，等待 CSS transition 收敛后读取颜色。 */
  const primaryContrast = async (selector) => {
    const samples = {}
    try {
      for (const state of ['normal', 'hover', 'active']) {
        if (state === 'normal') await page.mouse.move(0, 0)
        else await page.locator(selector).hover()
        if (state === 'active') await page.mouse.down()
        await page.waitForFunction(target => document.querySelector(target).getAnimations().every(animation => animation.playState !== 'running'), selector)
        const sample = await page.locator(selector).evaluate(el => {
          const style = getComputedStyle(el)
          return { color: style.color, background: style.backgroundColor }
        })
        sample.contrast = contrast(sample.color, sample.background)
        check(sample.contrast >= 4.5, `${selector} ${state} 文字对比度 ${sample.contrast.toFixed(2)} 低于 4.5`)
        samples[state] = sample
      }
      return samples
    } finally {
      // 移出按钮后释放，避免测量 active 时触发确认操作。
      await page.mouse.move(0, 0)
      await page.mouse.up()
    }
  }

  /** 普通按钮不能借装饰阴影表达层级。 */
  const checkButtonShadow = async () => {
    const shadow = await page.locator('#probe-button-1').evaluate(el => getComputedStyle(el).boxShadow)
    check(shadow === 'none' || shadow.endsWith('0px 0px 0px 0px'), '普通按钮仍有装饰阴影')
  }
  /** 无阴影暗色菜单依靠细描边与邻接表面区分。 */
  const checkDropdownEdge = async () => {
    const edge = await page.locator('.appearance-select-1').evaluate(el => { const s = getComputedStyle(el); return { width: s.outlineWidth, style: s.outlineStyle, color: s.outlineColor, background: s.backgroundColor } })
    check(edge.width === '1px' && edge.style === 'solid' && contrast(edge.color, edge.background) >= 1.5, '暗色菜单与相邻表面缺少可见边界')
  }

  try {
    // Pinia Store 的方法闭包不会随所有 HMR 修改重建，刷新后验证当前实现。
    await page.reload()
    await page.waitForFunction(() => document.querySelector('#app')?.__vue_app__)
    await page.setViewportSize({ width: 1280, height: 1000 })
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.evaluate(async () => {
      const main = await (await fetch('/src/app/main.js')).text()
      const imports = [...main.matchAll(/from\s+["']([^"']+)["']/g)].map(match => match[1])
      const vueUrl = imports.find(url => /\/vue\.js(?:\?|$)/.test(url))
      const antUrl = imports.find(url => /\/ant-design-vue\.js(?:\?|$)/.test(url))
      if (!vueUrl || !antUrl) throw Error('无法从 Vite 入口解析实际依赖')
      const { createApp, h, reactive, nextTick } = await import(vueUrl)
      const { ConfigProvider, Alert, Button, Input, Select, Table, Spin, Switch, Modal, message, notification } = await import(antUrl)
      const root = document.querySelector('#app').__vue_app__
      const AntEmpty = root.component('a-empty')
      if (!AntEmpty || AntEmpty.name !== 'YuxiEmpty') throw Error('应用未注册共享 a-empty')
      const { useThemeStore } = await import('/src/shared/model/theme.js')
      const { renderEmpty } = await import('/src/shared/ui/AntEmpty.js')
      const theme = useThemeStore(root.config.globalProperties.$pinia)
      const savedTheme = localStorage.getItem('theme')
      const savedDark = theme.isDark
      const state = reactive({ actions: 0, alertActions: 0, alertClosed: 0, modalOpen: false, switchChecked: false })
      const host = document.createElement('div')
      host.id = 'appearance-probe'
      host.style.cssText = 'position:fixed;inset:0;z-index:900;background:var(--gray-0);color:var(--gray-900);padding:24px;overflow:auto'
      document.body.append(host)

      /** 为合成场景提供轻量分区，不添加产品入口。 */
      const section = (title, children) => h('section', {
        style: 'min-width:0;border:1px solid var(--gray-150);border-radius:8px;padding:16px'
      }, [h('h3', { style: 'font-size:16px;font-weight:600;margin-bottom:16px' }, title), ...children])
      const sizes = ['small', undefined, 'large']
      const app = createApp({
        /** 始终使用真实 Store 当前主题，让浅深切换更新同一组件树。 */
        setup: () => () => h(ConfigProvider, { theme: theme.currentTheme, renderEmpty }, {
          default: () => [
            h('h2', { style: 'font-size:22px;font-weight:600;margin-bottom:20px' }, '共享组件外观验证'),
            h('div', { style: 'display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px' }, [
              section('按钮与状态', [
                h('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px' }, sizes.map((size, index) =>
                  h(Button, { size, id: `probe-button-${index}`, type: 'primary' }, () => ['小号', '默认', '大号'][index]))),
                h(Button, { id: 'probe-button-disabled', disabled: true }, () => '禁用操作'),
                h(Button, { id: 'probe-button-danger', danger: true, type: 'primary', style: 'margin-left:8px' }, () => '危险操作'),
                h('div', { style: 'margin-top:16px' }, [h(Switch, {
                  id: 'probe-switch', checked: state.switchChecked, onChange: value => state.switchChecked = value,
                  checkedChildren: '携带', unCheckedChildren: '关闭', 'aria-label': '示例开关'
                })])
              ]),
              section('输入框与焦点', [
                ...sizes.map((size, index) => h(Input, { size, id: `probe-input-${index}`, placeholder: '示例输入', style: 'margin-bottom:8px' })),
                h(Input, { id: 'probe-input-error', status: 'error', placeholder: '错误状态', style: 'margin-bottom:8px' }),
                h(Input, { id: 'probe-input-disabled', disabled: true, placeholder: '禁用输入' })
              ]),
              section('选择器', sizes.map((size, index) => h(Select, {
                size, id: `probe-select-${index}`, class: `appearance-select-control-${index}`, options: [], placeholder: '选择示例项目',
                popupClassName: `appearance-select-${index}`, style: 'width:100%;margin-bottom:8px'
              }))),
              section('默认空状态', [h(AntEmpty, { id: 'probe-empty', description: '暂无示例文件' }, {
                default: () => h(Button, { id: 'probe-empty-action', onClick: () => state.actions++ }, () => '添加示例')
              })]),
              section('显式内容保留', [
                h(AntEmpty, { id: 'probe-slot-empty' }, {
                  image: () => h('span', { id: 'probe-custom-image' }, '业务图像'),
                  description: () => h('strong', '请调整示例筛选'),
                  default: () => h(Button, { id: 'probe-slot-action', onClick: () => state.actions++ }, () => '清除筛选')
                }),
                h(AntEmpty, { id: 'probe-hidden-empty', image: false, description: false }, {
                  default: () => h('span', '显式隐藏图像与说明，保留操作区')
                })
              ]),
              section('表格默认空状态', [h(Table, {
                id: 'probe-table', columns: [{ title: '示例名称', dataIndex: 'name' }], dataSource: [], pagination: false
              })]),
              section('三档加载动画', [
                h('div', { style: 'display:flex;justify-content:space-around;align-items:center;min-height:56px' },
                  sizes.map((size, index) => h(Spin, { size, id: `probe-spin-${index}` }))),
                h('span', { id: 'probe-refresh-icon', class: 'yuxi-rotate', style: 'display:inline-block' }, '↻'),
                h('div', { class: 'page-shoulder' }, [h('span', { id: 'probe-shoulder-refresh', class: 'page-shoulder-refresh-icon is-spinning', style: 'display:inline-block' }, '↻')])
              ]),
              section('弹窗入口', [h(Button, { id: 'probe-modal-open', onClick: () => state.modalOpen = true }, () => '打开示例弹窗')])
            ]),
            h('section', { id: 'probe-alerts', style: 'margin-top:20px;padding:20px;border:1px solid var(--gray-150);border-radius:8px;background:var(--gray-0)' }, [
              h('h3', { style: 'font-size:18px;font-weight:600;margin-bottom:16px' }, '共享提醒外观验证'),
              h('div', { style: 'display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px' },
                ['success', 'info', 'warning', 'error'].map(type => h(Alert, {
                  id: `probe-alert-${type}`, type, showIcon: true,
                  message: { success: '操作成功', info: '提示信息', warning: '需要注意', error: '操作失败' }[type],
                  description: '示例说明保留可读性，状态通过图标和文字表达。'
                }))),
              h(Alert, { id: 'probe-alert-banner', banner: true, message: '示例横幅提醒', style: 'margin-top:12px' }),
              h(Alert, { id: 'probe-alert-no-icon', type: 'info', showIcon: false, message: '只展示示例文字', style: 'margin-top:12px' }),
              h(Alert, { id: 'probe-alert-slots', type: 'info', showIcon: true, style: 'margin-top:12px' }, {
                message: () => h('strong', { id: 'probe-alert-custom-message' }, '业务消息内容'),
                description: () => h('span', '业务补充说明'),
                action: () => h(Button, { id: 'probe-alert-action', size: 'small', onClick: () => state.alertActions++ }, () => '执行示例')
              }),
              h(Alert, { id: 'probe-alert-close', type: 'warning', message: '可以关闭示例提醒', closable: true,
                onClose: () => state.alertClosed++, style: 'margin-top:12px' })
            ]),
            h(Modal, {
              open: state.modalOpen, title: '共享基础弹窗', class: 'appearance-standard-modal',
              okText: '保存示例', cancelText: '取消', onOk: () => state.modalOpen = false,
              onCancel: () => state.modalOpen = false
            }, { default: () => '这是合成内容，用于验证基础弹窗外观。' })
          ]
        })
      })
      app.mount(host)

      /** 解析 CSS 变量的最终颜色，避免只比较未展开的字符串。 */
      const color = name => {
        const sample = document.createElement('span')
        sample.style.color = `var(${name})`
        host.append(sample)
        const value = getComputedStyle(sample).color
        sample.remove()
        return value
      }
      window.antAppearanceProbe = { app, host, theme, savedTheme, savedDark, state, nextTick, color, message, notification, Modal, confirm: null, ghost: null }
      theme.setTheme(false)
      await nextTick()
    })

    await page.locator('#probe-empty .lucide-inbox').waitFor()
    check(await page.locator('#probe-empty svg').getAttribute('aria-hidden') === 'true', '默认空图像未标记为装饰')
    check(await page.locator('#probe-table .yuxi-empty--default .lucide-inbox').count() === 1, 'Table 未使用共享默认空状态')
    check(await page.locator('#probe-slot-empty #probe-custom-image').count() === 1, '业务 image slot 被覆盖')
    check(await page.locator('#probe-slot-empty strong').textContent() === '请调整示例筛选', 'description slot 被覆盖')
    check(await page.locator('#probe-slot-empty .lucide-inbox').count() === 0, '自定义图像被默认图像覆盖')
    check(await page.locator('#probe-hidden-empty .ant-empty-description').count() === 0, 'description:false 被重新开启')
    check(await page.locator('#probe-hidden-empty .ant-empty-image').evaluate(el => getComputedStyle(el).display) === 'none', 'image:false 仍占据图像高度')
    check(await page.locator('#probe-empty .ant-empty-image').evaluate(el => el.getBoundingClientRect().height) === 32, '默认空图像未收敛为 32px')
    await page.locator('#probe-empty-action').click()
    await page.locator('#probe-slot-action').click()
    check(await page.evaluate(() => window.antAppearanceProbe.state.actions) === 2, '空状态动作丢失交互')

    for (const [index, expected] of [24, 32, 40].entries()) {
      for (const selector of [`#probe-button-${index}`, `#probe-input-${index}`, `.appearance-select-control-${index} .ant-select-selector`]) {
        const height = await page.locator(selector).evaluate(el => el.getBoundingClientRect().height)
        check(Math.abs(height - expected) < 1, `${selector} 尺寸未遵循 ${expected}px`)
      }
      check(await page.locator(`#probe-spin-${index} .yuxi-loading > div`).count() === 5, 'Spin 默认五线动画缺失')
    }
    check(await page.locator('#probe-button-disabled').isDisabled(), '按钮禁用行为丢失')
    check(await page.locator('#probe-button-1').evaluate(el => getComputedStyle(el).fontWeight) === '500', '按钮字重被晚注入样式覆盖')
    check(await page.locator('#probe-button-1 > span').last().evaluate(el => getComputedStyle(el).fontWeight) === '500', '按钮实际文字未继承字重')
    check(await page.locator('#probe-input-disabled').isDisabled(), '输入框禁用行为丢失')
    await page.locator('#probe-input-1').focus()
    check(await page.locator('#probe-input-1').evaluate(el => getComputedStyle(el).boxShadow !== 'none'), '输入框 focus ring 不可见')
    await page.waitForFunction(() => getComputedStyle(document.querySelector('#probe-input-error')).borderColor === window.antAppearanceProbe.color('--color-error-500'))
    check(await page.evaluate(() => getComputedStyle(document.querySelector('#probe-input-error')).borderColor === window.antAppearanceProbe.color('--color-error-500')), '错误输入边框未使用错误语义色')

    await page.locator('.appearance-select-control-1 .ant-select-selector').click()
    await page.locator('.appearance-select-1 .yuxi-empty--compact').waitFor()
    check(await page.locator('.appearance-select-1 .lucide-inbox').count() === 1, 'Select 默认空菜单未复用共享图像')
    await page.keyboard.press('Escape')
    await page.locator('#probe-modal-open').click()
    await page.locator('.appearance-standard-modal .ant-modal-content').waitFor()
    check(await page.locator('.appearance-standard-modal .ant-modal-content').evaluate(el => getComputedStyle(el).borderRadius) === '12px', '基础弹窗圆角未统一')
    check(await page.locator('.appearance-standard-modal .ant-modal-title').evaluate(el => getComputedStyle(el).fontSize) === '18px', '基础弹窗标题字号未统一')
    await page.locator('.appearance-standard-modal .ant-modal-footer').getByRole('button', { name: /取\s*消/ }).click()
    await page.locator('.appearance-standard-modal').waitFor({ state: 'hidden' })

    results.componentPrimaryContrast = {}
    results.dangerPrimaryContrast = {}
    results.switchContrast = {}
    results.alerts = {}
    for (const dark of [false, true]) {
      await page.evaluate(async value => {
        window.antAppearanceProbe.theme.setTheme(value)
        await window.antAppearanceProbe.nextTick()
      }, dark)
      await checkButtonShadow()
      await page.locator('.appearance-select-control-1 .ant-select-selector').click()
      const dropdown = page.locator('.appearance-select-1')
      await dropdown.waitFor()
      if (dark) {
        await checkDropdownEdge()
        const oldAppearance = await page.addStyleTag({ content: '#probe-button-1 { box-shadow:0 2px 0 #035065!important;transition:none!important }.appearance-select-1 { outline:none!important }' })
        let shadowRejected = false, edgeRejected = false
        try {
          try { await checkButtonShadow() } catch (error) { shadowRejected = error.message.includes('普通按钮仍有装饰阴影') }
          try { await checkDropdownEdge() } catch (error) { edgeRejected = error.message.includes('暗色菜单与相邻表面缺少可见边界') }
        } finally { await oldAppearance.evaluate(el => el.remove()) }
        check(shadowRejected && edgeRejected, '外观检查没有拒绝旧阴影或消失的菜单边界')
        await page.waitForFunction(() => document.querySelector('#probe-button-1').getAnimations().every(animation => animation.playState !== 'running'))
        await checkButtonShadow()
        await checkDropdownEdge()
        results.oldAppearanceRejected = true
      }
      await page.keyboard.press('Escape')
      results.componentPrimaryContrast[dark ? 'dark' : 'light'] = await primaryContrast('#probe-button-1')
      results.dangerPrimaryContrast[dark ? 'dark' : 'light'] = await primaryContrast('#probe-button-danger')
      const switches = {}
      for (const checked of [false, true]) {
        await page.evaluate(async value => {
          window.antAppearanceProbe.state.switchChecked = value
          await window.antAppearanceProbe.nextTick()
        }, checked)
        await page.waitForFunction(() => document.querySelector('#probe-switch').getAnimations().every(animation => animation.playState !== 'running'))
        const sample = await page.locator('#probe-switch').evaluate((el, value) => {
          const label = el.querySelector(`.ant-switch-inner-${value ? 'checked' : 'unchecked'}`)
          return { color: getComputedStyle(label).color, background: getComputedStyle(el).backgroundColor, label: label.textContent }
        }, checked)
        check(sample.label === (checked ? '携带' : '关闭'), 'Switch 当前状态文案丢失')
        sample.contrast = contrast(sample.color, sample.background)
        check(sample.contrast >= 4.5, `Switch ${checked ? 'checked' : 'unchecked'} 文字对比度 ${sample.contrast.toFixed(2)} 低于 4.5`)
        switches[checked ? 'checked' : 'unchecked'] = sample
      }
      results.switchContrast[dark ? 'dark' : 'light'] = switches
      await page.waitForFunction(() => {
        const probe = window.antAppearanceProbe
        const icons = { success: '--color-success-700', info: '--main-color', warning: '--color-warning-900', error: '--color-error-700' }
        return Object.entries(icons).every(([type, token]) => {
          const alert = document.querySelector(`#probe-alert-${type}`)
          return getComputedStyle(alert).backgroundColor === probe.color('--gray-25') &&
            getComputedStyle(alert.querySelector('.ant-alert-icon')).color === probe.color(token)
        })
      })
      const alerts = await page.evaluate(() => {
        const probe = window.antAppearanceProbe
        const icons = { success: '--color-success-700', info: '--main-color', warning: '--color-warning-900', error: '--color-error-700' }
        return Object.entries(icons).map(([type, token]) => {
          const alert = document.querySelector(`#probe-alert-${type}`)
          const style = getComputedStyle(alert)
          return {
            type, background: style.backgroundColor, border: style.borderTopColor, borderWidth: style.borderTopWidth, radius: style.borderRadius,
            icon: getComputedStyle(alert.querySelector('.ant-alert-icon')).color,
            message: getComputedStyle(alert.querySelector('.ant-alert-message')).color,
            description: getComputedStyle(alert.querySelector('.ant-alert-description')).color,
            expectedBackground: probe.color('--gray-25'), expectedBorder: probe.color('--gray-150'), expectedIcon: probe.color(token), expectedDescription: probe.color('--gray-600')
          }
        })
      })
      for (const alert of alerts) {
        check(alert.background === alert.expectedBackground && alert.border === alert.expectedBorder && alert.borderWidth === '1px', `${alert.type} Alert 表面未使用统一中性色`)
        check(alert.radius === '8px' && alert.icon === alert.expectedIcon, `${alert.type} Alert 圆角或语义图标不一致`)
        check(alert.description === alert.expectedDescription, `${alert.type} Alert 描述层级未统一`)
        alert.messageContrast = contrast(alert.message, alert.background)
        alert.descriptionContrast = contrast(alert.description, alert.background)
        check(alert.messageContrast >= 4.5 && alert.descriptionContrast >= 4.5, `${alert.type} Alert 正文或描述对比度低于 4.5`)
      }
      results.alerts[dark ? 'dark' : 'light'] = alerts
      const banner = await page.locator('#probe-alert-banner').evaluate(el => {
        const style = getComputedStyle(el)
        return { border: style.borderTopWidth, radius: style.borderRadius }
      })
      check(banner.border === '0px' && banner.radius === '0px', 'Alert banner 边框或圆角语义被覆盖')
      check(await page.locator('#probe-alert-banner .ant-alert-icon').count() === 1, 'Alert banner 缺省图标丢失')
      check(await page.locator('#probe-alert-no-icon .ant-alert-icon').count() === 0, 'Alert showIcon:false 被重新开启')
      check(await page.locator('#probe-alert-custom-message').textContent() === '业务消息内容', 'Alert message slot 被覆盖')
      check(await page.locator('#probe-alert-slots .ant-alert-description').textContent() === '业务补充说明', 'Alert description slot 被覆盖')
      await page.locator('#probe-alerts').screenshot({ path: dark ? '/tmp/yuxi-alerts-dark.png' : '/tmp/yuxi-alerts-light.png' })
    }
    await page.locator('#probe-alert-action').click()
    check(await page.evaluate(() => window.antAppearanceProbe.state.alertActions) === 1, 'Alert action slot 丢失点击行为')
    await page.locator('#probe-alert-close .ant-alert-close-icon').click()
    await page.locator('#probe-alert-close').waitFor({ state: 'hidden' })
    check(await page.evaluate(() => window.antAppearanceProbe.state.alertClosed) === 1, 'Alert close 事件未触发或重复触发')
    results.alertInterfaces = true
    await page.evaluate(async () => {
      window.antAppearanceProbe.theme.setTheme(false)
      await window.antAppearanceProbe.nextTick()
    })

    await page.evaluate(() => {
      const probe = window.antAppearanceProbe
      probe.message.success({ content: '共享提示样式已统一', duration: 0, key: 'appearance-message' })
      probe.notification.success({ message: '共享通知', description: '合成通知用于验证主题切换。', duration: 0, key: 'appearance-notification' })
      probe.confirm = probe.Modal.confirm({
        class: 'appearance-static-confirm', title: '共享确认弹窗', content: '确认按钮与取消按钮应适应当前主题。',
        okText: '确认示例', cancelText: '取消', width: 360, mask: false, style: { top: '540px' }
      })
    })
    await page.locator('.appearance-static-confirm .ant-modal-content').waitFor()
    await page.locator('.ant-message-success').filter({ hasText: '共享提示样式已统一' }).waitFor()
    await page.locator('.ant-notification-notice').filter({ hasText: '共享通知' }).waitFor()

    /** 在反馈已存在时切换主题，验证现有 DOM 持续响应 CSS 色板。 */
    const themeResult = async (dark) => {
      await page.evaluate(async value => {
        window.antAppearanceProbe.theme.setTheme(value)
        await window.antAppearanceProbe.nextTick()
      }, dark)
      await page.waitForFunction(value => {
        const probe = window.antAppearanceProbe
        const message = [...document.querySelectorAll('.ant-message-notice-content')].find(el => el.textContent.includes('共享提示样式已统一'))
        const input = document.querySelector('#probe-input-1')
        return document.documentElement.classList.contains('dark') === value && message &&
          getComputedStyle(message).backgroundColor === probe.color('--color-bg-elevated') &&
          getComputedStyle(input).backgroundColor === probe.color('--gray-0')
      }, dark)
      await page.waitForFunction(() => document.querySelector('.appearance-static-confirm .ant-btn-primary').getAnimations().every(animation => animation.playState !== 'running'))
      const styles = await page.evaluate(() => {
        const probe = window.antAppearanceProbe
        const message = [...document.querySelectorAll('.ant-message-notice-content')].find(el => el.textContent.includes('共享提示样式已统一'))
        const notification = [...document.querySelectorAll('.ant-notification-notice')].find(el => el.textContent.includes('共享通知'))
        const modal = document.querySelector('.appearance-static-confirm .ant-modal-content')
        const input = document.querySelector('#probe-input-1')
        return {
          elevated: probe.color('--color-bg-elevated'), container: probe.color('--gray-0'), text: probe.color('--gray-900'),
          message: getComputedStyle(message).backgroundColor, messageText: getComputedStyle(message).color,
          notification: getComputedStyle(notification).backgroundColor,
          modal: getComputedStyle(modal).backgroundColor, input: getComputedStyle(input).backgroundColor,
          confirmText: getComputedStyle(document.querySelector('.appearance-static-confirm .ant-modal-confirm-title')).color,
          primary: getComputedStyle(document.querySelector('.appearance-static-confirm .ant-btn-primary')).backgroundColor,
          primaryText: getComputedStyle(document.querySelector('.appearance-static-confirm .ant-btn-primary')).color,
          ordinaryPrimary: getComputedStyle(document.querySelector('#probe-button-1')).backgroundColor,
          ordinaryPrimaryText: getComputedStyle(document.querySelector('#probe-button-1')).color
        }
      })
      check(styles.message === styles.elevated && styles.notification === styles.elevated && styles.modal === styles.elevated, '静态反馈表面未跟随主题')
      check(styles.input === styles.container, '实际主题 Store 未更新组件树背景')
      check(styles.messageText === styles.text && styles.confirmText === styles.text, '静态反馈文字未跟随主题')
      check(styles.primary === styles.ordinaryPrimary && styles.primaryText === styles.ordinaryPrimaryText, '静态确认按钮与同主题普通按钮不一致')
      styles.primaryContrast = await primaryContrast('.appearance-static-confirm .ant-btn-primary')
      await page.evaluate(() => {
        const probe = window.antAppearanceProbe
        probe.ghost = probe.Modal.confirm({
          class: 'appearance-ghost-confirm', title: '透明按钮示例', content: '保留原有 ghost 语义。',
          okText: '透明按钮', cancelText: '取消', okButtonProps: { ghost: true }
        })
      })
      await page.locator('.appearance-ghost-confirm .ant-btn-primary').waitFor()
      styles.ghostBackground = await page.locator('.appearance-ghost-confirm .ant-btn-primary').evaluate(el => getComputedStyle(el).backgroundColor)
      check(styles.ghostBackground === 'rgba(0, 0, 0, 0)', '静态确认 ghost 按钮被填充背景')
      await page.evaluate(() => window.antAppearanceProbe.ghost.destroy())
      await page.locator('.appearance-ghost-confirm').waitFor({ state: 'hidden' })
      return styles
    }
    results.light = await themeResult(false)
    await page.screenshot({ path: '/tmp/yuxi-ui-light.png' })
    results.dark = await themeResult(true)
    check(results.light.elevated !== results.dark.elevated, '浅深主题表面颜色没有变化')
    await page.screenshot({ path: '/tmp/yuxi-ui-dark.png' })
    const lightAgain = await themeResult(false)
    check(lightAgain.elevated === results.light.elevated && lightAgain.input === results.light.input, '深色切回浅色未还原')

    await page.emulateMedia({ reducedMotion: 'reduce' })
    check(await page.locator('#probe-spin-1 .yuxi-loading > div').first().evaluate(el => getComputedStyle(el).animationName) === 'none', '减少动态效果未关闭加载动画')
    for (const selector of ['#probe-refresh-icon', '#probe-shoulder-refresh']) {
      check(await page.locator(selector).evaluate(el => getComputedStyle(el).animationName) === 'none', '减少动态效果未关闭刷新旋转')
    }
    await page.evaluate(() => window.antAppearanceProbe.confirm.destroy())
    await page.locator('.appearance-static-confirm').waitFor({ state: 'hidden' })
    await page.setViewportSize({ width: 375, height: 900 })
    check(await page.locator('#appearance-probe').evaluate(el => el.scrollWidth <= el.clientWidth), '窄屏共享组件产生横向溢出')
    await page.locator('#appearance-probe').evaluate(el => el.scrollTop = 0)
    await page.screenshot({ path: '/tmp/yuxi-ui-mobile.png' })
    results.emptyInterfaces = true
    results.controlHeights = [24, 32, 40]
    results.disabledErrorFocus = true
    results.staticFeedbackTheme = true
    results.reducedMotion = true
    results.mobileWidth = 375
    results.browserErrors = errors.length
    check(errors.length === 0, '共享组件验证期间出现浏览器异常')
    return results
  } finally {
    await page.evaluate(async () => {
      const probe = window.antAppearanceProbe
      if (!probe) return
      probe.message.destroy('appearance-message')
      probe.notification.close('appearance-notification')
      probe.confirm?.destroy()
      probe.ghost?.destroy()
      probe.app.unmount()
      probe.host.remove()
      probe.theme.setTheme(probe.savedDark)
      if (probe.savedTheme === null) localStorage.removeItem('theme')
      else localStorage.setItem('theme', probe.savedTheme)
      await probe.nextTick()
      delete window.antAppearanceProbe
    })
    await page.emulateMedia({ reducedMotion: reducedMotion ? 'reduce' : 'no-preference' })
    if (viewport) await page.setViewportSize(viewport)
    page.off('pageerror', onError)
  }
}
