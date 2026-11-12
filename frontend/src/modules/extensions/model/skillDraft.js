import { load, dump } from 'js-yaml'

const dependencyKeys = ['tool_dependencies', 'mcp_dependencies', 'skill_dependencies']

function splitRoot(content) {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)([\s\S]*)$/)
  if (!match) throw new Error('请先修正 SKILL.md 的前置元数据')
  let metadata
  try {
    metadata = load(match[1])
  } catch (error) {
    const folded = match[1].replace(/^description:[ \t]*\r?\n(?=[ \t]+\S)/m, 'description: >-\n')
    if (folded === match[1]) throw error
    metadata = load(folded)
  }
  if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata))
    throw new Error('SKILL.md 前置元数据必须是对象')
  return { metadata, body: match[2] }
}

export function readDraftDependencies(content) {
  const { metadata } = splitRoot(content)
  return Object.fromEntries(
    dependencyKeys.map((key) => {
      const values = metadata[key] || []
      if (!Array.isArray(values) || values.some((value) => typeof value !== 'string'))
        throw new Error(`${key} 必须是字符串列表`)
      return [key, [...values]]
    })
  )
}

export function writeDraftDependencies(content, dependencies) {
  const { metadata, body } = splitRoot(content)
  for (const key of dependencyKeys) metadata[key] = dependencies[key]
  return `---\n${dump(metadata)}---\n${body}`
}

export function collectSkillChanges(saved, draft, nodes) {
  return [
    ...nodes,
    ...Object.entries(draft)
      .filter(([path, content]) => saved[path] !== content)
      .map(([path, content]) => ({
        action: path in saved || nodes.some((node) => node.path === path) ? 'write' : 'create',
        path,
        content
      }))
  ]
}
