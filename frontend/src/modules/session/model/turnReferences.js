import { defineStore } from 'pinia'
import { reactive } from 'vue'
import { agentApi } from '@/apis'

/** 按 Thread/Turn 共享标注状态，切换会话时保持请求归属。 */
export const useTurnReferencesStore = defineStore('turnReferences', () => {
  const entries = reactive({})
  const requests = new Map()

  function getEntry(threadId, turnId) {
    const key = `${threadId}:${turnId}`
    entries[key] ||= {
      loaded: false, loading: false, annotating: false, error: '',
      available: false, sources: [], references: null, visible: true
    }
    return entries[key]
  }

  async function load(threadId, turnId) {
    const entry = getEntry(threadId, turnId)
    if (entry.loaded) return
    const key = `${threadId}:${turnId}`
    if (requests.has(key)) return requests.get(key)
    const request = (async () => {
      entry.loading = true
      entry.error = ''
      try {
        Object.assign(entry, await agentApi.getTurnReferences(threadId, turnId), { loaded: true })
      } catch (error) {
        entry.error = error.message || '来源加载失败'
      } finally {
        entry.loading = false
        requests.delete(key)
      }
    })()
    requests.set(key, request)
    return request
  }

  async function annotate(threadId, turnId) {
    const entry = getEntry(threadId, turnId)
    if (entry.annotating) return
    entry.annotating = true
    entry.error = ''
    try {
      entry.references = await agentApi.annotateTurnReferences(threadId, turnId)
      entry.visible = true
    } catch (error) {
      entry.error = error.message || '来源标注失败，请重试'
    } finally {
      entry.annotating = false
    }
  }

  return { getEntry, load, annotate }
})
