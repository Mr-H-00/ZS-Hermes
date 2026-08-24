import assert from 'node:assert/strict'
import test from 'node:test'

import { formatJsonKey, formatJsonScalar } from '../../src/utils/jsonTree.js'

test('JSON 树转义字符串值中的引号、反斜杠与换行', () => {
  assert.equal(formatJsonScalar('a"b\\c\nnext'), '"a\\"b\\\\c\\nnext"')
})

test('JSON 树按 JSON 语法转义对象键名', () => {
  assert.equal(formatJsonKey('a"b'), '"a\\"b"')
})

test('JSON 树保持非字符串标量格式', () => {
  assert.equal(formatJsonScalar(null), 'null')
  assert.equal(formatJsonScalar(undefined), 'undefined')
  assert.equal(formatJsonScalar(true), 'true')
  assert.equal(formatJsonScalar(12), '12')
})
