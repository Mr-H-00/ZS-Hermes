import assert from 'node:assert/strict'
import test from 'node:test'
import { mountWorkspaceView } from '../helpers/workspaceView.js'

test('工作区消费文件打开参数，保留其他参数且后续可再次请求同一文件', async (t) => {
  const path = '/agents/MEMORY.md'
  const { state, route, replacements } = await mountWorkspaceView(t, { path })
  route.query = { open: path, other: 'retained' }
  await state.openFileByPath(path)
  assert.equal(state.selectedPreviewPath.value, path)
  assert.deepEqual(route.query, { other: 'retained' })
  state.closePreview()
  route.query.open = path
  await state.openFileByPath(path)
  assert.equal(state.selectedPreviewPath.value, path)
  assert.equal(replacements.length, 2)

  route.query.open = '/agents/USER.md'
  await state.openFileByPath(path)
  assert.equal(route.query.open, '/agents/USER.md', '不消费更新后的另一个打开请求')
  assert.equal(replacements.length, 2)
})
