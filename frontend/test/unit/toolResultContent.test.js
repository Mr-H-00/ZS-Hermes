import assert from 'node:assert/strict'
import test from 'node:test'
import {
  getToolCallResultContent,
  getToolResultParts,
  parseToolCallResult,
  unwrapToolResultContent
} from '../../src/modules/session/model/toolCallProjection.js'

test('正文提取保留实时优先级，结构解析继续保留零、false 与失败契约', () => {
  for (const content of [0, false, { rows: [1, 2] }, '普通文本', '']) {
    const call = { tool_call_result: { content }, result: '旧结果' }
    assert.equal(getToolCallResultContent(call), content)
  }
  assert.equal(getToolCallResultContent({ result: '历史结果' }), '历史结果')
  assert.equal(parseToolCallResult({ result: '普通文本' }), null)
  assert.equal(parseToolCallResult({ result: '' }), null)
  assert.equal(parseToolCallResult({ result: '0' }), 0)
  assert.equal(parseToolCallResult({ result: false }), false)
  assert.deepEqual(parseToolCallResult({ result: '{"rows":[1,2]}' }), { rows: [1, 2] })
})

test('图片块共享同一来源规则，文本和未知块保留原顺序', () => {
  const source = 'data:image/png;base64,cGljdHVyZQ=='
  for (const image of [
    { type: 'image', base64: 'cGljdHVyZQ==', mime_type: 'image/png' },
    { type: 'image', data: 'cGljdHVyZQ==', mimeType: 'image/png' },
    { type: 'image_url', image_url: { url: source } },
    { type: 'input_image', image_url: source }
  ]) {
    const content = [{ type: 'input_text', text: '开始' }, image, { other: '保留' }]
    const expected = [
      { text: '开始' },
      { image: true, src: source },
      { text: '{\n  "other": "保留"\n}' }
    ]
    assert.deepEqual(getToolResultParts(content), expected)
    assert.deepEqual(getToolResultParts(JSON.stringify(content)), expected)
  }
})

test('裸图片 URL 仅在生成图片工具启用，通用结果保留普通链接和对象', () => {
  for (const source of [
    'https://example.com/image.png',
    '/api/v1/agents/sessions/example/artifacts/image.png'
  ]) {
    assert.deepEqual(getToolResultParts(source), [{ text: source }])
    assert.deepEqual(getToolResultParts(source, { imageUrls: true }), [
      { image: true, src: source }
    ])
  }
  for (const content of [0, false, null, { rows: [1, 2] }, [1, 2]]) {
    const text = typeof content === 'object' ? JSON.stringify(content, null, 2) : String(content)
    assert.deepEqual(getToolResultParts(content), [{ text }])
  }
})

test('无效图片来源显示失败占位，编码和危险协议不进入文本', () => {
  for (const image of [
    { type: 'image', base64: 'private-payload' },
    { type: 'image_url', image_url: { url: 'javascript:alert(1)' } },
    { type: 'input_image', image_url: '//outside.example/image.png' },
    { type: 'image', url: 'file:///tmp/image.png' }
  ]) {
    assert.deepEqual(getToolResultParts([image]), [{ image: true, src: '' }])
  }
})

test('MCP 文本信封解包为内嵌 JSON，恢复知识库工具被误判为空结果的结构', () => {
  const kbs = [{ kb_id: 'kb_1', name: '演示库', description: '全部为虚构演示数据' }]
  // 实时 MCP 形态：type=text；持久化形态：type=input_text
  for (const type of ['text', 'input_text']) {
    const envelope = [{ type, text: JSON.stringify(kbs) }]
    assert.deepEqual(unwrapToolResultContent(envelope), kbs)
    assert.deepEqual(unwrapToolResultContent(JSON.stringify(envelope)), kbs)
  }
  // 内嵌对象解析为结构化结果，普通文本信封保持字符串
  assert.deepEqual(unwrapToolResultContent([{ type: 'text', text: '{"results":[1]}' }]), {
    results: [1]
  })
  assert.equal(unwrapToolResultContent([{ type: 'text', text: '仅提示文本' }]), '仅提示文本')
  // 多个文本块按顺序解析，保留各自的解析结果
  assert.deepEqual(
    unwrapToolResultContent([{ type: 'text', text: '[1]' }, { type: 'input_text', text: '"b"' }]),
    [[1], 'b']
  )
  // 非信封数组与已结构化数据不被误改
  const rows = [{ rows: [1, 2] }, { other: '保留' }]
  assert.deepEqual(unwrapToolResultContent(rows), rows)
  assert.deepEqual(unwrapToolResultContent({ kb_id: 'kb_1' }), { kb_id: 'kb_1' })
})
