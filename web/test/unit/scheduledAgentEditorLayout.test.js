import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const readSource = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8')

const editorSource = readSource('../../src/components/scheduled-agents/ScheduledAgentEditor.vue')
const approvalSource = readSource('../../src/components/ToolApprovalModeSelector.vue')
const agentSelectionSource = readSource('../../src/components/AgentSelectionSection.vue')
const agentViewSource = readSource('../../src/views/AgentView.vue')
const scheduledViewSource = readSource('../../src/views/ScheduledAgentsView.vue')
const agentManageViewSource = readSource('../../src/views/AgentManageView.vue')
const attachmentOptionsSource = readSource('../../src/components/AttachmentOptionsComponent.vue')
const simpleDropdownSource = readSource(
  '../../src/components/scheduled-agents/SimpleDropdownSelect.vue'
)

test('任务详情打开后使用自适应 4/6 分栏且右侧平滑生长过渡', () => {
  assert.match(scheduledViewSource, /\.scheduled-shell\.open \.list-pane \{[\s\S]*?flex: 0 0 40%/)
  assert.match(scheduledViewSource, /\.detail-pane \{[\s\S]*?flex: 0 0 60%/)
  assert.match(scheduledViewSource, /\.detail-slide-enter-active \{[\s\S]*?160ms/)
  assert.match(
    scheduledViewSource,
    /\.task-row \{[\s\S]*?&:hover,[\s\S]*?&.selected \{[\s\S]*?border-color: var\(--gray-150\);[\s\S]*?background: var\(--gray-0\)/
  )
  assert.doesNotMatch(scheduledViewSource, /clamp\(300px, 23vw, 360px\)/)
  assert.doesNotMatch(scheduledViewSource, /width: min\(860px, 100%\)/)
})

test('编辑器只在信息组层级使用圆角卡片且列表项保持内缩', () => {
  assert.match(editorSource, /class="settings-card"/)
  assert.match(editorSource, /\.prompt-field \{[\s\S]*?border-radius: 10px/)
  assert.match(editorSource, /\.settings-card \{[\s\S]*?border-radius: 10px/)
  assert.match(editorSource, /\.settings-card \{[\s\S]*?background: var\(--gray-0\)/)
  assert.match(editorSource, /> \.setting-row:last-child \{\s+border-bottom: 0/)
  assert.match(
    editorSource,
    /\.setting-row \{[\s\S]*?padding: 0 14px;[\s\S]*?border-bottom: 1px solid var\(--gray-100\)/
  )
  assert.match(scheduledViewSource, /\.task-list \{[\s\S]*?padding: 8px 12px/)
  assert.match(scheduledViewSource, /\.task-row \{[\s\S]*?border-radius: 9px/)
})

test('定时任务工具栏与其他管理页共享 PageShoulder 布局', () => {
  assert.match(
    scheduledViewSource,
    /<aside class="list-pane"[\s\S]*?<PageShoulder[\s\S]*?v-model:search="searchQuery"/
  )
  assert.match(
    scheduledViewSource,
    /<template #actions>[\s\S]*?新建任务[\s\S]*?aria-label="刷新定时任务"/
  )
  assert.match(scheduledViewSource, /\.schedule-shoulder \{[\s\S]*?max-width: 820px/)
  assert.match(scheduledViewSource, /:deep\(\.search-input\) \{[\s\S]*?width: min\(280px, 100%\)/)
  assert.match(scheduledViewSource, /<PageShoulder[\s\S]*?<a-alert v-if="listError"/)
  assert.doesNotMatch(agentManageViewSource, /schedulePanelRef\?\.openCreate/)
})

test('定时任务工具栏和移动端详情保持可访问', () => {
  assert.match(scheduledViewSource, /\.scheduled-shell\.open \.detail-pane \{[\s\S]*?height: 100%/)
  assert.match(
    scheduledViewSource,
    /@media \(max-width: 640px\)[\s\S]*?\.schedule-shoulder \{[\s\S]*?padding-inline: 12px/
  )
})

test('工具信任提示显示在详情卡片下方而不是作为表格行', () => {
  assert.match(
    editorSource,
    /<\/div>\s+<p v-if="form\.tool_approval_mode === 'always_trust'" class="trust-warning"/
  )
  assert.match(editorSource, /\.trust-warning \{[\s\S]*?margin: 6px 0 0/)
  assert.doesNotMatch(editorSource, /\.trust-warning \{[\s\S]*?border-top/)
})

test('定时任务复用控件共享受限值列且不再泛化覆盖输入按钮', () => {
  assert.match(editorSource, /class="setting-control"/)
  assert.match(editorSource, /<AgentSelectionSection/)
  assert.doesNotMatch(editorSource, /<select v-model="form\.agent_slug"/)
  assert.doesNotMatch(editorSource, /<select v-model="form\.frequency"/)
  assert.match(editorSource, /<SimpleDropdownSelect[\s\S]*?:model-value="form\.frequency"/)
  assert.match(editorSource, /width: min\(240px, 100%\)/)
  assert.match(editorSource, /\.project-trigger-label\),\s+\.inline-editor :deep\(\.model-info\)/)
  assert.match(editorSource, /\.model-select-content\) \{\s+justify-content: flex-end/)
  assert.match(editorSource, /\.config-dropdown-trigger\)[\s\S]*?justify-content: flex-end/)
  assert.match(agentSelectionSource, /\.agent-selection-trigger \{[\s\S]*?font-size: 13px/)
  assert.doesNotMatch(editorSource, /justify-content: flex-start/)
  assert.doesNotMatch(editorSource, /:deep\(\.input-action-btn\)/)
})

test('聊天页和定时任务共用智能体选择器', () => {
  assert.match(agentViewSource, /<AgentSelectionSection/)
  assert.match(agentSelectionSource, /overlay-class-name="agent-selection-overlay"/)
  assert.match(agentSelectionSource, /<FallbackAvatar/)
  assert.match(agentSelectionSource, /emit\('update:modelValue', value\)/)
  assert.match(agentSelectionSource, /useOutsidePointerdown\(dropdownOpen/)
  assert.match(agentSelectionSource, /role="menuitemradio"/)
  assert.match(agentSelectionSource, /ArrowDown: currentIndex \+ 1/)
  assert.match(agentSelectionSource, /event\.key === 'Escape'/)
  assert.match(agentSelectionSource, /'Enter', ' '/)
  assert.match(agentSelectionSource, /focusInitialItem\(generation, attempt \+ 1\)/)
  assert.match(agentSelectionSource, /window\.clearTimeout\(focusTimer\)/)
  assert.match(agentViewSource, /agents\.value\.filter\(\(agent\) => !agent\.is_subagent\)/)
  assert.doesNotMatch(agentViewSource, /agentQuickSwitchOptions/)
})

test('工具审批组件独立拥有 Teleport 弹层样式', () => {
  assert.match(approvalSource, /overlay-class-name="tool-approval-overlay"/)
  assert.match(approvalSource, /placement: \{ type: String, default: 'topLeft' \}/)
  assert.match(editorSource, /placement="bottomRight"/)
  assert.match(approvalSource, /\.tool-approval-overlay \.config-dropdown-panel/)
  assert.match(approvalSource, /\.tool-approval-overlay \.config-dropdown-item/)
  assert.match(approvalSource, /min-width: 188px/)
  assert.match(approvalSource, /margin: 3px 0/)
})

test('自定义频率切换与下拉菜单保留安全和键盘语义', () => {
  assert.match(editorSource, /applyFrequencyChange\(form, frequency\)/)
  assert.match(editorSource, /\(form\.cronExpression \|\| ''\)\.trim\(\)/)
  assert.match(editorSource, /@update:model-value="changeFrequency"/)
  assert.match(simpleDropdownSource, /@keydown="handleTriggerKeydown"/)
  assert.match(simpleDropdownSource, /'Enter', ' '/)
  assert.match(simpleDropdownSource, /ArrowDown: index \+ 1/)
  assert.match(simpleDropdownSource, /event\.key === 'Escape'/)
  assert.match(simpleDropdownSource, /focusInitialOption\(generation, attempt \+ 1\)/)
  assert.match(simpleDropdownSource, /window\.clearTimeout\(focusTimer\)/)
})

test('附件下拉在智能体选择器抽取后继续拥有基础样式', () => {
  assert.match(attachmentOptionsSource, /\.config-dropdown-overlay \.config-dropdown-panel/)
  assert.match(attachmentOptionsSource, /\.config-dropdown-overlay \.config-dropdown-item/)
})
