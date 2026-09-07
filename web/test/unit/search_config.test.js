import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createSearchConfigSnapshot,
  isSearchParamVisible,
  searchConfigChanged
} from '../../src/utils/searchConfig.js'

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

test('检索参数可见性同时执行 any、all 和 depend_on 谓词', () => {
  const param = {
    depend_on: ['use_vector_score_fusion', true],
    visible_when: {
      any: [{ search_mode: 'vector' }, { use_graph_retrieval: true }],
      all: [{ rerank_enabled: true }, { provider: 'local' }]
    }
  }

  assert.equal(
    isSearchParamVisible(param, {
      use_vector_score_fusion: false,
      search_mode: 'vector',
      rerank_enabled: true,
      provider: 'local'
    }),
    false
  )
  assert.equal(
    isSearchParamVisible(param, {
      use_vector_score_fusion: true,
      search_mode: 'keyword',
      use_graph_retrieval: false,
      rerank_enabled: true,
      provider: 'local'
    }),
    false
  )
  assert.equal(
    isSearchParamVisible(param, {
      use_vector_score_fusion: true,
      search_mode: 'vector',
      rerank_enabled: true,
      provider: 'remote'
    }),
    false
  )
  assert.equal(
    isSearchParamVisible(param, {
      use_vector_score_fusion: true,
      search_mode: 'keyword',
      use_graph_retrieval: true,
      rerank_enabled: true,
      provider: 'local'
    }),
    true
  )
})

test('可见性条件对象要求所有键值都相等', () => {
  const param = {
    visible_when: {
      any: [{ search_mode: 'hybrid', use_graph_retrieval: true }]
    }
  }

  assert.equal(
    isSearchParamVisible(param, { search_mode: 'hybrid', use_graph_retrieval: false }),
    false
  )
  assert.equal(
    isSearchParamVisible(param, { search_mode: 'hybrid', use_graph_retrieval: true }),
    true
  )
})
