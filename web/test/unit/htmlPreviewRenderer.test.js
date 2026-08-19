import assert from 'node:assert/strict'
import test from 'node:test'

import { renderHtmlPreviewBlocks } from '../../src/utils/htmlPreviewRenderer.js'

test('流式 HTML 预览使用正式预览一半的 loading 高度', () => {
  const result = renderHtmlPreviewBlocks('```html:preview\n<div>')
  assert.match(result, /--html-preview-height: 360px/)
  assert.match(result, /--html-preview-loading-height: 180px/)
  assert.match(result, /html-preview-loading-slot/)
})
