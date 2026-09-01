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

test('无项目时全部对话仍可在最近分组读取', () => {
  const conversations = [{ id: 'thread-1', project_id: 'implicit', created_at: '2026-08-30' }]

  const result = buildProjectConversationGroups([], conversations)

  assert.deepEqual(result.groups, [])
  assert.equal(result.otherConversations[0].id, 'thread-1')
})

test('最近分组保持按创建时间排序且不受更新时间影响', () => {
  const conversations = [
    {
      id: 'older-renamed',
      created_at: '2026-08-29T10:00:00Z',
      updated_at: '2026-09-01T10:00:00Z'
    },
    {
      id: 'newer-created',
      created_at: '2026-08-30T10:00:00Z',
      updated_at: '2026-08-30T10:00:00Z'
    }
  ]

  const result = buildProjectConversationGroups([], conversations)

  assert.deepEqual(
    result.otherConversations.map((conversation) => conversation.id),
    ['newer-created', 'older-renamed']
  )
})

test('侧边栏同时展示项目和最近分组，最近只展示其他对话', () => {
  const source = readFileSync(
    new URL('../../src/components/ConversationNavSection.vue', import.meta.url),
    'utf8'
  )
  const projectHeadingIndex = source.indexOf('<span>项目</span>')
  const recentHeadingIndex = source.indexOf('<span>最近</span>')
  const recentSectionStart = source.indexOf('<section class="history-group recent-history-group">')
  const recentSection = source.slice(
    recentSectionStart,
    source.indexOf('</section>', recentSectionStart)
  )

  assert.ok(projectHeadingIndex >= 0)
  assert.ok(projectHeadingIndex < recentHeadingIndex)
  assert.ok(recentSectionStart >= 0)
  assert.match(recentSection, /v-if="projectsLoading"[^>]*>正在加载对话/)
  assert.match(recentSection, /v-else-if="projectsError"[^>]*>项目加载失败，暂时无法分类对话/)
  assert.match(recentSection, /v-for="chat in otherConversations"/)
  assert.match(source, /<FolderOpen v-if="isProjectExpanded\(group\.project\.id\)"/)
  assert.match(source, /<FolderClosed v-else/)
  assert.equal(source.match(/<CollapseTransition>/g)?.length, 3)
  assert.doesNotMatch(source, /v-show=/)
  assert.match(source, /\.project-history-group\s*{\s*margin-bottom: 16px;/)
  assert.match(source, /\.collapse-icon\s*{[\s\S]*?opacity: 0;/)
  assert.match(
    source,
    /&:hover,[\s\S]*?&:focus-visible\s*{[\s\S]*?\.collapse-icon\s*{\s*opacity: 1;/
  )
  assert.doesNotMatch(
    source,
    /view-switch|viewMode|project-chevron|project-count|<span>其他对话<\/span>/
  )
})

test('页面内创建的 Project 会写入共享侧边栏导航 Owner', () => {
  const layoutSource = readFileSync(
    new URL('../../src/layouts/AppLayout.vue', import.meta.url),
    'utf8'
  )
  const selectionSource = readFileSync(
    new URL('../../src/components/ProjectSelectionSection.vue', import.meta.url),
    'utf8'
  )

  assert.match(layoutSource, /useProjectsStore\(\)/)
  assert.match(selectionSource, /useProjectsStore\(\)/)
  assert.match(selectionSource, /projectsStore\.upsertProject\(project\)/)
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
