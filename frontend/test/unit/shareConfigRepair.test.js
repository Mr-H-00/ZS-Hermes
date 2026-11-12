import assert from 'node:assert/strict'
import test from 'node:test'
import { reactive } from 'vue'
import { cloneShareConfig, getShareConfigLabel } from '../../src/modules/agents/model/shareConfig.js'

for (const broken of [
  { version: 2, read_scope: { access_level: false } },
  { version: 2, read_scope: { access_level: 'department', department_ids: { 1: true } } },
  { version: 2, manage_scope: { access_level: 'user', user_uids: 1 } }
]) {
  test(`损坏配置暂存为仅所有者，不修改原值：${JSON.stringify(broken)}`, () => {
    const source = reactive(broken)
    const before = JSON.stringify(source)
    const staged = cloneShareConfig(source, true)
    assert.deepEqual(staged, { version: 2, read_scope: null, manage_scope: null })
    assert.equal(getShareConfigLabel(source, true), '共享配置无效')
    assert.equal(JSON.stringify(source), before)
    staged.read_scope = { access_level: 'global', department_ids: [], user_uids: [] }
    assert.equal(JSON.stringify(source), before)
  })
}

test('合法共享配置在 Vue 响应式表单中独立复制，保留共享标签', () => {
  const source = reactive({
    version: 2,
    read_scope: { access_level: 'department', department_ids: [1], user_uids: [] },
    manage_scope: null
  })
  const copy = cloneShareConfig(source)
  assert.deepEqual(copy, JSON.parse(JSON.stringify(source)))
  copy.read_scope.department_ids.push(2)
  assert.deepEqual(source.read_scope.department_ids, [1])
  assert.equal(getShareConfigLabel(source), '只读部门(1)')
})
