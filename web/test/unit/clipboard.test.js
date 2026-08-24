import assert from 'node:assert/strict'
import test from 'node:test'

import { copyTextToClipboard } from '../../src/utils/clipboard.js'

function createDocumentStub(execResult = true) {
  const appended = []
  const removed = []
  return {
    appended,
    removed,
    execCommand(command) {
      assert.equal(command, 'copy')
      return execResult
    },
    createElement(tag) {
      assert.equal(tag, 'textarea')
      return {
        style: {},
        setAttribute() {},
        focus() {},
        select() {}
      }
    },
    body: {
      appendChild(node) {
        appended.push(node)
      },
      removeChild(node) {
        removed.push(node)
      }
    }
  }
}

function withClipboardGlobals({ secure, writeText, document }, callback) {
  const previousWindow = globalThis.window
  const previousNavigator = globalThis.navigator
  const previousDocument = globalThis.document
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { isSecureContext: secure }
  })
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { clipboard: writeText ? { writeText } : undefined }
  })
  Object.defineProperty(globalThis, 'document', { configurable: true, value: document })

  return Promise.resolve(callback()).finally(() => {
    if (previousWindow === undefined) delete globalThis.window
    else Object.defineProperty(globalThis, 'window', { configurable: true, value: previousWindow })
    if (previousNavigator === undefined) delete globalThis.navigator
    else
      Object.defineProperty(globalThis, 'navigator', {
        configurable: true,
        value: previousNavigator
      })
    if (previousDocument === undefined) delete globalThis.document
    else
      Object.defineProperty(globalThis, 'document', {
        configurable: true,
        value: previousDocument
      })
  })
}

test('非安全上下文使用 execCommand 降级复制', async () => {
  const document = createDocumentStub()
  await withClipboardGlobals({ secure: false, document }, async () => {
    await copyTextToClipboard('hello')
  })

  assert.equal(document.appended[0].value, 'hello')
  assert.equal(document.removed.length, 1)
})

test('Clipboard API 被拒绝时继续使用 execCommand 降级复制', async () => {
  const document = createDocumentStub()
  await withClipboardGlobals(
    {
      secure: true,
      writeText: async () => {
        throw new Error('permission denied')
      },
      document
    },
    async () => {
      await copyTextToClipboard('fallback')
    }
  )

  assert.equal(document.appended[0].value, 'fallback')
  assert.equal(document.removed.length, 1)
})
