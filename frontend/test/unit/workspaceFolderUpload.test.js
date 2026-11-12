import assert from 'node:assert/strict'
import test from 'node:test'

import {
  WORKSPACE_UPLOAD_MAX_FILE_BYTES,
  buildFolderUploadBatches,
  collectDragUploadRoots,
  collectFolderUploadEntries,
  traverseDirectoryEntry,
  validateFolderUploadEntries
} from '../../src/modules/workspace/model/workspaceFolderUpload.js'

const entry = (relativePath, size = 1) => ({
  file: { name: relativePath.split('/').pop(), size },
  relativePath
})

test('收集文件夹选择时保留 webkitRelativePath 和隐藏文件', () => {
  const visible = { name: 'a.txt', size: 3, webkitRelativePath: 'root/a.txt' }
  const hidden = { name: '.env', size: 4, webkitRelativePath: 'root/.env' }

  assert.deepEqual(collectFolderUploadEntries([visible, hidden]), [
    { file: visible, relativePath: 'root/a.txt' },
    { file: hidden, relativePath: 'root/.env' }
  ])
})

test('缺少相对路径时拒绝继续，避免静默平铺', () => {
  assert.throws(
    () => collectFolderUploadEntries([{ name: 'a.txt', size: 1 }]),
    /无法获取 a\.txt 的文件夹路径/
  )
})

test('保留文件夹名前导空白，不把不同目录合并', () => {
  const file = { name: 'a.txt', size: 1, webkitRelativePath: ' root/sub/a.txt' }

  assert.equal(collectFolderUploadEntries([file])[0].relativePath, ' root/sub/a.txt')
})

test('单文件超过后端上限时提前拒绝', () => {
  const oversized = WORKSPACE_UPLOAD_MAX_FILE_BYTES + 1
  assert.throws(
    () => validateFolderUploadEntries([entry('root/large.bin', oversized)]),
    /超过 100 MB/
  )
  assert.doesNotThrow(() =>
    validateFolderUploadEntries([entry('root/limit.bin', WORKSPACE_UPLOAD_MAX_FILE_BYTES)])
  )
})

test('按文件数量和总字节数分批且保持顺序', () => {
  const entries = [
    entry('root/a.txt', 6),
    entry('root/b.txt', 6),
    entry('root/c.txt', 6),
    entry('root/d.txt', 6),
    entry('root/e.txt', 6)
  ]

  const byCount = buildFolderUploadBatches(entries, { maxFiles: 2, maxBytes: 1000 })
  assert.deepEqual(
    byCount.map((batch) => batch.map((item) => item.relativePath)),
    [['root/a.txt', 'root/b.txt'], ['root/c.txt', 'root/d.txt'], ['root/e.txt']]
  )

  const byBytes = buildFolderUploadBatches(entries, { maxFiles: 50, maxBytes: 12 })
  assert.deepEqual(
    byBytes.map((batch) => batch.map((item) => item.relativePath)),
    [['root/a.txt', 'root/b.txt'], ['root/c.txt', 'root/d.txt'], ['root/e.txt']]
  )
})

test('单个文件超过批次字节预算时独立成批', () => {
  const entries = [entry('root/small.txt', 1), entry('root/large.bin', 30), entry('root/after.txt', 1)]

  const batches = buildFolderUploadBatches(entries, { maxFiles: 50, maxBytes: 18 })

  assert.deepEqual(
    batches.map((batch) => batch.map((item) => item.relativePath)),
    [['root/small.txt'], ['root/large.bin'], ['root/after.txt']]
  )
})

test('空选择不产生批次', () => {
  assert.deepEqual(buildFolderUploadBatches([]), [])
})

const dragFile = (name) => ({ name, size: 2 })

const makeFileEntry = (name) => ({
  isFile: true,
  isDirectory: false,
  name,
  file: (resolve) => resolve(dragFile(name))
})

const makeDirectoryEntry = (name, children) => {
  let batchesExhausted = false
  return {
    isFile: false,
    isDirectory: true,
    name,
    createReader: () => ({
      readEntries: (resolve) => {
        // readEntries 首批之后必须返回空批，模拟真实目录读取循环。
        if (batchesExhausted) {
          resolve([])
          return
        }
        batchesExhausted = true
        resolve(children)
      }
    })
  }
}

const dataTransferWithItems = (items) => ({
  items: {
    length: items.length,
    [Symbol.iterator]: () => items[Symbol.iterator]()
  }
})

test('拖拽收集区分顶层文件与目录，忽略非文件项', () => {
  const dirEntry = makeDirectoryEntry('docs', [])
  const dataTransfer = dataTransferWithItems([
    { kind: 'string' },
    { kind: 'file', webkitGetAsEntry: () => makeFileEntry('a.txt'), getAsFile: () => dragFile('a.txt') },
    { kind: 'file', webkitGetAsEntry: () => dirEntry, getAsFile: () => dragFile('ignored') },
    { kind: 'file', webkitGetAsEntry: () => null, getAsFile: () => dragFile('b.txt') }
  ])

  const roots = collectDragUploadRoots(dataTransfer)

  assert.deepEqual(
    roots.files.map((file) => file.name),
    ['a.txt', 'b.txt']
  )
  assert.deepEqual(
    roots.directories.map((directory) => directory.name),
    ['docs']
  )
})

test('拖拽收集在无条目 API 时退回文件列表', () => {
  const dataTransfer = { files: [dragFile('a.txt')], items: { length: 0 } }

  assert.deepEqual(collectDragUploadRoots(dataTransfer).files, [dragFile('a.txt')])
  assert.deepEqual(collectDragUploadRoots(undefined), { files: [], directories: [] })
})

test('递归遍历拖拽目录并保留相对路径层级', async () => {
  const tree = makeDirectoryEntry('root', [
    makeFileEntry('a.txt'),
    makeDirectoryEntry('sub', [makeFileEntry('b.txt')])
  ])

  const entries = await traverseDirectoryEntry(tree)

  assert.deepEqual(
    entries.map((item) => item.relativePath),
    ['root/a.txt', 'root/sub/b.txt']
  )
  assert.deepEqual(
    entries.map((item) => item.file.name),
    ['a.txt', 'b.txt']
  )
})

test('非目录入口返回空列表', async () => {
  assert.deepEqual(await traverseDirectoryEntry(makeFileEntry('a.txt')), [])
  assert.deepEqual(await traverseDirectoryEntry(null), [])
})

test('目录条目读取失败时向上传播错误', async () => {
  const broken = {
    isFile: false,
    isDirectory: true,
    name: 'bad',
    createReader: () => ({
      readEntries: (resolve, reject) => reject(new Error('read denied'))
    })
  }

  await assert.rejects(() => traverseDirectoryEntry(broken), /read denied/)
})

test('目录内单文件读取失败时向上传播错误', async () => {
  const brokenFile = {
    isFile: true,
    isDirectory: false,
    name: 'f.txt',
    file: (resolve, reject) => reject(new Error('file gone'))
  }
  const parent = makeDirectoryEntry('root', [brokenFile])

  await assert.rejects(() => traverseDirectoryEntry(parent), /file gone/)
})
