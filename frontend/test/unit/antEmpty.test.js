import assert from 'node:assert/strict'
import test from 'node:test'
import { createSSRApp, h, resolveComponent } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { ConfigProvider, Empty, Table } from 'ant-design-vue'
import AntEmpty, { renderEmpty } from '../../src/shared/ui/AntEmpty.js'

/** 通过真实 Empty 渲染检查可见内容和透传属性。 */
function render(props = {}, slots) {
  return renderToString(createSSRApp({ render: () => h(AntEmpty, props, slots) }))
}

test('默认空状态替换图像，保留说明、动作和调用方属性', async () => {
  const html = await render(
    { description: '暂无文件，上传后显示', class: 'file-empty', 'data-owner': 'files' },
    { default: () => h('button', '上传文件') }
  )

  assert.match(html, /class="[^"]*ant-empty[^"]*yuxi-empty--default[^"]*file-empty/)
  assert.match(html, /data-owner="files"/)
  assert.match(html, /<svg[^>]*aria-hidden="true"/)
  assert.match(html, /lucide-inbox/)
  assert.match(html, /暂无文件，上传后显示/)
  assert.match(html, /ant-empty-footer[^]*<button>上传文件<\/button>/)
})

test('显式禁用图像或说明不会被默认内容重新打开', async () => {
  const html = await render({ image: false, description: false })

  assert.match(html, /yuxi-empty--no-image/)
  assert.doesNotMatch(html, /<svg|<img|ant-empty-description|yuxi-empty--default/)
})

test('自定义 URL 图像、图像样式和函数图像保留原接口', async () => {
  const imageHtml = await render({
    image: '/assets/empty-example.svg',
    imageStyle: { height: '48px' },
    description: '没有匹配结果'
  })
  assert.match(imageHtml, /class="ant-empty-image" style="height:48px;"/)
  assert.match(imageHtml, /<img alt="没有匹配结果" src="\/assets\/empty-example.svg"/)
  assert.doesNotMatch(imageHtml, /lucide-inbox|yuxi-empty--default/)

  const functionHtml = await render({ image: () => h('span', { id: 'custom-image' }, '自定义图像') })
  assert.match(functionHtml, /id="custom-image"[^>]*>自定义图像/)
  assert.doesNotMatch(functionHtml, /lucide-inbox|yuxi-empty--default/)

  const vnodeHtml = await render({ image: h('span', { id: 'vnode-image' }, '原有图像') })
  assert.match(vnodeHtml, /id="vnode-image"[^>]*>原有图像/)
  assert.doesNotMatch(vnodeHtml, /lucide-inbox|yuxi-empty--default/)
})

test('自定义 image、description 和动作 slots 完整保留，image prop 仍优先', async () => {
  const slots = {
    image: () => h('span', { id: 'slot-image' }, '业务图像'),
    description: () => h('strong', '请调整筛选'),
    default: () => h('button', '清除筛选')
  }
  const html = await render({}, slots)
  assert.match(html, /id="slot-image"[^>]*>业务图像/)
  assert.match(html, /<strong>请调整筛选<\/strong>/)
  assert.match(html, /<button>清除筛选<\/button>/)
  assert.doesNotMatch(html, /lucide-inbox|yuxi-empty--default/)

  const propHtml = await render({ image: false, description: false }, slots)
  assert.match(propHtml, /<button>清除筛选<\/button>/)
  assert.doesNotMatch(propHtml, /slot-image|请调整筛选|lucide-inbox|ant-empty-description/)
})

test('ConfigProvider 默认空表采用共享图像，业务 emptyText 仍优先', async () => {
  const renderTable = (locale) =>
    renderToString(
      createSSRApp({
        render: () =>
          h(ConfigProvider, { renderEmpty, locale: { Empty: { description: '暂无记录' } } }, {
            default: () => h(Table, {
              columns: [{ title: '文件', dataIndex: 'name' }],
              dataSource: [],
              pagination: false,
              locale
            })
          })
      })
    )

  const html = await renderTable()
  assert.match(html, /yuxi-empty--default/)
  assert.match(html, /lucide-inbox/)
  assert.match(html, /暂无记录/)
  const customHtml = await renderTable({ emptyText: '尚未同步文件' })
  assert.match(customHtml, /尚未同步文件/)
  assert.doesNotMatch(customHtml, /yuxi-empty|lucide-inbox/)
})

test('下拉菜单使用紧凑空状态，现有 a-empty 标签可从同一入口替换', async () => {
  for (const name of ['Select', 'TreeSelect', 'Cascader', 'Transfer', 'Mentions']) {
    const html = await renderToString(createSSRApp({ render: () => renderEmpty(name) }))
    assert.match(html, /yuxi-empty--compact/)
    assert.match(html, /lucide-inbox/)
  }
  const app = createSSRApp({
    render: () => h(resolveComponent('a-empty'), { description: '暂无项目' })
  })
  app.component('AEmpty', Empty)
  app.component('a-empty', AntEmpty)
  const html = await renderToString(app)
  assert.match(html, /lucide-inbox/)
  assert.match(html, /暂无项目/)
})
