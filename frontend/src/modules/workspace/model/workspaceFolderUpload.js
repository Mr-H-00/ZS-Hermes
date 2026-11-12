export const WORKSPACE_UPLOAD_MAX_FILES = 50
// 与后端单文件上限一致；前端只做提前提示，最终仍由后端拒绝。
export const WORKSPACE_UPLOAD_MAX_FILE_BYTES = 100 * 1024 * 1024
// 目标批次低于 18 MiB；单个超过该值的文件独立成批，仍可能触发入口 413。
export const WORKSPACE_UPLOAD_BATCH_BYTES = 18 * 1024 * 1024

/** 收集文件夹选择结果，保留浏览器提供的相对路径。 */
export function collectFolderUploadEntries(fileList) {
  return Array.from(fileList || []).map((file) => {
    const relativePath = String(file?.webkitRelativePath || '')
    if (!relativePath) {
      throw new Error(`无法获取 ${file?.name || '文件'} 的文件夹路径`)
    }
    return { file, relativePath }
  })
}

/** 在请求前拒绝超过单文件上限的选择，避免上传到一半才失败。 */
export function validateFolderUploadEntries(entries) {
  const oversized = (entries || []).find(
    ({ file }) => Number(file?.size) > WORKSPACE_UPLOAD_MAX_FILE_BYTES
  )
  if (oversized) {
    throw new Error(`${oversized.relativePath} 超过 100 MB，未开始上传`)
  }
}

/** 按文件数量和总字节数切批，保持选择顺序且不拆分单个文件。 */
export function buildFolderUploadBatches(
  entries,
  { maxFiles = WORKSPACE_UPLOAD_MAX_FILES, maxBytes = WORKSPACE_UPLOAD_BATCH_BYTES } = {}
) {
  const batches = []
  let current = []
  let currentBytes = 0

  for (const entry of entries || []) {
    const size = Number(entry?.file?.size) || 0
    if (current.length && (current.length >= maxFiles || currentBytes + size > maxBytes)) {
      batches.push(current)
      current = []
      currentBytes = 0
    }
    current.push(entry)
    currentBytes += size
  }
  if (current.length) batches.push(current)
  return batches
}

/** 同步收集拖拽中的顶层文件与目录入口；目录识别依赖浏览器条目 API，缺失时退回纯文件。 */
export function collectDragUploadRoots(dataTransfer) {
  const files = []
  const directories = []
  const items = dataTransfer?.items
  if (items && items.length) {
    for (const item of items) {
      if (item.kind !== 'file') continue
      const entry = typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null
      if (entry?.isDirectory) {
        directories.push({ name: entry.name, entry })
        continue
      }
      const file = item.getAsFile()
      if (file) files.push(file)
    }
    return { files, directories }
  }
  for (const file of Array.from(dataTransfer?.files || [])) {
    files.push(file)
  }
  return { files, directories }
}

const readDirectoryEntries = (reader) =>
  new Promise((resolve, reject) => {
    const collected = []
    const readBatch = () => {
      // readEntries 每次最多返回 100 条，必须循环读到空批才算遍历完。
      reader.readEntries((batch) => {
        if (!batch.length) {
          resolve(collected)
          return
        }
        collected.push(...batch)
        readBatch()
      }, reject)
    }
    readBatch()
  })

/** 递归读取拖拽目录入口，返回与文件夹选择同构的 {file, relativePath} 列表。 */
export async function traverseDirectoryEntry(directoryEntry) {
  if (!directoryEntry?.isDirectory) return []
  const files = []
  const walk = async (entry, prefix) => {
    const path = prefix ? `${prefix}/${entry.name}` : entry.name
    const children = await readDirectoryEntries(entry.createReader())
    for (const child of children) {
      if (child.isDirectory) {
        await walk(child, path)
      } else if (child.isFile) {
        const file = await new Promise((resolve, reject) => child.file(resolve, reject))
        files.push({ file, relativePath: `${path}/${child.name}` })
      }
    }
  }
  await walk(directoryEntry, '')
  return files
}
