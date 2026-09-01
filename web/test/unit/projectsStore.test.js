import assert from 'node:assert/strict'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'
import { buildProjectConversationGroups } from '../../src/utils/projectConversationGroups.js'

globalThis.localStorage = {
  getItem: () => null,
  setItem: () => {},
  removeItem: () => {}
}

test('创建 Project 后迟到的列表响应不会覆盖侧边栏状态', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  setActivePinia(createPinia())
  try {
    const { projectApi } = await server.ssrLoadModule('/src/apis/project_api.js')
    let resolveProjects
    projectApi.getProjects = () =>
      new Promise((resolve) => {
        resolveProjects = resolve
      })

    const { useProjectsStore } = await server.ssrLoadModule('/src/stores/projects.js')
    const store = useProjectsStore()
    const loadPromise = store.loadProjects()
    const createdProject = {
      id: 'project-new',
      name: '新项目',
      selection_status: 'selectable',
      status: 'active'
    }

    store.upsertProject(createdProject)
    resolveProjects([])
    await loadPromise

    assert.deepEqual(store.projects, [createdProject])
    assert.equal(store.isLoading, false)
    const grouped = buildProjectConversationGroups(store.projects, [
      { id: 'thread-new', project_id: createdProject.id, created_at: '2026-09-01T10:00:00Z' }
    ])
    assert.equal(grouped.groups[0].conversations[0].id, 'thread-new')
    assert.deepEqual(grouped.otherConversations, [])
  } finally {
    await server.close()
  }
})
