import assert from 'node:assert/strict'
import test from 'node:test'

import {
  parseBool,
  normalizeOptions,
  normalizeQuestions,
  DEFAULT_OTHER_OPTION_VALUE
} from '../../src/utils/questionUtils.js'

test('parseBool 能够正确解析字符串、数值和布尔值', () => {
  assert.equal(parseBool(true), true)
  assert.equal(parseBool(false), false)
  assert.equal(parseBool('true'), true)
  assert.equal(parseBool('false'), false)
  assert.equal(parseBool('0'), false)
  assert.equal(parseBool('1'), true)
  assert.equal(parseBool(undefined, true), true)
  assert.equal(parseBool(null, false), false)
})

test('normalizeOptions 能够处理 item 包装对象并保留 description', () => {
  const raw = {
    item: [
      {
        label: '给公司内部决策用的战略建议 (Recommended)',
        value: 'strategy',
        description: '结论面向战略建议'
      },
      {
        label: '行业 / 产品趋势分析报告',
        value: 'industry',
        description: '结论面向行业读者'
      }
    ]
  }

  const result = normalizeOptions(raw)
  assert.equal(result.length, 2)
  assert.equal(result[0].label, '给公司内部决策用的战略建议 (Recommended)')
  assert.equal(result[0].value, 'strategy')
  assert.equal(result[0].description, '结论面向战略建议')
  assert.equal(result[1].value, 'industry')
})

test('normalizeQuestions 能够正确解析复杂嵌套提问及字符串 multi_select', () => {
  const userCaseQuestions = [
    {
      question: '本次调研分析的最终落点是什么？',
      options: {
        item: [
          {
            label: '给公司内部决策用的战略建议 (Recommended)',
            value: 'strategy',
            description: '战略建议说明'
          },
          {
            label: '行业 / 产品趋势分析报告',
            value: 'industry',
            description: '行业报告说明'
          }
        ]
      },
      multi_select: 'false',
      question_id: 'final_deliverable'
    },
    {
      question: '对接目标系统的范围，以哪个为主？',
      options: {
        item: [
          { label: 'IM', value: 'im' },
          { label: 'OA', value: 'oa' }
        ]
      },
      multi_select: 'true',
      question_id: 'target_systems'
    }
  ]

  const result = normalizeQuestions(userCaseQuestions)
  assert.equal(result.length, 2)

  // 问题 1：单选，包含选项描述和其他选项
  const q1 = result[0]
  assert.equal(q1.questionId, 'final_deliverable')
  assert.equal(q1.multiSelect, false)
  assert.equal(q1.allowOther, true)
  assert.equal(q1.options.length, 3) // 2 个业务选项 + 1 个自动追加的其他
  assert.equal(q1.options[0].label, '给公司内部决策用的战略建议 (Recommended)')
  assert.equal(q1.options[0].value, 'strategy')
  assert.equal(q1.options[0].description, '战略建议说明')
  assert.equal(q1.options[2].value, DEFAULT_OTHER_OPTION_VALUE)

  // 问题 2：多选
  const q2 = result[1]
  assert.equal(q2.questionId, 'target_systems')
  assert.equal(q2.multiSelect, true)
  assert.equal(q2.options[0].value, 'im')
})
