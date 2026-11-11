import test from 'node:test'
import assert from 'node:assert/strict'
import { applyStrategyReference, detachStrategyReference } from '../src/lib/strategyReference.js'
const source={id:7,kind:'forecast',name:'固定预测',year:2026,revision:2,source_digest:'digest',snapshot:{totals:{net_cash_flow:100.25},coverage:{property_fee_cash_included:false}}}
test('fixed source plus explicit adjustments preserves other strategy assumptions',()=>{
 const original={hold:{annualNetCashFlow:1,exitPrice:500},renovate:{annualNetCashFlow:2,capex:30}}
 const linked=applyStrategyReference(original,source,{hold:-2,renovate:3,reason:'经常性资本支出与改造收益'})
 assert.equal(linked.hold.annualNetCashFlow,98.25);assert.equal(linked.renovate.annualNetCashFlow,103.25)
 assert.equal(linked.hold.exitPrice,500);assert.equal(original.hold.annualNetCashFlow,1)
 assert.equal(linked.reference.version_id,7);assert.equal(linked.reference.property_fee_cash_included,false)
 source.snapshot.totals.net_cash_flow=200
 assert.equal(linked.reference.net_cash_flow,100.25)
 const manual=detachStrategyReference(linked);assert.equal(manual.hold.annualNetCashFlow,98.25);assert.equal(manual.reference,undefined)
})
test('zero is allowed, missing cash or adjustment explanations are not',()=>{
 assert.equal(applyStrategyReference({}, {...source,snapshot:{totals:{net_cash_flow:0}}}).hold.annualNetCashFlow,0)
 assert.throws(()=>applyStrategyReference({}, {...source,kind:'budget'}))
 assert.throws(()=>applyStrategyReference({}, {...source,snapshot:{totals:{}}}))
 assert.throws(()=>applyStrategyReference({},source,{hold:1,reason:''}))
 assert.throws(()=>applyStrategyReference({},source,{hold:''}))
})
