import assert from 'node:assert/strict'
import test from 'node:test'

import { shouldAutoOpenAgentPanel } from '../../src/utils/agentPanelAutoOpen.js'

test('UserWorkspace Workdir 出现普通文件时自动打开 AgentPanel', () => {
  assert.equal(
    shouldAutoOpenAgentPanel([
      { path: '/home/gem/user-data/projects/abc/uploads', is_dir: true },
      { path: '/home/gem/user-data/projects/abc/report.md', is_dir: false }
    ]),
    true
  )
})

test('目录和 UserWorkspace 外路径不会触发 Project Viewer', () => {
  assert.equal(
    shouldAutoOpenAgentPanel([
      { path: '/home/gem/user-data/projects/abc/outputs', is_dir: true },
      { path: '/home/gem/skills/shared/SKILL.md', is_dir: false },
      { path: '/tmp/outside.md', is_dir: false }
    ]),
    false
  )
})
