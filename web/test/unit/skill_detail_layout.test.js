import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function readSource(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8')
}

test('Skill 与共享详情框架使用居中自适应 Tab 且不提供全屏入口', () => {
  const source = readSource('../../src/components/extensions/SkillDetailView.vue')
  const layout = readSource('../../src/components/shared/ExtensionDetailLayout.vue')
  const extensionStyles = readSource('../../src/assets/css/extensions.less')

  assert.match(source, /<ExtensionDetailLayout/)
  assert.match(source, /<template #breadcrumb>/)
  assert.match(source, /<template #actions>/)
  assert.match(source, /<template #panel-editor>/)
  assert.match(source, /<template #panel-config>/)
  assert.match(
    source,
    /key: 'editor',[\s\S]*?label: '代码管理',[\s\S]*?icon: FileText,[\s\S]*?panelClass: 'extension-detail-panel-fixed'/
  )
  assert.match(source, /key: 'config', label: '配置', icon: Settings/)
  assert.doesNotMatch(source, /<a-tab-pane/)
  assert.match(source, /<h3>可用范围<\/h3>/)
  assert.match(source, /<h3>运行依赖<\/h3>/)
  assert.match(source, /extension-detail-view extension-detail-gray-switches config-view/)
  assert.match(source, /const treeVisible = ref\(false\)/)
  assert.match(source, /<Transition name="skill-tree">/)
  assert.match(source, /v-if="treeVisible" id="skill-project-tree"/)
  assert.match(source, /width: min\(calc\(100% - 48px\), 768px\)/)
  assert.match(source, /width: min\(calc\(100% - 48px\), 1100px\)/)
  assert.match(source, /:show-inline-html-controls="true"/)
  assert.doesNotMatch(source, /:show-fullscreen="true"/)

  assert.match(layout, /class="minimal-tabs extension-detail-tabs"/)
  assert.match(layout, /<template #leftExtra>/)
  assert.match(layout, /<template v-if="\$slots\.actions" #rightExtra>/)
  assert.match(
    layout,
    /<a-tab-pane v-for="tab in tabs" :key="tab.key" :force-render="tab.forceRender === true">/
  )
  assert.match(layout, /<slot :name="`panel-\$\{tab\.key\}`"/)
  assert.doesNotMatch(layout, /@click="emit\('update:activeKey'/)
  assert.match(source, /<template #overlays>/)
  assert.match(layout, /<slot name="overlays" \/>/)
  assert.doesNotMatch(layout, /:deep\(\.extension-panel-action-secondary/)
  assert.match(
    extensionStyles,
    /\.extension-panel-action \{[\s\S]*?\.extension-panel-action-secondary[\s\S]*?\.extension-panel-action-danger/
  )
  assert.match(extensionStyles, /:focus-visible:not\(:disabled\)/)
  assert.match(layout, /:deep\(\.extension-detail-view\) \{[\s\S]*?margin: 48px auto;/)
  assert.doesNotMatch(layout, /extension-detail-view-spacious/)
  assert.match(layout, /\.extension-detail-panel \{[\s\S]*?overflow-y: auto;/)
  assert.match(layout, /\.extension-detail-panel-fixed[\s\S]*?overflow: hidden;/)
  assert.match(
    layout,
    /\.extension-detail-gray-switches \.ant-switch\.ant-switch-checked[\s\S]*?var\(--gray-700\)/
  )
})

test('文件树入口只显示图标，编辑入口属于项目结构操作区', () => {
  const source = readSource('../../src/components/extensions/SkillDetailView.vue')
  const actionsStart = source.indexOf('<template #actions>')
  const topBarActions = source.slice(actionsStart, source.indexOf('</template>', actionsStart))
  const treeActionsStart = source.indexOf('<div class="tree-actions">')
  const treeActions = source.slice(
    treeActionsStart,
    source.indexOf('<div class="tree-content">', treeActionsStart)
  )

  assert.match(topBarActions, /<FolderTree :size="14" aria-hidden="true" \/>/)
  assert.doesNotMatch(topBarActions, /<span>文件树<\/span>/)
  assert.doesNotMatch(topBarActions, /startEditingCurrentFile/)
  assert.match(topBarActions, /aria-label="导出 Skill"/)
  assert.match(topBarActions, /aria-label="删除 Skill"/)
  assert.match(treeActions, /aria-label="编辑当前文件"/)
  assert.match(treeActions, /@click="startEditingCurrentFile"/)
})

test('MCP 详情复用统一框架、内容宽度和无卡片分隔列表', () => {
  const source = readSource('../../src/components/extensions/McpDetailView.vue')

  assert.match(source, /<ExtensionDetailLayout/)
  assert.match(source, /<template #breadcrumb>/)
  assert.match(source, /<template #actions>/)
  assert.match(source, /<template #panel-general>/)
  assert.match(source, /<template #panel-tools>/)
  assert.match(source, /key: 'general', label: '信息', icon: Settings2/)
  assert.match(source, /key: 'tools', label: `工具 \(\$\{tools\.value\.length\}\)`, icon: Wrench/)
  assert.match(source, /class="extension-detail-view mcp-general-view"/)
  assert.match(source, /extension-detail-view extension-detail-gray-switches tools-tab/)
  assert.match(source, /tool-card extension-detail-divider-row/)
  assert.doesNotMatch(source, /class="detail-top-bar"/)
  assert.doesNotMatch(source, /<a-tabs/)
  assert.match(source, /:aria-label="testLoading \? '正在测试 MCP' : '测试 MCP'"/)
  assert.match(source, /aria-label="编辑 MCP"/)
  assert.match(source, /:aria-label="`\$\{actionLabel\} MCP`"/)
  assert.match(
    source,
    /:aria-label="`\$\{tool\.name\} \$\{tool\.enabled \? '已启用' : '已禁用'\}`"/
  )
  assert.match(source, /\.tool-card \{[\s\S]*?background: transparent;[\s\S]*?border: 0;/)
  assert.match(
    source,
    /@media \(max-width: 768px\)[\s\S]*?\.form-grid,[\s\S]*?\.form-grid-three[\s\S]*?grid-template-columns: minmax\(0, 1fr\)/
  )
})

test('共享权限组件隐藏解释和关闭状态占位文案', () => {
  const source = readSource('../../src/components/ShareConfigForm.vue')

  assert.match(source, /<a-switch\s+size="small"/)
  assert.match(
    source,
    /:aria-label="`\$\{scope\.title\}\$\{scopes\[scope\.key\] \? '已开启' : '已关闭'\}`"/
  )
  assert.doesNotMatch(source, /checked-children=/)
  assert.doesNotMatch(source, /un-checked-children=/)
  assert.doesNotMatch(source, /这些用户可以浏览、预览、下载和使用资源。/)
  assert.doesNotMatch(source, /未设置管理范围，读取用户只能查看和使用。/)
  assert.doesNotMatch(source, /class="permission-scope-empty"/)
  assert.doesNotMatch(source, /\.permission-scope-section \{[^}]*border-bottom:/)
  assert.doesNotMatch(source, /\.permission-scope-section \{[^}]*border-radius: 12px/)
})

test('保存运行依赖不会重载并覆盖同页尚未保存的范围配置', () => {
  const source = readSource('../../src/components/extensions/SkillDetailView.vue')
  const saveStart = source.indexOf('const saveDependencies = async () =>')
  const saveDependencies = source.slice(saveStart, source.indexOf('onMounted(', saveStart))

  assert.ok(saveStart >= 0)
  assert.doesNotMatch(saveDependencies, /fetchSkillDetail\(\)/)
})

test('无预览 header 的 HTML 文件在编辑态隐藏模式控件', () => {
  const source = readSource('../../src/components/AgentFilePreview.vue')
  const controlsStart = source.indexOf('showInlineHtmlControls &&')
  const controls = source.slice(
    controlsStart,
    source.indexOf('class="preview-mode-switch', controlsStart)
  )

  assert.ok(controlsStart >= 0)
  assert.match(controls, /!\(canEdit && editMode === 'edit'\)/)
})
