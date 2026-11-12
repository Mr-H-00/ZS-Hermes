export interface SessionRoute {
  threadId: string
  agentId: string
  memberSessionId?: string
}

export interface SessionSelection {
  selectThreadFromRoute(threadId: string): Promise<boolean | null>
  openSessionFromRoute(sessionId: string): Promise<void>
}

interface RouteActions {
  initializeAgents(): Promise<void>
  selectAgent(agentId: string): Promise<void>
  rejectThread(): Promise<unknown>
  consumeAgent(): Promise<unknown>
  onError(error: unknown): void
}

/** 串行消费路由选择，过期操作只完成内部清理，不回写当前路由。 */
export function createThreadRouteCoordinator(actions: RouteActions) {
  let revision = 0
  let tail = Promise.resolve()
  let queued = 0
  let disposed = false

  return {
    sync(target: SessionRoute, selection: SessionSelection | null): Promise<void> {
      const currentRevision = ++revision
      if (disposed || !selection) return Promise.resolve()
      const { threadId, agentId } = target
      const isCurrent = () => !disposed && revision === currentRevision
      queued += 1
      // 每次 sync 都在队尾注册自己的操作，结束窗口中的新请求也有执行者。
      tail = tail.then(async () => {
        if (!isCurrent()) return
        if (!threadId) await actions.initializeAgents()
        if (!isCurrent()) return
        const selected = await selection.selectThreadFromRoute(threadId)
        if (!isCurrent() || selected === null) return
        if (selected && target.memberSessionId !== undefined) {
          await selection.openSessionFromRoute(target.memberSessionId)
          if (!isCurrent()) return
        }
        if (threadId && !selected) {
          await actions.rejectThread()
        } else if (!threadId && agentId) {
          await actions.selectAgent(agentId)
          if (isCurrent()) await actions.consumeAgent()
        }
      }).catch((error: unknown) => {
        if (isCurrent()) actions.onError(error)
      }).finally(() => {
        queued -= 1
      })
      return tail
    },
    isSyncing(): boolean {
      return queued > 0
    },
    dispose(): void {
      disposed = true
      revision += 1
    }
  }
}
