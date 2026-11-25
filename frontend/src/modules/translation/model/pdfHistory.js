const storeName = 'translated-pdfs'

async function withPdfHistory(mode, access) {
  const database = await new Promise((resolve, reject) => {
    const request = indexedDB.open('yuxi-translation', 1)
    request.onupgradeneeded = () => request.result.createObjectStore(storeName, { keyPath: ['owner', 'id'] })
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(storeName, mode)
      const request = access(transaction.objectStore(storeName))
      transaction.oncomplete = () => resolve(request?.result)
      transaction.onabort = () => reject(transaction.error)
    })
  } finally {
    database.close()
  }
}

export function readPdfHistory(owner, id) {
  return withPdfHistory('readonly', (store) => store.get([owner, id]))
}

// PDF 二进制由 IndexedDB 管理，文本历史只保存文件名和摘要。
export function savePdfHistory(owner, id, source, result) {
  return withPdfHistory('readwrite', (store) => store.put({ owner, id, source, result }))
}

export function deletePdfHistory(owner, ids) {
  return withPdfHistory('readwrite', (store) => {
    for (const id of ids) store.delete([owner, id])
  })
}
