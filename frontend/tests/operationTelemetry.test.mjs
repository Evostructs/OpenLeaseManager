import test from 'node:test'
import assert from 'node:assert/strict'
import { createOperationTelemetry, safeEndpoint } from '../src/lib/operationTelemetry.js'
function harness() {
 let time=0, invisible=false;const frames=[];const timers=new Map();let id=0
 const engine=createOperationTelemetry({now:()=>time,frame:fn=>frames.push(fn),hidden:()=>invisible,timer:fn=>{timers.set(++id,fn);return id},cancelTimer:id=>timers.delete(id)})
 return {engine,advance:ms=>time+=ms,hide:()=>invisible=true,frame:()=>{time+=16;frames.shift()?.()},expire:()=>[...timers.values()].forEach(fn=>fn())}
}
test('readiness waits for response, lazy component and chart work instead of a fixed delay', async()=>{
 const h=harness();const operation=h.engine.begin('login','dashboard');const api=h.engine.task('api')
 h.advance(140);operation.finish('ok');await Promise.resolve();h.frame();assert.equal(h.engine.snapshot().operations.length,0)
 api();const chart=h.engine.task('chart-render');h.advance(1100);h.frame();assert.equal(h.engine.snapshot().operations.length,0)
 chart();h.frame();h.frame();const sample=h.engine.snapshot().operations[0]
 assert.equal(sample.dataReadyMs,140);assert.equal(sample.readyMs,1304);assert.equal(sample.status,'ok')
})
test('new work between frames delays completion, duplicate finish is harmless',async()=>{
 const h=harness();const op=h.engine.begin('contract-filter','contracts');op.finish();op.finish();await Promise.resolve();h.frame()
 const work=h.engine.task('api');h.frame();assert.equal(h.engine.snapshot().operations.length,0);work();work();h.frame();h.frame()
 assert.equal(h.engine.snapshot().operations.length,1)
})
test('background and incomplete operations never become successful readiness samples',async()=>{
 const h=harness();h.engine.begin('receipt-save').finish();h.hide();await Promise.resolve();h.frame();assert.equal(h.engine.snapshot().operations[0].status,'background')
 const x=harness();x.engine.task('api');x.engine.begin('receipt-save').finish();x.expire();assert.equal(x.engine.snapshot().operations[0].status,'incomplete')
})
test('request spans separate server, headers and body time and redact query and object ids',async()=>{
 const h=harness();const op=h.engine.begin('financial-save','planning');h.advance(250)
 h.engine.request({endpoint:'/api/contracts/123?private-query=not-retained',start:0,duration:250,headersMs:230,bodyMs:20,serverMs:180,status:503})
 op.finish();await Promise.resolve();h.frame();h.frame();const sample=h.engine.snapshot().operations[0]
 assert.equal(sample.status,'degraded');assert.equal(sample.requests[0].endpoint,'/api/contracts/:id');assert.equal(sample.requests[0].serverMs,180)
 assert.equal(safeEndpoint('/api/contracts/123/payment-operations/26b40162-a9ab-44f2-bf57-49a2a87339a0'),'\/api/contracts/:id/payment-operations/:id')
})
test('reset excludes stale completions and snapshots cannot mutate request history',async()=>{
 const h=harness();const old=h.engine.begin('login');old.finish();h.engine.reset();await Promise.resolve();h.frame();h.frame();assert.equal(h.engine.snapshot().operations.length,0)
 const op=h.engine.begin('login');h.engine.request({endpoint:'/api/auth/me',start:32,duration:1,status:200});op.finish();await Promise.resolve();h.frame();h.frame()
 h.engine.snapshot().operations[0].requests[0].status=500;assert.equal(h.engine.snapshot().operations[0].requests[0].status,200)
})
