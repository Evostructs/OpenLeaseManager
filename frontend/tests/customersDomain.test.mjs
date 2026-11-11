import assert from 'node:assert/strict'

import {
  compactCurrency,
  customerRiskScore,
  customerRiskSignals,
  customerRiskTier,
  customerValueTier,
  roomsText,
  shareValue
} from '../src/lib/customersDomain.js'

assert.equal(shareValue(25, 100), 25)
assert.equal(customerValueTier({ rent: 20000, rentShare: 25 }, 2), 'high')
assert.equal(customerRiskTier({ overdueBalance: 100 }), 'high')
assert.equal(customerRiskScore({ rentShare: 20, overdueBalance: 100, maxAgingDays: 30 }), 80)
assert.equal(compactCurrency(12500), '1.3万')
assert.equal(roomsText({ rooms: ['S0301', 'S0302'], room_count: 2 }), 'S0301、S0302')
assert.deepEqual(customerRiskSignals({ overdueBalance: 12500, followUpLabel: '本周跟进', attachmentGap: '合同' }), [
  '应收 1.3万',
  '本周跟进',
  '缺合同'
])
