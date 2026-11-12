/** 将标准问题字段映射为组件显示模型，不解析兼容格式或生成回答 ID。 */
export const mapQuestionsForDisplay = (questions) => {
  if (!Array.isArray(questions)) return []

  return questions
    .filter((item) => item && typeof item.question === 'string')
    .map((item) => {
      const options = Array.isArray(item.options) ? item.options : []
      const multiSelect = item.multi_select === true
      let answerMode = 'text'
      if (options.length) answerMode = multiSelect ? 'multiple' : 'single'
      return {
        questionId: item.question_id,
        question: item.question,
        options,
        multiSelect,
        allowOther: item.allow_other !== false,
        operation: item.operation || '',
        answerMode
      }
    })
}

/** 判断问题是否已有可提交答案。 */
export const isQuestionAnswered = (
  question,
  selectedValues = [],
  text = '',
  customAnswer = false
) => {
  const normalizedText = String(text || '').trim()
  if (question?.answerMode === 'text') return Boolean(normalizedText)
  if (question?.allowOther && customAnswer) return Boolean(normalizedText)
  return Array.isArray(selectedValues) && selectedValues.length > 0
}

/** 构建单题的 resume 答案。 */
export const buildQuestionAnswer = (
  question,
  selectedValues = [],
  text = '',
  customAnswer = false
) => {
  const normalizedText = String(text || '').trim()
  if (question?.answerMode === 'text') return normalizedText

  const selected = Array.isArray(selectedValues) ? selectedValues : []
  if (question?.allowOther && customAnswer) {
    return {
      type: 'other',
      text: normalizedText,
      selected
    }
  }
  return question?.multiSelect ? selected : selected[0]
}
