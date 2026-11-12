import { getToolCallId, parseToolCallArgs, parseToolCallResult, getToolCallStatus } from './toolCallProjection.js'

export const COOPERATION_ACTIONS = {
  create_session: '创建',
  submit_input: '提交工作至',
  send_message: '发送消息至',
  cancel_turn: '停止',
  wait_sessions: '等待',
  wait_inputs: '等待',
  get_result: '查看结果',
  list_sessions: '查看会话'
}

/** 从明确的工具回执建立任务与会话的对应关系。 */
export const collectCooperationTasks = (messages) => {
  const tasks = new Map()
  for (const message of messages) {
    for (const call of message.tool_calls || []) {
      if (!(getToolCallId(call) in COOPERATION_ACTIONS)) continue
      const result = parseToolCallResult(call)
      for (const item of [result, ...(result?.results || [])]) {
        if (item?.input_id && item.session_id) tasks.set(item.input_id, item.session_id)
      }
    }
  }
  return tasks
}

/** 使用精确会话 ID、路径或任务回执生成协作入口，不按名称猜测归属。 */
export const getCooperationToolTargets = (call, { sessions, tasks, treeRootSessionId }) => {
  const id = getToolCallId(call)
  const args = parseToolCallArgs(call)
  const result = parseToolCallResult(call)
  let targets
  if (id === 'list_sessions') targets = result?.sessions || []
  else if (id === 'wait_inputs') {
    targets = (args.input_ids || []).map((inputId) => ({
      session_id: result?.results?.find((item) => item.input_id === inputId)?.session_id || tasks.get(inputId)
    }))
  } else if (id === 'get_result') {
    targets = [{ session_id: result?.session_id || tasks.get(args.input_id) ||
      sessions.find((member) => args.turn_id && member.turn_id === args.turn_id)?.session_id }]
  } else if (id === 'wait_sessions') targets = (args.targets || []).map((target) => ({ target }))
  else targets = [{ session_id: result?.session_id || result?.thread_id, target: args.target, name: result?.name || args.name,
    path: result?.path }]

  return targets.map((target) => {
    const session = sessions.find((member) =>
      (target.session_id && member.session_id === target.session_id) ||
      (target.target && (member.session_id === target.target ||
        (member.path === target.target && (!treeRootSessionId || member.tree_root_session_id === treeRootSessionId))))
    )
    return {
      sessionId: target.session_id || session?.session_id || (/^[0-9a-f-]{36}$/i.test(target.target || '') ? target.target : undefined),
      name: session?.title || session?.name || target.title || target.name || (target.path || (target.target?.startsWith('/') ? target.target : ''))?.split('/').pop() || ''
    }
  }).filter((target, index, list) => !target.sessionId ||
    list.findIndex((item) => item.sessionId === target.sessionId) === index)
}

/** 只合并相邻、同动作同状态的协作调用，正文、推理与错误始终保留边界。 */
export function groupCooperationEntries(entries) {
  const grouped = []
  for (const entry of entries) {
    const name = entry.type === 'tool' ? getToolCallId(entry.toolCall) : ''
    if (!(name in COOPERATION_ACTIONS)) {
      grouped.push(entry)
      continue
    }
    const status = getToolCallStatus(entry.toolCall)
    const waitTimedOut = Boolean(parseToolCallResult(entry.toolCall)?.wait_timed_out)
    const previous = grouped.at(-1)
    if (previous?.type === 'cooperation' && previous.name === name && previous.status === status &&
        previous.waitTimedOut === waitTimedOut &&
        !['error', 'incomplete'].includes(status) && name !== 'list_sessions') {
      previous.toolCalls.push(entry.toolCall)
    } else {
      grouped.push({ type: 'cooperation', key: entry.key, name, status, waitTimedOut, toolCalls: [entry.toolCall] })
    }
  }
  return grouped
}
