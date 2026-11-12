/** 只投影当前等待中的人工操作，不沿用已结束轮次的等待原因。 */
export function getSessionAttentionLabel(session) {
  if (session?.turn_status !== 'waiting') return ''
  if (session.waiting_for === 'approval') return '待审批'
  if (session.waiting_for === 'answer') return '待回答'
  return ''
}

/** 汇总其他协作成员的人工待办，导航不消费等待点。 */
export function getCooperationPendingActions(sessions, currentId) {
  return sessions
    .filter((session) => session.session_id !== currentId && getSessionAttentionLabel(session))
    .map((session) => ({
      sessionId: session.session_id,
      name: session.name || session.path?.split('/').pop() || '协作会话',
      label: getSessionAttentionLabel(session),
      action: session.waiting_for === 'approval' ? '查看审批' : '去回答'
    }))
}
