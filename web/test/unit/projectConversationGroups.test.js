import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildProjectConversationGroups,
  deriveProjectThreadStatus
} from '../../src/utils/projectConversationGroups.js'

test('项目状态优先展示运行中，其次展示未读完成', () => {
  assert.equal(deriveProjectThreadStatus([{ thread_status: 'ready' }]), 'ready')
  assert.equal(
    deriveProjectThreadStatus([{ thread_status: 'ready' }, { thread_status: 'loading' }]),
    'loading'
  )
  assert.equal(deriveProjectThreadStatus([{ thread_status: 'done' }]), 'done')
  assert.equal(deriveProjectThreadStatus([]), 'done')
})

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
  assert.match(
    source,
    /<section\s+v-if="projectsLoading \|\| projectsError \|\| projectGroups\.length"\s+class="history-group project-history-group"/
  )
  assert.doesNotMatch(source, />暂无项目</)
  assert.ok(recentSectionStart >= 0)
  assert.match(recentSection, /v-if="projectsLoading"[^>]*>正在加载对话/)
  assert.match(recentSection, /v-else-if="projectsError"[^>]*>项目加载失败，暂时无法分类对话/)
  assert.match(recentSection, /v-for="chat in otherConversations"/)
  assert.match(
    source,
    /<FolderOpen[^>]*v-if="isProjectExpanded\(group\.project\.id\)"[^>]*class="project-icon"[^>]*\/>/
  )
  assert.match(source, /<FolderClosed[^>]*v-else[^>]*class="project-icon"[^>]*\/>/)
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

test('项目默认折叠且长名称不会挤压文件夹图标', () => {
  const source = readFileSync(
    new URL('../../src/components/ConversationNavSection.vue', import.meta.url),
    'utf8'
  )

  assert.match(source, /const expandedProjects = ref\(new Set\(\)\)/)
  assert.match(
    source,
    /const isProjectExpanded = \(projectId\) => expandedProjects\.value\.has\(projectId\)/
  )
  assert.doesNotMatch(source, /collapsedProjects/)
  assert.match(source, /class="project-name" :title="group\.project\.name"/)
  assert.match(source, /\.project-name\s*{[\s\S]*?min-width: 0;[\s\S]*?flex: 1;/)
  assert.match(source, /\.project-icon\s*{\s*flex: 0 0 17px;/)
})

test('项目运行态只在折叠时占据最右状态位，悬浮时让位给操作', () => {
  const source = readFileSync(
    new URL('../../src/components/ConversationNavSection.vue', import.meta.url),
    'utf8'
  )
  const projectRowStart = source.indexOf('.project-row {')
  const projectRowStyles = source.slice(projectRowStart, source.indexOf('.project-toggle {'))

  assert.ok(projectRowStart >= 0)
  assert.match(
    source,
    /group\.threadStatus === 'loading' &&\s*!isProjectExpanded\(group\.project\.id\)/
  )
  assert.match(source, /\.project-row\s*{\s*position: relative;/)
  assert.match(source, /\.project-status\s*{[\s\S]*?position: absolute;[\s\S]*?right: 6px;/)
  assert.match(
    projectRowStyles,
    /&:hover,\s*&:focus-within\s*{[\s\S]*?\.project-action\s*{[\s\S]*?opacity: 1;[\s\S]*?pointer-events: auto;[\s\S]*?\.project-status\s*{\s*display: none;/
  )
  assert.match(
    source,
    /\.project-action\s*{[\s\S]*?opacity: 0;[\s\S]*?pointer-events: none;/
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

test('对话状态拥有常驻遮罩且项目提供带项目上下文的新建入口', () => {
  const itemSource = readFileSync(
    new URL('../../src/components/ConversationNavItem.vue', import.meta.url),
    'utf8'
  )
  const navigationSource = readFileSync(
    new URL('../../src/components/ConversationNavSection.vue', import.meta.url),
    'utf8'
  )
  const layoutSource = readFileSync(
    new URL('../../src/layouts/AppLayout.vue', import.meta.url),
    'utf8'
  )
  const agentViewSource = readFileSync(new URL('../../src/views/AgentView.vue', import.meta.url), 'utf8')
  const chatSource = readFileSync(
    new URL('../../src/components/AgentChatComponent.vue', import.meta.url),
    'utf8'
  )

  assert.match(itemSource, /class="status-mask"/)
  assert.match(itemSource, /v-if="chat\.thread_status === 'loading' \|\| chat\.thread_status === 'ready'"/)
  assert.match(navigationSource, /class="project-status project-status-loading"/)
  assert.match(navigationSource, /class="project-status project-status-ready"/)
  assert.match(navigationSource, /@click\.stop="\$emit\('create-project-chat', group\.project\.id\)"/)
  assert.match(layoutSource, /query: \{ project_id: projectId \}/)
  const createProjectChatHandler = layoutSource.slice(
    layoutSource.indexOf('const handleCreateProjectChat'),
    layoutSource.indexOf('const searchWorkspace')
  )
  assert.match(createProjectChatHandler, /const handleCreateProjectChat = async/)
  assert.ok(
    createProjectChatHandler.indexOf('await router.push') <
      createProjectChatHandler.indexOf('setCurrentThreadId(null)')
  )
  assert.match(agentViewSource, /:initial-project-id="routeDraftProjectId"/)
  assert.match(chatSource, /if \(!threadId\) selectedProjectId\.value = initialProjectId \|\| AUTO_PROJECT_ID/)
  assert.match(
    chatSource,
    /ensureActiveThread\.reset\(\)\s*selectedProjectId\.value = props\.initialProjectId \|\| AUTO_PROJECT_ID/
  )
  assert.doesNotMatch(chatSource, /ensureActiveThread\.reset\(\)\s*selectedProjectId\.value = AUTO_PROJECT_ID/)
  assert.match(layoutSource, /\.foo\s*\{[\s\S]*?background: var\(--main-5\);/)
})
