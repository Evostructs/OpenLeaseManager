import test from 'node:test'
import assert from 'node:assert/strict'
import { navigationViewQuery, cleanViewFilters, viewStorageKey, readSavedViews, saveView, buildViewLink, readViewLink } from '../src/lib/savedViews.js'
const storage = () => { const data = new Map(); return { getItem: key => data.get(key), setItem: (key,value) => data.set(key,value) } }
test('saved filters isolate user, building and page and persist only whitelisted preferences', () => {
  const s=storage(); const key=viewStorageKey({id:1},{type:'building',code:'A'},'contracts')
  saveView(s,key,'到期',{status:'即将到期',keyword:'101',businessRows:[1],authorization:'never-store'})
  assert.deepEqual(readSavedViews(s,key),[{name:'到期',filters:{status:'即将到期',keyword:'101'}}])
  for (const other of [viewStorageKey({id:2},{type:'building',code:'A'},'contracts'),viewStorageKey({id:1},{type:'building',code:'B'},'contracts'),viewStorageKey({id:1},{type:'building',code:'A'},'receivables')]) assert.deepEqual(readSavedViews(s,other),[])
})
test('corrupt storage and quota failures are bounded and observable', () => {
  assert.deepEqual(readSavedViews({getItem:()=>'{bad'},'key'),[])
  assert.deepEqual(readSavedViews({getItem:()=>'{"name":"bad"}'},'key'),[])
  const s=storage();for(let i=0;i<8;i++)saveView(s,'key',String(i),{})
  assert.throws(()=>saveView(s,'key','ninth',{}),/8/)
  assert.equal(saveView(s,'key','0',{status:'new'}).length,8)
  assert.throws(()=>saveView({getItem:()=>null,setItem:()=>{throw new Error('quota')}},'key','new',{}),/quota/)
})
test('detail links retain scope, month and filters without arbitrary fields', () => {
 const link=buildViewLink('http://localhost','receivables',{type:'building',code:'B'}, {month:'2026-09',status:'overdue',unknown:'ignored'}, {type:'contract',id:7})
 const url=new URL(link); const context=readViewLink(Object.fromEntries(url.searchParams))
 assert.equal(url.pathname,'/receivables');assert.equal(url.searchParams.get('building'),'B')
 assert.deepEqual(context,{filters:{month:'2026-09',status:'overdue'},detail:{type:'contract',id:7}})
 assert.equal(readViewLink({detail:'contract',id:'-1',view:'{bad'}).detail,null)
 assert.deepEqual(cleanViewFilters({keyword:'a'.repeat(161),status:[],ledger:'dunning'}),{ledger:'dunning'})
 assert.equal(buildViewLink('http://localhost','unknown',{type:'building',code:'B'},{}),'')
})

test('navigation clears stale view and detail references across pages and buildings', () => {
 const q={building:'A',view:'{}',detail:'contract',id:'7',month:'2026-09'}
 assert.deepEqual(navigationViewQuery(q,'/contracts','/receivables',{type:'building',code:'A'}),{building:'A',month:'2026-09'})
 assert.deepEqual(navigationViewQuery(q,'/contracts','/contracts',{type:'building',code:'B'}),{building:'A',month:'2026-09'})
 assert.deepEqual(navigationViewQuery(q,'/contracts','/contracts',{type:'building',code:'A'}),q)
 assert.equal(q.id,'7')
})
