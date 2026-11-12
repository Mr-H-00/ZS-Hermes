import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createSearchConfigSnapshot,
  isSearchParamVisible,
  searchConfigChanged
} from '../../src/modules/knowledge/model/searchConfig.js'

test('检索配置快照只包含服务端声明的参数', () => {
  const snapshot = createSearchConfigSnapshot(
    [{ key: 'top_k' }, { key: 'use_rerank' }],
    { top_k: 5, use_rerank: true, stale_local_value: 'ignore' }
  )

  assert.deepEqual(snapshot, { top_k: 5, use_rerank: true })
})

test('检索配置恢复原值后不再标记为 dirty', () => {
  const initial = { top_k: 5, use_rerank: true }

  assert.equal(searchConfigChanged({ top_k: 10, use_rerank: true }, initial), true)
  assert.equal(searchConfigChanged({ top_k: 5, use_rerank: true }, initial), false)
})

test('融合参数同时满足依赖、任一检索模式和全部附加条件才可见', () => {
  const param = {
    depend_on: ['use_vector_score_fusion', true],
    visible_when: {
      any: [{ search_mode: 'vector' }, { use_graph_retrieval: true }],
      all: [{ rerank_enabled: true }, { provider: 'local' }]
    }
  }
  const ready = {
    use_vector_score_fusion: true,
    search_mode: 'vector',
    use_graph_retrieval: false,
    rerank_enabled: true,
    provider: 'local'
  }
  assert.equal(isSearchParamVisible(param, ready), true)
  assert.equal(isSearchParamVisible(param, { ...ready, use_vector_score_fusion: false }), false)
  assert.equal(isSearchParamVisible(param, { ...ready, search_mode: 'keyword' }), false)
  assert.equal(isSearchParamVisible(param, { ...ready, provider: 'remote' }), false)
  assert.equal(isSearchParamVisible(param, { ...ready, rerank_enabled: false }), false)
  assert.equal(
    isSearchParamVisible(param, { ...ready, search_mode: 'keyword', use_graph_retrieval: true }),
    true
  )
})

test('可见性条件对象要求所有键值都相等，普通参数保持可见', () => {
  const param = { visible_when: { any: [{ search_mode: 'hybrid', use_graph_retrieval: true }] } }
  assert.equal(isSearchParamVisible(param, { search_mode: 'hybrid', use_graph_retrieval: false }), false)
  assert.equal(isSearchParamVisible(param, { search_mode: 'hybrid', use_graph_retrieval: true }), true)
  assert.equal(isSearchParamVisible({ key: 'top_k' }, {}), true)
})
