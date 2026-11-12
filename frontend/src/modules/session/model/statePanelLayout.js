/** 右侧留白足够时嵌入状态面板，否则贴主对话区域右侧悬浮。 */
export const getStatePanelPlacement = (workspace, trigger, input, embeddedArea, mainArea = workspace) => {
  const embedded = embeddedArea && embeddedArea.right - embeddedArea.left >= 340
  const top = trigger.bottom - workspace.top + 16
  const bottom = Math.min(workspace.bottom, input?.top ?? workspace.bottom)
  const right = embedded
    ? workspace.right - embeddedArea.left - 340
    : workspace.right - Math.min(mainArea.right, workspace.right) + 8
  return {
    mode: embedded ? 'embedded' : 'floating',
    style: {
      top: `${top}px`,
      right: `${right}px`,
      width: `${embedded ? 340 : Math.min(340, Math.max(0, Math.min(mainArea.right, workspace.right) - Math.max(mainArea.left, workspace.left) - 16))}px`,
      maxHeight: `${Math.max(0, bottom - workspace.top - top - 8)}px`
    }
  }
}
