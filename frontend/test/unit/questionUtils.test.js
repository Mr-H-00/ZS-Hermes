import assert from 'node:assert/strict'
import test from 'node:test'

import {
  mapQuestionsForDisplay,
  isQuestionAnswered,
  buildQuestionAnswer
} from '../../src/modules/session/model/questionUtils.js'

test('标准问题只映射显示字段，保留后端 ID 和选项描述', () => {
  const questions = [
    {
      question_id: 'server-generated-id',
      question: '选择交付物',
      options: [{ label: '战略建议', value: 'strategy', description: '面向决策' }],
      multi_select: false,
      allow_other: false,
      operation: '确认交付物'
    },
    {
      question_id: 'target_systems',
      question: '选择系统',
      options: [
        { label: 'IM', value: 'im' },
        { label: 'OA', value: 'oa' }
      ],
      multi_select: true,
      allow_other: true
    }
  ]
  const result = mapQuestionsForDisplay(questions)
  assert.deepEqual(result[0], {
    questionId: 'server-generated-id',
    question: '选择交付物',
    options: questions[0].options,
    multiSelect: false,
    allowOther: false,
    operation: '确认交付物',
    answerMode: 'single'
  })
  assert.equal(result[1].questionId, 'target_systems')
  assert.equal(result[1].answerMode, 'multiple')
  assert.equal(result[1].allowOther, true)
})

test('显示映射不解析 JSON、包装对象、字段别名或生成回答 ID', () => {
  assert.deepEqual(mapQuestionsForDisplay('[{"question":"问题"}]'), [])
  assert.deepEqual(mapQuestionsForDisplay({ items: [{ question: '问题' }] }), [])
  assert.deepEqual(mapQuestionsForDisplay([{ title: '问题' }]), [])
  const [rawToolQuestion] = mapQuestionsForDisplay([{ question: '问题' }])
  assert.equal(rawToolQuestion.questionId, undefined)
})

test('标准纯问答收集去除首尾空白的文本', () => {
  const [question] = mapQuestionsForDisplay([
    {
      question_id: 'destination',
      question: '你想去哪个城市？',
      options: [],
      multi_select: false,
      allow_other: true
    }
  ])
  assert.equal(question.answerMode, 'text')
  assert.deepEqual(question.options, [])
  assert.equal(isQuestionAnswered(question, [], '  杭州  '), true)
  assert.equal(isQuestionAnswered(question, [], '   '), false)
  assert.equal(buildQuestionAnswer(question, [], '  杭州  '), '杭州')
})

test('选择题答案保持单选、多选和自行填写格式', () => {
  const [single, multiple] = mapQuestionsForDisplay([
    {
      question_id: 'season',
      question: '季节？',
      options: [
        { label: '春天', value: '春天' },
        { label: '夏天', value: '夏天' }
      ],
      multi_select: false,
      allow_other: true
    },
    {
      question_id: 'cities',
      question: '城市？',
      options: [
        { label: '杭州', value: '杭州' },
        { label: '上海', value: '上海' }
      ],
      multi_select: true,
      allow_other: true
    }
  ])
  assert.equal(buildQuestionAnswer(single, ['春天']), '春天')
  assert.deepEqual(buildQuestionAnswer(multiple, ['杭州', '上海']), ['杭州', '上海'])
  assert.deepEqual(buildQuestionAnswer(single, [], '  秋天  ', true), {
    type: 'other',
    text: '秋天',
    selected: []
  })
  assert.deepEqual(buildQuestionAnswer(multiple, ['杭州'], '  苏州  ', true), {
    type: 'other',
    text: '苏州',
    selected: ['杭州']
  })
  assert.equal(
    buildQuestionAnswer({ ...single, allowOther: false }, [], '补充说明', true),
    undefined
  )
})
