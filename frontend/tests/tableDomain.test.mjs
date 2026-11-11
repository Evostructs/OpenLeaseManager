import assert from 'node:assert/strict'

import { dunningStatusTone, invoiceStatusTone } from '../src/lib/receivableStatusTone.js'
import {
  clampPager,
  listTotalPages,
  pageLabel,
  pageRows,
  showingText
} from '../src/lib/table.js'

assert.equal(listTotalPages(0, 25), 1)
assert.equal(listTotalPages(25, 25), 1)
assert.equal(listTotalPages(136, 25), 6)
assert.equal(listTotalPages(136, 0), 1)

assert.deepEqual(pageRows([1, 2, 3, 4, 5], { page: 2, pageSize: 2 }), [3, 4])
assert.deepEqual(pageRows([1, 2, 3], { page: 5, pageSize: 2 }), [])
assert.deepEqual(pageRows([1, 2, 3], { page: 1, pageSize: 0 }), [1, 2, 3])

assert.equal(pageLabel({ page: 2, pageSize: 25 }, 6), '2 / 6')
assert.equal(pageLabel({ page: 1, pageSize: 0 }, 1), '全部')

assert.equal(showingText(136, { page: 2, pageSize: 25 }), '显示 26-50 条')
assert.equal(showingText(136, { page: 6, pageSize: 25 }), '显示 126-136 条')
assert.equal(showingText(0, { page: 1, pageSize: 25 }), '显示 0-0 条')
assert.equal(showingText(136, { page: 1, pageSize: 0 }), '显示全部 136 条')

const overPager = { page: 9, pageSize: 25 }
clampPager(overPager, 6)
assert.equal(overPager.page, 6)
const underPager = { page: 0, pageSize: 25 }
clampPager(underPager, 3)
assert.equal(underPager.page, 1)

assert.equal(invoiceStatusTone('已开票'), 'success')
assert.equal(invoiceStatusTone('红冲/作废'), 'error')
assert.equal(invoiceStatusTone('待开票'), 'warning')
assert.equal(invoiceStatusTone('无需开票'), 'info')
assert.equal(invoiceStatusTone('未开票'), 'warning')

assert.equal(dunningStatusTone('已升级'), 'error')
assert.equal(dunningStatusTone('催收中'), 'warning')
assert.equal(dunningStatusTone('已提醒'), 'warning')
assert.equal(dunningStatusTone('无需催收'), 'success')
assert.equal(dunningStatusTone('未催收'), 'info')
