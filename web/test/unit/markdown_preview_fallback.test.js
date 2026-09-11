import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createMarkdownRenderer,
  normalizeLegacyMinioPublicUrls
} from '../../src/utils/markdown_preview.js'

test('无代码高亮器时 Markdown 仍保留结构化渲染', () => {
  const renderer = createMarkdownRenderer({ themeName: 'github-light', highlighter: null })
  const html = renderer.render('# Skill\n\n```python\nprint(42)\n```')
  assert.match(html, /<h1>Skill<\/h1>/)
  assert.match(html, /<pre><code/)
})

test('历史 MinIO 公共链接转换为同源代理且不改写外部链接', () => {
  const markdown = [
    '![legacy](http://old-host:9000/public/pages/a.png?version=1)',
    '![external](https://cdn.example.test/public/pages/b.png)'
  ].join('\n')

  assert.equal(
    normalizeLegacyMinioPublicUrls(markdown),
    [
      '![legacy](/minio/public/pages/a.png?version=1)',
      '![external](https://cdn.example.test/public/pages/b.png)'
    ].join('\n')
  )
})
