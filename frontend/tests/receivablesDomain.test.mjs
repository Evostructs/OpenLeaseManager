import assert from 'node:assert/strict'

import {
  compareReceivablePriority,
  hasReceivableActiveDunning,
  hasReceivableFollowUp,
  hasReceivablePendingInvoice,
  isReceivableOverdue,
  sumReceivableAmount
} from '../src/lib/receivablesDomain.js'

const overdue = {
  contract_id: 1,
  overdue_balance: 1200,
  balance: 1200,
  aging_days: 12,
  invoice_status: '已开票'
}
const followUp = {
  contract_id: 2,
  balance: 900,
  follow_up_due: true,
  dunning_status: '已提醒',
  invoice_status: '待开票'
}

assert.equal(sumReceivableAmount([overdue, followUp], 'balance'), 2100)
assert.equal(isReceivableOverdue(overdue), true)
assert.equal(hasReceivablePendingInvoice(followUp), true)
assert.equal(hasReceivableActiveDunning(followUp), true)
assert.equal(hasReceivableFollowUp(followUp), true)
assert.ok(compareReceivablePriority(overdue, followUp, new Set()) < 0)
assert.ok(compareReceivablePriority(followUp, overdue, new Set([2])) < 0)
