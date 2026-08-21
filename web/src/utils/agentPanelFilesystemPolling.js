const FILESYSTEM_REFRESH_INTERVAL_MS = 1000

export const createFilesystemRefreshGate = () => {
  const inFlightThreads = new Set()
  return {
    begin(threadId) {
      const key = String(threadId || '')
      if (!key || inFlightThreads.has(key)) return false
      inFlightThreads.add(key)
      return true
    },
    finish(threadId) {
      inFlightThreads.delete(String(threadId || ''))
    },
    canCommit(requestedThreadId, currentThreadId) {
      return Boolean(requestedThreadId) && requestedThreadId === currentThreadId
    }
  }
}

export const expandedKeysAfterFilesystemRefresh = (currentKeys, { silent }) =>
  silent ? currentKeys : []

const treeContainsKey = (nodes, targetKey) =>
  nodes.some(
    (node) =>
      node.key === targetKey || (node.children?.length && treeContainsKey(node.children, targetKey))
  )

const replaceTreeChildren = (nodes, targetKey, children) =>
  nodes.map((node) => {
    if (node.key === targetKey) return { ...node, children }
    if (!node.children?.length) return node
    return { ...node, children: replaceTreeChildren(node.children, targetKey, children) }
  })

export const refreshExpandedTree = async (nodes, expandedKeys, loadChildren) => {
  let refreshed = nodes
  const directoryKeys = [...new Set(expandedKeys)].sort(
    (left, right) => String(left).split('/').length - String(right).split('/').length
  )
  for (const key of directoryKeys) {
    if (!treeContainsKey(refreshed, key)) continue
    try {
      refreshed = replaceTreeChildren(refreshed, key, await loadChildren(key))
    } catch {
      // 单个目录瞬时不可读时保留上一轮 children，不能清空整个已展开树。
    }
  }
  return refreshed
}

export const shouldRefreshActivePreview = (currentFile, latestFile) => {
  if (!currentFile) return false
  if (!latestFile) return false
  return (
    Number(currentFile.size ?? -1) !== Number(latestFile.size ?? -1) ||
    String(currentFile.modified_at || '') !== String(latestFile.modified_at || '')
  )
}

export const invalidatePreviewCacheEntryBeforeReload = (
  previewCache,
  cacheKey,
  revokeObjectURL
) => {
  const cachedEntry = previewCache.get(cacheKey)
  if (!cachedEntry) return false

  previewCache.delete(cacheKey)
  if (cachedEntry.status === 'ready' && cachedEntry.file?.previewUrl) {
    revokeObjectURL(cachedEntry.file.previewUrl)
  }
  return true
}

export const reloadPreviewAfterOrderedCacheEntryInvalidation = async ({
  previewCache,
  cacheKey,
  revokeObjectURL,
  notifyPreviewChanged,
  reloadPreview
}) => {
  invalidatePreviewCacheEntryBeforeReload(previewCache, cacheKey, revokeObjectURL)
  notifyPreviewChanged()
  await reloadPreview()
}

export const replacePreviewCacheEntryIfCurrent = (
  previewCache,
  cacheKey,
  currentEntry,
  nextEntry
) => {
  if (previewCache.get(cacheKey) !== currentEntry) return false
  if (nextEntry) previewCache.set(cacheKey, nextEntry)
  else previewCache.delete(cacheKey)
  return true
}

export const settlePreviewCacheLoad = ({
  previewCache,
  cacheKey,
  loadingEntry,
  nextFile,
  lastAccessed,
  revokeObjectURL
}) => {
  const published = replacePreviewCacheEntryIfCurrent(previewCache, cacheKey, loadingEntry, {
    status: 'ready',
    file: nextFile,
    lastAccessed
  })
  if (!published && nextFile?.previewUrl) revokeObjectURL(nextFile.previewUrl)
  return published
}

export const startAgentPanelFilesystemPolling = ({
  refresh,
  setIntervalFn = window.setInterval.bind(window),
  clearIntervalFn = window.clearInterval.bind(window)
}) => {
  const timer = setIntervalFn(() => {
    void refresh()
  }, FILESYSTEM_REFRESH_INTERVAL_MS)
  return () => clearIntervalFn(timer)
}
