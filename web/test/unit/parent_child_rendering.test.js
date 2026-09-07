import assert from 'node:assert/strict'
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { pid } from 'node:process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'

import { compileTemplate, parse } from 'vue/compiler-sfc'
import { createRenderer, h } from 'vue'

function createHostNode(type) {
  return { type, props: {}, children: [], parent: null, text: '' }
}

const renderer = createRenderer({
  createElement: createHostNode,
  createText(text) {
    const node = createHostNode('text')
    node.text = text
    return node
  },
  createComment(text) {
    const node = createHostNode('comment')
    node.text = text
    return node
  },
  insert(child, parent, anchor = null) {
    child.parent = parent
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index >= 0) parent.children.splice(index, 0, child)
    else parent.children.push(child)
  },
  remove(child) {
    const index = child.parent?.children.indexOf(child) ?? -1
    if (index >= 0) child.parent.children.splice(index, 1)
  },
  setText(node, text) {
    node.text = text
  },
  setElementText(node, text) {
    node.text = text
    node.children = []
  },
  parentNode(node) {
    return node.parent
  },
  nextSibling(node) {
    const siblings = node.parent?.children || []
    return siblings[siblings.indexOf(node) + 1] || null
  },
  patchProp(node, key, _previous, value) {
    node.props[key] = value
  }
})

function extractDiv(source, marker) {
  const markerIndex = source.indexOf(marker)
  assert.notEqual(markerIndex, -1, `未找到模板标记: ${marker}`)

  const start = source.lastIndexOf('<div', markerIndex)
  const divTags = /<div\b[^>]*>|<\/div>/g
  divTags.lastIndex = start
  let depth = 0
  let match

  while ((match = divTags.exec(source))) {
    depth += match[0].startsWith('</') ? -1 : 1
    if (depth === 0) return source.slice(start, divTags.lastIndex)
  }

  throw new Error(`模板节点未闭合: ${marker}`)
}

async function compileFragment(componentPath, marker, name) {
  const source = readFileSync(new URL(componentPath, import.meta.url), 'utf8')
  const { descriptor } = parse(source)
  const fragment = extractDiv(descriptor.template.content, marker)
  const compiled = compileTemplate({
    source: fragment,
    filename: componentPath,
    id: `parent-child-${name}`
  })
  assert.equal(compiled.errors.length, 0)

  const compiledPath = fileURLToPath(
    new URL(`../../.parent-child-${name}-test-${pid}.mjs`, import.meta.url)
  )
  writeFileSync(compiledPath, compiled.code)
  try {
    return await import(`${pathToFileURL(compiledPath).href}?test=${Date.now()}`)
  } finally {
    unlinkSync(compiledPath)
  }
}

function mountRender(render, context) {
  const container = createHostNode('root')
  const app = renderer.createApp({
    setup() {
      return () => h({ render, setup: () => context })
    }
  })
  app.mount(container)
  return { app, container }
}

function findNodes(node, predicate, result = []) {
  if (predicate(node)) result.push(node)
  for (const child of node.children || []) findNodes(child, predicate, result)
  return result
}

function hasClass(node, className) {
  const classes = Array.isArray(node.props.class) ? node.props.class : [node.props.class]
  return classes.filter(Boolean).flatMap((value) => String(value).split(/\s+/)).includes(className)
}

function getText(node) {
  return [node.text, ...(node.children || []).map(getText)].filter(Boolean).join(' ')
}

test('知识库配置复用可编辑 Parent-Child 控件并明确旧文件需显式重切', () => {
  const source = readFileSync(
    new URL('../../src/views/DataBaseInfoView.vue', import.meta.url),
    'utf8'
  )

  assert.match(source, /<IndexingFeaturesConfig/)
  assert.match(source, /:params="editForm"/)
  assert.match(source, /已有文件需要在文件菜单中显式选择“重新切片”/)
  assert.match(source, /validateParentChildConfig\(editForm\)/)
})

test('检索结果展示 Parent-Child 子块命中标识、分数、索引和偏移', async () => {
  const { render } = await compileFragment(
    '../../src/components/QuerySection.vue',
    'class="result-item"',
    'query-result'
  )
  const { app, container } = mountRender(render, {
    queryResult: [
      {
        content: '父块正文',
        metadata: { result_type: 'parent_child_parent', source: 'guide.md' },
        child_hits: [
          {
            child_id: 'child-001',
            chunk_index: 0,
            score: 0,
            start_offset: 0,
            end_offset: 42
          },
          {
            child_id: 'child-002',
            chunk_index: 3,
            score: 0.87654,
            start_offset: 43,
            end_offset: 88
          }
        ]
      }
    ]
  })

  const hits = findNodes(container, (node) => hasClass(node, 'parent-child-hits'))[0]
  assert.ok(hits)
  assert.match(getText(hits), /子块命中/)
  assert.match(getText(hits), /child-001\s+#0\s+0\.0000\s+0-42/)
  assert.match(getText(hits), /child-002\s+#3\s+0\.8765\s+43-88/)
  app.unmount()
})

test('旧版检索结果缺少 Parent-Child 字段时继续展示原有正文和元数据', async () => {
  const { render } = await compileFragment(
    '../../src/components/QuerySection.vue',
    'class="result-item"',
    'query-result-legacy'
  )
  const { app, container } = mountRender(render, {
    queryResult: [
      {
        content: '旧版块正文',
        score: 0.75,
        distance: 0,
        metadata: { source: 'legacy.md', file_id: 'file-legacy', chunk_index: 0 }
      }
    ]
  })

  const result = findNodes(container, (node) => hasClass(node, 'result-item'))[0]
  assert.ok(result)
  assert.match(getText(result), /旧版块正文/)
  assert.match(getText(result), /相似度:\s+75\.00%/)
  assert.match(getText(result), /来源:\s+legacy\.md/)
  assert.match(getText(result), /文件ID:\s+file-legacy/)
  assert.match(getText(result), /块索引:\s+0/)
  assert.match(getText(result), /距离:\s+0\.0000/)
  assert.equal(findNodes(result, (node) => hasClass(node, 'parent-child-hits')).length, 0)
  app.unmount()
})
