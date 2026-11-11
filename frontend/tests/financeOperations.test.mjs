import test from 'node:test'
import assert from 'node:assert/strict'
import { parseBankCsv } from '../src/lib/financeOperations.js'
const header='流水编号,日期,方向,金额,往来方,摘要,账户简称\n'
test('CSV preserves quoted separators and BOM, directions and decimal cents',()=>{
 const rows=parseBankCsv('\uFEFF'+header+'demo-1,2026-09-01,收入,12.34,"模拟,客户","说明\n续行",结算账户\n')
 assert.equal(rows.length,1);assert.equal(rows[0].amount,'12.34');assert.equal(rows[0].counterparty,'模拟,客户');assert.equal(rows[0].direction,'in')
})
test('ambiguous duplicate bank references and invalid amount rejected before import',()=>{
 assert.throws(()=>parseBankCsv(header+'a,2026-09-01,收入,1,客户,,\na,2026-09-01,收入,1,客户,,'),/重复/)
 assert.throws(()=>parseBankCsv(header+'b,2026-09-01,支出,-1,客户,,'),/无效/)
 assert.throws(()=>parseBankCsv(header+'b,2026-09-01,支出,1.234,客户,,'),/无效/)
})
