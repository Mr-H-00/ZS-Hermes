const databaseName = 'yuxi-paintings'
const storeName = 'paintings'

function openHistory() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(databaseName, 1)
    request.onupgradeneeded = () => {
      const records = request.result.createObjectStore(storeName, { keyPath: ['owner', 'id'] })
      records.createIndex('owner', 'owner')
    }
    request.onerror = () => reject(request.error)
    request.onsuccess = () => resolve(request.result)
  })
}

async function withHistory(mode, action) {
  const database = await openHistory()
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(storeName, mode)
      const request = action(transaction.objectStore(storeName))
      transaction.oncomplete = () => resolve(request.result)
      transaction.onabort = () => reject(transaction.error)
    })
  } finally {
    database.close()
  }
}

/** 按已登录账号读取这台设备的绘画历史。 */
export async function readPaintings(owner) {
  const records = await withHistory('readonly', (store) => store.index('owner').getAll(owner))
  return records.sort((first, second) => second.updatedAt - first.updatedAt)
}

export function savePainting(owner, painting) {
  return withHistory('readwrite', (store) => store.put({ ...painting, owner }))
}

export function deletePainting(owner, id) {
  return withHistory('readwrite', (store) => store.delete([owner, id]))
}
