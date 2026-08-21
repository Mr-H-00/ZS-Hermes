const USER_WORKSPACE_PREFIX = '/home/gem/user-data/'

const isTrackedPanelFilePath = (path) => {
  const normalizedPath = String(path || '')
  return normalizedPath.startsWith(USER_WORKSPACE_PREFIX)
}

export const shouldAutoOpenAgentPanel = (threadFiles) => {
  if (!Array.isArray(threadFiles) || threadFiles.length === 0) return false

  return threadFiles.some((item) => item?.is_dir !== true && isTrackedPanelFilePath(item?.path))
}
