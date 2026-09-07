import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildDatabaseRequest,
  createEmptyDatabaseForm,
  selectDatabaseType,
  validateDatabaseConfig
} from '../../src/utils/databaseCreateForm.js'
import { getKbTypeLabel } from '../../src/utils/kb_utils.js'

const difyType = {
  create_params: {
    options: [
      { key: 'url', label: '地址', required: true },
      { key: 'token', label: 'Token', type: 'password', required: true }
    ]
  }
}

test('切换知识库类型保留通用字段并重置类型参数', () => {
  const form = { ...createEmptyDatabaseForm('embed/model'), name: '产品资料', description: '说明' }
  const selected = selectDatabaseType(form, 'dify', difyType)
  assert.equal(selected.name, '产品资料')
  assert.equal(selected.description, '说明')
  assert.deepEqual(selected.additional_params, { url: '', token: '' })
})

test('从连接器切回嵌入知识库时恢复可选索引默认配置', () => {
  const connectorForm = selectDatabaseType(createEmptyDatabaseForm('embed/model'), 'dify', difyType)
  const selected = selectDatabaseType(connectorForm, 'milvus', {
    requires_embedding_model: true,
    create_params: { options: [] }
  })

  assert.deepEqual(selected.additional_params.embedding_features, {
    bge_m3_sparse_enabled: false
  })
  assert.equal(selected.additional_params.parent_child.enabled, false)
  assert.deepEqual(selected.additional_params.chunk_parser_config, {
    chunk_token_num: 512,
    overlapped_percent: 0,
    delimiter: '\\n'
  })
})

test('配置校验拒绝空名称和必填动态字段', () => {
  const empty = selectDatabaseType(createEmptyDatabaseForm(), 'dify', difyType)
  assert.equal(validateDatabaseConfig(empty, difyType), '请输入知识库名称')
  empty.name = '资料'
  assert.equal(validateDatabaseConfig(empty, difyType), '请填写地址')
})

test('只为需要嵌入模型的类型构建模型和分块参数', () => {
  const form = {
    ...createEmptyDatabaseForm('embed/model'),
    name: '资料',
    kb_type: 'milvus',
    chunk_preset_id: 'general'
  }
  const request = buildDatabaseRequest(
    form,
    { requires_embedding_model: true, create_params: { options: [] } },
    { version: 2 },
    'fallback/model'
  )
  assert.equal(request.embedding_model_spec, 'embed/model')
  assert.equal(request.additional_params.chunk_preset_id, 'general')

  const connectorRequest = buildDatabaseRequest(
    { ...form, kb_type: 'dify' },
    difyType,
    { version: 2 },
    'fallback/model'
  )
  assert.equal('embedding_model_spec' in connectorRequest, false)
  assert.equal('chunk_preset_id' in connectorRequest.additional_params, false)
})

test('Parent-Child 关闭时提交原单层分块参数且不提交隐藏父子参数', () => {
  const form = {
    ...createEmptyDatabaseForm('provider:BAAI/bge-m3'),
    name: '原单层知识库',
    kb_type: 'milvus'
  }
  form.additional_params.chunk_parser_config = {
    chunk_token_num: 768,
    overlapped_percent: 12,
    delimiter: '\\n\\n'
  }
  form.additional_params.parent_child = {
    enabled: false,
    parent_token_num: 100,
    child_token_num: 200,
    child_overlap_percent: 120,
    separator: ''
  }

  assert.equal(
    validateDatabaseConfig(form, { requires_embedding_model: true, create_params: { options: [] } }),
    ''
  )

  const request = buildDatabaseRequest(
    form,
    { requires_embedding_model: true, create_params: { options: [] } },
    { version: 2 },
    'fallback/model'
  )

  assert.deepEqual(request.additional_params.chunk_parser_config, {
    chunk_token_num: 768,
    overlapped_percent: 12,
    delimiter: '\\n\\n'
  })
  assert.deepEqual(request.additional_params.parent_child, { enabled: false })
})

test('Parent-Child 开启时只提交父子块参数并停用原单层参数', () => {
  const form = {
    ...createEmptyDatabaseForm('provider:BAAI/bge-m3'),
    name: '父子块知识库',
    kb_type: 'milvus'
  }
  form.additional_params.parent_child.enabled = true

  const request = buildDatabaseRequest(
    form,
    { requires_embedding_model: true, create_params: { options: [] } },
    { version: 2 },
    'fallback/model'
  )

  assert.equal('chunk_parser_config' in request.additional_params, false)
  assert.deepEqual(request.additional_params.parent_child, form.additional_params.parent_child)
})

test('知识库类型标签映射将 milvus 解析为 Yuxi', () => {
  assert.equal(getKbTypeLabel('milvus'), 'Yuxi')
  assert.equal(getKbTypeLabel('Milvus'), 'Yuxi')
  assert.equal(getKbTypeLabel('dify'), 'Dify')
  assert.equal(getKbTypeLabel('notion'), 'Notion')
  assert.equal(getKbTypeLabel('unknown'), 'unknown')
})
