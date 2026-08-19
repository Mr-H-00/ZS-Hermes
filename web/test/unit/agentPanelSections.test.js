import assert from 'node:assert/strict'
import test from 'node:test'

import {
  FILE_TREE_SECTION,
  closeAgentPanelSection,
  upsertAgentPanelSection
} from '../../src/utils/agentPanelSections.js'

test('同一子线程重复打开时更新已有 Section 而不新增', () => {
  const section = { key: 'subagent:thread-1', type: 'subagent', threadId: 'thread-1', title: '研究员' }
  const first = upsertAgentPanelSection([FILE_TREE_SECTION], section)
  const second = upsertAgentPanelSection(first, { ...section, title: '研究助手' })
  assert.equal(second.length, 2)
  assert.equal(second[1].title, '研究助手')
})

test('关闭活动 Tab 后激活相邻项', () => {
  const sections = [
    FILE_TREE_SECTION,
    { key: 'subagent:a', type: 'subagent', threadId: 'a' },
    { key: 'subagent:b', type: 'subagent', threadId: 'b' }
  ]
  assert.deepEqual(closeAgentPanelSection(sections, 'subagent:a', 'subagent:a'), {
    sections: [sections[0], sections[2]],
    activeKey: 'subagent:b'
  })
})
