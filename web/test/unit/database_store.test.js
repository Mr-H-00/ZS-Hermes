import test from 'node:test'
import assert from 'node:assert/strict'

import { message } from 'ant-design-vue'
import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

test('从二级目录点击全部文件会清空 parent_id 并返回根目录', async () => {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom'
  })

  try {
    setActivePinia(createPinia())
    const { documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
    const { useDatabaseStore } = await server.ssrLoadModule('/src/stores/database.js')
    const requests = []

    documentApi.listDocuments = async (kbId, params) => {
      requests.push({ kbId, params })
      return {
        items: [],
        page: 1,
        page_size: 100,
        total: 0,
        has_more: false,
        path_prefix: ''
      }
    }

    const store = useDatabaseStore()
    store.kbId = 'kb_1'
    store.fileBrowser.parentId = 'folder_2'
    store.folderBreadcrumbs = [
      { file_id: null, filename: '全部文件', path_prefix: '' },
      { file_id: 'folder_2', filename: '二级目录', path_prefix: '' }
    ]

    await store.goToFolder(0)

    assert.equal(store.fileBrowser.parentId, null)
    assert.deepEqual(store.folderBreadcrumbs, [
      { file_id: null, filename: '全部文件', path_prefix: '' }
    ])
    assert.deepEqual(requests, [
      {
        kbId: 'kb_1',
        params: {
          page: 1,
          page_size: 100,
          status: 'all',
          recursive: false
        }
      }
    ])
  } finally {
    await server.close()
  }
})

test('文档重切登记非敏感任务摘要并刷新文件状态', async () => {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom'
  })
  const originalSetTimeout = globalThis.setTimeout
  const originalClearTimeout = globalThis.clearTimeout
  const originalMessageSuccess = message.success
  const originalMessageError = message.error
  globalThis.setTimeout = (callback, delay) => {
    if (delay === 1000) callback()
    return 1
  }
  globalThis.clearTimeout = () => {}
  message.success = () => {}
  message.error = () => {}

  try {
    setActivePinia(createPinia())
    const { databaseApi, documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
    const { useDatabaseStore } = await server.ssrLoadModule('/src/stores/database.js')
    const { useTaskerStore } = await server.ssrLoadModule('/src/stores/tasker.js')
    const requests = []
    let databaseRefreshCount = 0
    let documentRefreshCount = 0

    documentApi.resliceDocuments = async (kbId, fileIds, params) => {
      requests.push({ kbId, fileIds, params })
      return { status: 'queued', task_id: 'task-reslice-1', message: '重切已排队' }
    }
    databaseApi.getDatabaseInfo = async () => {
      databaseRefreshCount += 1
      return { kb_id: 'kb-1', files: {}, stats: { processing_count: 0 } }
    }
    documentApi.listDocuments = async () => {
      documentRefreshCount += 1
      return { items: [], page: 1, page_size: 100, total: 0, has_more: false }
    }

    const store = useDatabaseStore()
    const taskerStore = useTaskerStore()
    store.kbId = 'kb-1'
    const params = {
      parent_child: { enabled: true },
      embedding_features: { bge_m3_sparse_enabled: true }
    }

    const succeeded = await store.resliceFiles(['file-1', 'file-2'], params)

    assert.equal(succeeded, true)
    assert.deepEqual(requests, [{ kbId: 'kb-1', fileIds: ['file-1', 'file-2'], params }])
    assert.equal(databaseRefreshCount, 1)
    assert.equal(documentRefreshCount, 1)
    assert.equal(store.state.chunkLoading, false)
    assert.deepEqual(
      taskerStore.tasks.map(({ id, name, type, status, payload }) => ({
        id,
        name,
        type,
        status,
        payload
      })),
      [
        {
          id: 'task-reslice-1',
          name: '文档重切 (kb-1)',
          type: 'knowledge_reslice',
          status: 'queued',
          payload: { kb_id: 'kb-1', count: 2 }
        }
      ]
    )
  } finally {
    globalThis.setTimeout = originalSetTimeout
    globalThis.clearTimeout = originalClearTimeout
    message.success = originalMessageSuccess
    message.error = originalMessageError
    await server.close()
  }
})
