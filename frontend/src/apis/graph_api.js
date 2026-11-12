import { apiGet } from './base'

export const graphApi = {
  getSubgraph: async (params) => {
    const {
      kb_id,
      file_id,
      node_label = '*',
      max_depth = 2,
      max_nodes = 100,
      exclude_chunk = false
    } = params

    if (!kb_id) {
      throw new Error('kb_id is required')
    }

    const queryParams = new URLSearchParams({
      kb_id,
      node_label,
      max_depth: max_depth.toString(),
      max_nodes: max_nodes.toString(),
      exclude_chunk: exclude_chunk.toString()
    })

    if (file_id) queryParams.set('file_id', file_id)

    return await apiGet(`/api/graph/subgraph?${queryParams.toString()}`, {}, true)
  }
}
