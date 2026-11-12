const sessionTimestamp = (session) => {
  const timestamp = session.created_at * 1000
  return Number.isNaN(timestamp) ? 0 : timestamp
}

const sortSidebarSessions = (sessions) =>
  [...sessions].sort((left, right) => {
    if (left.yuxi.is_pinned !== right.yuxi.is_pinned) return left.yuxi.is_pinned ? -1 : 1
    return sessionTimestamp(right) - sessionTimestamp(left)
  })

export const deriveProjectWorkStatus = (sessions) =>
  ['requires_action', 'in_progress', 'failed', 'cancelled', 'completed'].find(
    (status) => sessions.some((session) => session.status === status)
  ) || 'idle'

export const buildProjectSessionGroups = (projects, sessions) => {
  const sortedSessions = sortSidebarSessions(sessions)
  const pinnedSessions = sortedSessions.filter((session) => session.yuxi.is_pinned)
  const activeProjects = projects.filter(
    (project) => project.status !== 'deleted' && project.selection_status === 'selectable'
  )
  const sessionsByProject = new Map(activeProjects.map((project) => [project.id, []]))
  const otherSessions = []

  sortedSessions.forEach((session) => {
    const projectSessions = sessionsByProject.get(session.yuxi.project_id)
    if (projectSessions) {
      projectSessions.push(session)
    } else if (!session.yuxi.is_pinned) {
      otherSessions.push(session)
    }
  })

  return {
    pinnedSessions,
    groups: activeProjects.map((project) => {
      const projectSessions = sessionsByProject.get(project.id)
      return {
        project,
        sessions: projectSessions.filter((session) => !session.yuxi.is_pinned),
        status: deriveProjectWorkStatus(projectSessions),
        unread: projectSessions.some((session) => session.yuxi.unread)
      }
    }),
    otherSessions
  }
}
