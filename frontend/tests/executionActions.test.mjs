import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import * as vue from 'vue'
const {descriptor}=parse(readFileSync(new URL('../src/components/ExecutionActions.vue',import.meta.url),'utf8'))
const code=compileScript(descriptor,{id:'ExecutionActions'}).content.replace(/import\s+([\s\S]*?)\s+from\s+(['"])(.*?)\2;?/g,(_,binding,q,path)=>`const ${binding.trim().replace(/\s+as\s+/g,': ')}=imports[${JSON.stringify(path)}];`).replace('export default','return')
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const flush=async()=>{await vue.nextTick();await Promise.resolve();await Promise.resolve()}
function harness(t){
 const calls=[],hooks=[];let scopeChange
 const old=globalThis.window;globalThis.window=new EventTarget()
 const props=vue.reactive({contractId:1,contractEnd:'2026-12-01',buildingCode:'DEMO_A'})
 const imports={vue:{...vue,onActivated:()=>{},onBeforeUnmount:fn=>hooks.push(fn)},'./BaseModal.vue':{},'../lib/api':{api:(path,options)=>{const pending=deferred();calls.push({path,options,...pending});return pending.promise}},'../lib/propertyScope':{onPropertyScopeContextChange:fn=>{scopeChange=fn;return ()=>{}}}}
 const component=new Function('imports',code)(imports),effect=vue.effectScope()
 const state=effect.run(()=>component.setup(props,{expose:()=>{},emit:()=>{}}))
 t.after(()=>{hooks.forEach(fn=>fn());effect.stop();globalThis.window=old})
 return {state,calls,scopeChange}
}
const listing={items:[],total:0,can_write:true,current_user:'admin'}
test('late reads cannot repopulate a revoked or switched context',async t=>{
 const {state,calls,scopeChange}=harness(t)
 scopeChange();assert.equal(calls.length,2)
 calls[1].reject(new Error('无权访问'));await flush()
 calls[0].resolve({...listing,items:[{id:99}]});await flush()
 assert.deepEqual(state.rows.value,[]);assert.equal(state.canWrite.value,false)
})
test('save locks duplicate submits and preserves input on revision conflict',async t=>{
 const {state,calls}=harness(t);calls[0].resolve(listing);await flush()
 state.edit({id:1,revision:2,owner:'admin',due_on:'2026-12-01',status:'open',next_action:'原内容'})
 calls[1].resolve({contracts:[],follow_ups:[],expenses:[]});await flush()
 state.draft.value.next_action='新的处理内容'
 const first=state.save();await state.save();assert.equal(calls.length,3)
 calls[2].reject(new Error('事项已被更新'));await first
 assert.equal(state.opened.value,true);assert.equal(state.draft.value.next_action,'新的处理内容')
 assert.match(state.error.value,/事项已被更新/)
 state.close();assert.equal(state.discard.value,true)
})
test('scope invalidation clears visible drafts during an in-flight save',async t=>{
 const {state,calls,scopeChange}=harness(t);calls[0].resolve(listing);await flush();state.edit()
 calls[1].resolve({contracts:[],follow_ups:[],expenses:[]});await flush();state.draft.value.next_action='合成操作'
 const saving=state.save();scopeChange();calls[3].reject(new Error('无权访问'));await flush()
 calls[2].resolve({id:1});await saving
 assert.equal(state.opened.value,false);assert.deepEqual(state.draft.value,{})
 assert.equal(state.canWrite.value,false);assert.equal(state.busy.value,false)
})
