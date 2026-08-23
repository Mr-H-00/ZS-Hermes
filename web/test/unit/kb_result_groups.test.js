import assert from 'node:assert/strict'
import test from 'node:test'

import { groupKnowledgeChunks } from '../../src/utils/kbResultGroups.js'

test('同名文件按知识库和文件身份分别聚合', () => {
  const groups = groupKnowledgeChunks([
    { kb_id: 'kb-1', file_id: 'file-1', content: 'A', metadata: { source: 'guide.md' } },
    { kb_id: 'kb-2', file_id: 'file-2', content: 'B', metadata: { source: 'guide.md' } },
    { kb_id: 'kb-1', file_id: 'file-1', content: 'C', metadata: { source: 'guide.md' } }
  ])

  assert.equal(groups.length, 2)
  assert.deepEqual(
    groups.map((group) => [group.kb_id, group.file_id, group.chunks.length]),
    [
      ['kb-1', 'file-1', 2],
      ['kb-2', 'file-2', 1]
    ]
  )
})
