const conversationTimestamp = (conversation) => {
  const timestamp = Date.parse(conversation.created_at || '')
  return Number.isNaN(timestamp) ? 0 : timestamp
}

const sortSidebarConversations = (conversations) =>
  [...conversations].sort((left, right) => {
    if (left.is_pinned !== right.is_pinned) return left.is_pinned ? -1 : 1
    return conversationTimestamp(right) - conversationTimestamp(left)
  })

export const buildProjectConversationGroups = (projects, conversations) => {
  const sortedConversations = sortSidebarConversations(conversations)
  const activeProjects = projects.filter(
    (project) => project.status !== 'deleted' && project.selection_status === 'selectable'
  )
  const conversationsByProject = new Map(activeProjects.map((project) => [project.id, []]))
  const otherConversations = []

  sortedConversations.forEach((conversation) => {
    const projectConversations = conversationsByProject.get(conversation.project_id)
    if (projectConversations) {
      projectConversations.push(conversation)
    } else {
      otherConversations.push(conversation)
    }
  })

  return {
    groups: activeProjects.map((project) => ({
      project,
      conversations: conversationsByProject.get(project.id)
    })),
    otherConversations
  }
}
