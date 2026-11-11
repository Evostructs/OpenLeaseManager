import assert from 'node:assert/strict'

import {
  buildImportSuccessResult,
  createContractDomain,
  formatImportIssue,
  roomSummary
} from '../src/lib/contractsDomain.js'

const domain = createContractDomain({
  activeStatuses: new Set(['在租', '即将到期', '逾期占用']),
  closedStatuses: new Set(['已到期', '已续租', '已变更', '已退租'])
})

assert.equal(domain.isExpiringSoon({ status: '在租', days_to_expiry: 30 }), true)
assert.equal(domain.expiryLabel({ days_to_expiry: -3 }), '逾期 3 天')
assert.equal(domain.depositAttentionTag({ deposit_summary: { unpaid_amount: 10 } }), '押金未收')
assert.deepEqual(domain.contractNextAction({ status: '在租', days_to_expiry: 20 }), {
  label: '续签',
  detail: '20 天到期',
  tone: 'warning'
})
assert.equal(roomSummary({ room_items: [{}, {}], room_no: 'S0301' }), '2 间 · S0301')
assert.equal(formatImportIssue({ row: 4, contract_no: 'C-4', reason: '缺少起租日' }), '第4行 合同 C-4: 缺少起租日')
assert.deepEqual(buildImportSuccessResult({ imported: 2, skipped: 1, skipped_rows: [{ row: 3, reason: '重复' }] }).issues, ['第3行: 重复'])
