import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildChunkParamsPayload,
  buildKnowledgeIndexingDefaultsPayload,
  createIndexingParams,
  validateParentChildConfig
} from '../../src/utils/chunkUtils.js'

const readSource = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')

test('入库参数深拷贝知识库默认值，并由文件处理参数逐字段覆盖', () => {
  const databaseParams = {
    embedding_features: { bge_m3_sparse_enabled: true },
    parent_child: {
      enabled: true,
      parent_token_num: 1200,
      child_token_num: 240,
      child_overlap_percent: 20,
      separator: '\\n\\n'
    }
  }
  const processingParams = {
    chunk_preset_id: 'paper',
    chunk_parser_config: { chunk_token_num: 768 },
    parent_child: { child_token_num: 300 }
  }

  const params = createIndexingParams(databaseParams, processingParams)

  assert.deepEqual(params, {
    chunk_preset_id: 'paper',
    chunk_parser_config: { chunk_token_num: 768 },
    embedding_features: { bge_m3_sparse_enabled: true },
    parent_child: {
      enabled: true,
      parent_token_num: 1200,
      child_token_num: 300,
      child_overlap_percent: 20,
      separator: '\\n\\n',
      text_preservation: 'parsed_markdown_raw'
    }
  })
  params.parent_child.parent_token_num = 1600
  params.embedding_features.bge_m3_sparse_enabled = false
  assert.equal(databaseParams.parent_child.parent_token_num, 1200)
  assert.equal(databaseParams.embedding_features.bge_m3_sparse_enabled, true)
})

test('任务载荷保留关闭开关并过滤知识库级 tokenizer_policy', () => {
  const payload = buildChunkParamsPayload({
    chunk_preset_id: '',
    chunk_parser_config: { chunk_token_num: 512, overlapped_percent: 0 },
    embedding_features: { bge_m3_sparse_enabled: false },
    parent_child: {
      enabled: false,
      parent_token_num: 1000,
      child_token_num: 200,
      child_overlap_percent: 15,
      separator: '\\n',
      tokenizer_policy: 'embedding_provider_or_tiktoken',
      tokenizer_id: 'provider:model',
      text_preservation: 'parsed_markdown_raw'
    }
  })

  assert.deepEqual(payload, {
    chunk_parser_config: { chunk_token_num: 512, overlapped_percent: 0 },
    embedding_features: { bge_m3_sparse_enabled: false },
    parent_child: {
      enabled: false,
      parent_token_num: 1000,
      child_token_num: 200,
      child_overlap_percent: 15,
      separator: '\\n',
      tokenizer_id: 'provider:model',
      text_preservation: 'parsed_markdown_raw'
    }
  })
})

test('知识库默认值载荷保留稀疏与 Parent-Child 配置', () => {
  const payload = buildKnowledgeIndexingDefaultsPayload({
    chunk_preset_id: 'general',
    embedding_features: { bge_m3_sparse_enabled: false },
    parent_child: {
      enabled: true,
      parent_token_num: 1400,
      child_token_num: 280,
      child_overlap_percent: 10,
      separator: '---',
      tokenizer_policy: 'embedding_provider_or_tiktoken'
    }
  })

  assert.equal(payload.embedding_features.bge_m3_sparse_enabled, false)
  assert.equal(payload.parent_child.enabled, true)
  assert.equal(payload.parent_child.tokenizer_policy, 'embedding_provider_or_tiktoken')
  assert.equal('tokenizer_id' in payload.parent_child, false)
})

test('Parent-Child 前端校验拒绝父块不大于子块', () => {
  assert.equal(
    validateParentChildConfig({
      parent_child: { enabled: true, parent_token_num: 256, child_token_num: 256 }
    }),
    '父块 Token 数必须大于子块 Token 数'
  )
})

test('上传、文件任务与知识库保存都使用统一索引能力契约', () => {
  const uploadSource = readSource('../../src/components/FileUploadModal.vue')
  const tableSource = readSource('../../src/components/FileTable.vue')
  const detailSource = readSource('../../src/views/DataBaseInfoView.vue')

  assert.match(uploadSource, /createIndexingParams\(store\.database\?\.additional_params\)/)
  assert.match(uploadSource, /Object\.assign\(params, buildAutoIndexParams\(\)\)/)
  assert.match(tableSource, /createIndexingParams\(store\.database\?\.additional_params, processingParams\)/)
  assert.match(tableSource, /store\.resliceFiles\(currentIndexFileIds\.value, params\)/)
  assert.match(detailSource, /buildKnowledgeIndexingDefaultsPayload\(editForm\)/)
  assert.doesNotMatch(detailSource, /handleEditSubmit[\s\S]*store\.resliceFiles/)
})
