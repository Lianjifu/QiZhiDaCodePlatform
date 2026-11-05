import assert from 'node:assert/strict'

import { longTermDetail, longTermReadyLabel } from '../src/features/autonomy/longTerm.ts'

/** 造一个长期记忆状态。 */
function status(overrides = {}) {
  return {
    ready: false,
    collection_name: 'agent-memory',
    collection_id: null,
    embedding_model: '',
    embedding_dim: 0,
    keyword_only: false,
    doc_count: 0,
    reason: 'collection_missing',
    hint: '集合 agent-memory 还不存在。',
    provider_name: '',
    ...overrides,
  }
}

let passed = 0
function check(name, fn) {
  fn()
  passed += 1
  console.log(`  ✓ ${name}`)
}

check('没建好就是未初始化', () => {
  assert.equal(longTermReadyLabel(status()), '未初始化')
  assert.equal(longTermReadyLabel(status({ ready: true })), '已就绪')
})

check('没建好时给的是可操作的那句话', () => {
  assert.equal(longTermDetail(status()), '集合 agent-memory 还不存在。')
})

check('就绪时报清楚集合里有什么', () => {
  const text = longTermDetail(
    status({
      ready: true,
      collection_id: 3,
      doc_count: 12,
      embedding_model: 'text-embedding-3-small',
      embedding_dim: 1536,
    }),
  )
  assert.ok(text.includes('12 条记忆'))
  assert.ok(text.includes('text-embedding-3-small'))
  assert.ok(text.includes('1536'))
})

check('只有关键词索引时说清楚召回还在、语义不在', () => {
  const text = longTermDetail(status({ ready: true, keyword_only: true, doc_count: 3 }))
  assert.ok(text.includes('关键词'))
  assert.ok(text.includes('语义检索用不上'))
})

console.log(`\n${passed} 条通过`)
