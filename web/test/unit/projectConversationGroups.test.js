import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { buildProjectConversationGroups } from '../../src/utils/projectConversationGroups.js'

test('项目视图按项目顺序分组并把 implicit 对话放在最后', () => {
  const projects = [
    { id: 'project-a', name: 'Yuxi', selection_status: 'selectable', status: 'active' },
    { id: 'project-b', name: '论文', selection_status: 'selectable', status: 'active' },
    { id: 'deleted', name: '已删除', selection_status: 'selectable', status: 'deleted' }
  ]
  const conversations = [
    { id: 'implicit', project_id: 'implicit-project', updated_at: '2026-08-30T12:00:00Z' },
    { id: 'project-b-chat', project_id: 'project-b', updated_at: '2026-08-30T11:00:00Z' },
    { id: 'project-a-old', project_id: 'project-a', updated_at: '2026-08-30T10:00:00Z' },
    {
      id: 'project-a-pinned',
      project_id: 'project-a',
      is_pinned: true,
      updated_at: '2026-08-29T10:00:00Z'
    },
    { id: 'deleted-chat', project_id: 'deleted', updated_at: '2026-08-30T09:00:00Z' }
  ]

  const result = buildProjectConversationGroups(projects, conversations)

  assert.deepEqual(
    result.groups.map((group) => group.project.id),
    ['project-a', 'project-b']
  )
  assert.deepEqual(
    result.groups[0].conversations.map((conversation) => conversation.id),
    ['project-a-pinned', 'project-a-old']
  )
  assert.deepEqual(
    result.otherConversations.map((conversation) => conversation.id),
    ['implicit', 'deleted-chat']
  )
})

test('无项目时全部对话仍可在其他对话与最近视图读取', () => {
  const conversations = [{ id: 'thread-1', project_id: 'implicit', created_at: '2026-08-30' }]

  const result = buildProjectConversationGroups([], conversations)

  assert.deepEqual(result.groups, [])
  assert.equal(result.otherConversations[0].id, 'thread-1')
  assert.equal(result.sortedConversations[0].id, 'thread-1')
})

test('对话选择与操作菜单使用并列按钮语义', () => {
  const source = readFileSync(
    new URL('../../src/components/ConversationNavItem.vue', import.meta.url),
    'utf8'
  )

  assert.match(source, /class="conversation-select"/)
  assert.match(source, /type="button"/)
  assert.doesNotMatch(source, /role="button"/)
  assert.doesNotMatch(source, /@keydown\.(?:enter|space)/)
})
