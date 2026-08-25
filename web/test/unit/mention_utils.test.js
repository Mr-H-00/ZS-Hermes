import assert from 'node:assert/strict'
import test, { after, before } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

let mentionUtils
let server

before(async () => {
  server = await createServer({
    root: fileURLToPath(new URL('../..', import.meta.url)),
    server: { middlewareMode: true },
    appType: 'custom'
  })
  mentionUtils = await server.ssrLoadModule('/src/utils/mention_utils.js')
})

after(async () => {
  await server?.close()
})

test('删除无空格相邻正文前的 mention 时使用 chip 边界', () => {
  const { expandMentionDeletionRange, parseMentionText } = mentionUtils
  const text = '@knowledge:示例知识库这个知识库有什么作用'
  const mentionEnd = '@knowledge:示例知识库'.length

  assert.deepEqual(parseMentionText(text), [
    {
      kind: 'mention',
      raw: text,
      type: 'knowledge',
      value: '示例知识库这个知识库有什么作用',
      start: 0,
      end: text.length
    }
  ])
  assert.deepEqual(
    expandMentionDeletionRange(text, mentionEnd, mentionEnd, 'backward', [
      { start: 0, end: mentionEnd }
    ]),
    { start: 0, end: mentionEnd }
  )
})

test('没有 chip 边界时仍按文本 mention 解析删除', () => {
  const { expandMentionDeletionRange } = mentionUtils
  const text = '@knowledge:"示例知识库" 后文'
  const mentionEnd = '@knowledge:"示例知识库"'.length

  assert.deepEqual(expandMentionDeletionRange(text, mentionEnd, mentionEnd), {
    start: 0,
    end: mentionEnd
  })
})
