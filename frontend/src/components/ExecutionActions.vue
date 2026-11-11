<script setup>
import { ref, computed, watch, onBeforeUnmount, onActivated } from 'vue'
import BaseModal from './BaseModal.vue'
import { api } from '../lib/api'
import { onPropertyScopeContextChange } from '../lib/propertyScope'
const props=defineProps({ contractId:Number, contractEnd:String, versionId:Number, measureKey:String, buildingCode:String, compact:Boolean })
const emit=defineEmits(['open-contract'])
const rows=ref([]), total=ref(0), page=ref(1), filter=ref('all'), loading=ref(false), busy=ref(false), error=ref(''), canWrite=ref(false), currentUser=ref('')
const selected=ref(null), opened=ref(false), discard=ref(false), draft=ref({}), benefit=ref({}), benefitOpen=ref(false)
const choices=ref({contracts:[],follow_ups:[],expenses:[]}), proofKeyword=ref('')
let generation=0, choiceSequence=0, baseline='', benefitBaseline='', discardTarget='action'
async function loadChoices(row=selected.value){const token=generation, sequence=++choiceSequence;choices.value={contracts:[],follow_ups:[],expenses:[]};try{const data=await call('/options',{}, {...(props.contractId||row?.kind==='expiry'?{contract_id:props.contractId||row.contract_id}:{}),...(props.versionId||row?.version_id?{version_id:props.versionId||row.version_id}:{}),keyword:proofKeyword.value});if(token===generation&&sequence===choiceSequence)choices.value=data}catch(e){if(token===generation)error.value=e.message}}
const visibleRows=computed(()=>props.measureKey ? rows.value.filter(row=>row.measure_key===props.measureKey) : rows.value)
const contextual=computed(()=>Boolean(props.contractId || (props.versionId && props.measureKey)))
const title=computed(()=>props.contractId ? '到期处理记录' : props.measureKey ? ({newLease:'新增租约执行',costSaving:'经营降本执行'}[props.measureKey]) : '处理与核验')
function url(path='',query={}) { const q=new URLSearchParams(query);if(props.buildingCode)q.set('building',props.buildingCode);return `/api/execution-actions${path}${q.size?'?'+q:''}` }
function call(path='',options={},query={}) { return api(url(path,query),{...options,...(props.buildingCode?{includeScope:false}:{})}) }
function changed(){ window.dispatchEvent(new Event('execution-action-changed')) }
async function load(clear=false) {
 const token=++generation
 if(clear){rows.value=[];canWrite.value=false;opened.value=false;benefitOpen.value=false;discard.value=false;selected.value=null;draft.value={};benefit.value={};choices.value={contracts:[],follow_ups:[],expenses:[]};error.value=''}
 loading.value=true
 try {
  const data=await call('',{}, {page:page.value,filter:filter.value,...(props.contractId?{contract_id:props.contractId}:{}),...(props.versionId?{version_id:props.versionId}:{})})
  if(token!==generation)return
  rows.value=data.items;total.value=data.total;canWrite.value=data.can_write;currentUser.value=data.current_user
 } catch(e){if(token===generation){error.value=e.message;rows.value=[];canWrite.value=false}}
 finally{if(token===generation)loading.value=false}
}
function edit(row=null){
 selected.value=row
 draft.value={expected_revision:row?.revision||0,owner:row?.owner||currentUser.value,due_on:row?.due_on||props.contractEnd||new Date().toLocaleDateString('en-CA'),next_follow_up_on:row?.next_follow_up_on||'',status:row?.status||'open',decision:row?.decision||'undecided',next_action:row?.next_action||'',result_note:row?.result_note||'',result_contract_id:row?.result_contract_id||'',follow_up_id:row?.follow_up_id||''}
 baseline=JSON.stringify(draft.value);error.value='';opened.value=true;proofKeyword.value='';loadChoices(row)
}
function close(){if(busy.value)return;if(JSON.stringify(draft.value)!==baseline){discardTarget='action';discard.value=true;return}opened.value=false}
async function save(){
 if(busy.value || !canWrite.value)return
 const token=generation;busy.value=true;error.value=''
 try{
  const payload={...draft.value,next_follow_up_on:draft.value.next_follow_up_on||null,result_contract_id:Number(draft.value.result_contract_id)||null,follow_up_id:Number(draft.value.follow_up_id)||null}
  if(!selected.value)Object.assign(payload, props.contractId?{kind:'expiry',contract_id:props.contractId}:{kind:'measure',version_id:props.versionId,measure_key:props.measureKey})
  await call(selected.value?`/${selected.value.id}`:'',{method:selected.value?'PUT':'POST',body:JSON.stringify(payload)})
  if(token!==generation)return
  opened.value=false;changed()
 }catch(e){if(token===generation)error.value=e.message}
 finally{busy.value=false}
}
async function verify(row){
 if(busy.value)return
 const token=generation;busy.value=true;error.value=''
 try{await call(`/${row.id}/verify`,{method:'POST',body:JSON.stringify({expected_revision:row.revision})});if(token===generation)changed()}
 catch(e){if(token===generation)error.value=e.message}finally{busy.value=false}
}
function openBenefit(row){selected.value=row;benefit.value={amount_yuan:'',evidence:'',contract_id:'',expense_id:''};error.value='';benefitBaseline=JSON.stringify(benefit.value);benefitOpen.value=true;proofKeyword.value='';loadChoices(row)}
function closeBenefit(){if(busy.value)return;if(JSON.stringify(benefit.value)!==benefitBaseline){discardTarget='benefit';discard.value=true;return}benefitOpen.value=false}
function discardChanges(){discard.value=false;if(discardTarget==='benefit')benefitOpen.value=false;else opened.value=false}
async function saveBenefit(){
 if(busy.value)return
 const token=generation;busy.value=true;error.value=''
 try{await call(`/${selected.value.id}/benefit`,{method:'POST',body:JSON.stringify({...benefit.value,amount_yuan:benefit.value.amount_yuan===''?null:Number(benefit.value.amount_yuan),contract_id:Number(benefit.value.contract_id)||null,expense_id:Number(benefit.value.expense_id)||null,expected_revision:selected.value.revision})});if(token===generation){benefitOpen.value=false;changed()}}
 catch(e){if(token===generation)error.value=e.message}finally{busy.value=false}
}
const statusName=s=>({open:'处理中',waiting:'等待对方',done:'任务完成'}[s]||s)
const refresh=()=>load()
const unlisten=onPropertyScopeContextChange(()=>load(true))
watch(()=>[props.contractId,props.versionId,props.measureKey,props.buildingCode],()=>{page.value=1;load(true)},{immediate:true})
watch(page,()=>load())
watch(filter,()=>{if(page.value!==1)page.value=1;else load()})
onActivated(()=>load())
window.addEventListener('execution-action-changed',refresh)
onBeforeUnmount(()=>{generation++;unlisten();window.removeEventListener('execution-action-changed',refresh)})
</script>
<template>
 <section class="execution-actions" :aria-label="$t(title)">
  <header class="execution-heading"><h3>{{ $t(title) }}</h3><button class="btn" :disabled="loading||busy" @click="load()">{{ $t("刷新记录") }}</button><button v-if="contextual && !visibleRows.length && canWrite" class="btn btn-primary" :disabled="loading||busy" @click="edit()">{{ $t("建立处理记录") }}</button></header>
  <p v-if="!contextual" class="execution-hint">{{ $t("合同到期事项与已保存方案的措施共用这些记录；催收和客户跟进仍在原台账处理。") }}</p>
  <label v-if="!contextual">{{ $t("查看") }}<select v-model="filter"><option value="all">{{ $t("全部记录") }}</option><option value="mine">{{ $t("我的待办") }}</option><option value="overdue">{{ $t("已逾期") }}</option><option value="waiting">{{ $t("等待对方") }}</option><option value="follow-up">{{ $t("今日需跟进") }}</option><option value="done">{{ $t("任务已完成") }}</option></select></label>
  <p v-if="error && !opened && !benefitOpen" role="alert">{{ $t(error) }}</p><p v-if="loading && !rows.length" role="status">{{ $t("正在读取处理记录…") }}</p>
  <p v-else-if="!visibleRows.length">{{ $t("尚无处理记录。") }}{{ $t(contextual ? '建立后可安排负责人、期限及结果核验。' : '可从合同详情或已保存的经营措施建立。') }}</p>
  <article v-for="row in visibleRows" :key="row.id" class="execution-row">
   <div><strong>{{ $t(row.title) }}</strong><p>{{ $t(statusName(row.status)) }} · {{ $t(row.owner) }}{{ $t(" · 截止 ") }}{{ $t(row.due_on) }} <span v-if="row.overdue">{{ $t(" · 已逾期") }}</span></p><p>{{ $t(row.next_action) }}</p><p v-if="row.next_follow_up_on">{{ $t("下次跟进 ") }}{{ $t(row.next_follow_up_on) }}{{ $t(row.follow_up_due?' · 已到跟进日':'') }}</p>
   <p v-if="row.kind==='expiry'">{{ $t("决定：") }}{{ $t({undecided:'待确定',renew:'续租',vacate:'退租'}[row.decision]) }}{{ $t(" · 合同动作：") }}{{ $t({pending:'待核验',verified:'已核验',stale:'依据变化，请重新核验'}[row.verification_state]) }}</p>
   <p v-else>{{ $t("固定来源：") }}{{ $t(row.source.year) }}{{ $t(" 年 · 第 ") }}{{ $t(row.source.revision) }}{{ $t(" 版 · 收益：") }}{{ $t(row.benefit_state==='verified'?'已人工核验 '+Number(row.benefit.amount_yuan).toLocaleString()+' 元':row.benefit_state==='stale'?'依据变化，请重新核验':'未核验') }}{{ $t("。不自动计入收入或预测。") }}</p>
   <p v-if="row.result_note">{{ $t("处理结果：") }}{{ $t(row.result_note) }}</p><p v-if="row.follow_up_id">{{ $t("关联客户跟进 #") }}{{ $t(row.follow_up_id) }}</p>
   <details v-if="row.verification.by||row.benefit.by"><summary>{{ $t("核验依据") }}</summary><p v-if="row.verification.by">{{ $t(row.verification.by) }} · {{ $t(row.verification.at) }}{{ $t(" · 已核对实际合同处置关系") }}</p><p v-if="row.benefit.by">{{ $t(row.benefit.by) }} · {{ $t(row.benefit.at) }} · {{ $t(row.benefit.evidence) }}</p><p v-if="row.benefit.contract_id">{{ $t("实际合同 #") }}{{ $t(row.benefit.contract_id) }}</p><p v-if="row.benefit.expense_id">{{ $t("费用记录 #") }}{{ $t(row.benefit.expense_id) }}</p></details>
   </div>
   <div class="execution-buttons"><button v-if="row.contract_id && !contractId" class="btn" @click="emit('open-contract',row.contract_id)">{{ $t("打开合同") }}</button><button v-if="canWrite" class="btn" :disabled="busy" @click="edit(row)">{{ $t("更新处理") }}</button><button v-if="canWrite && row.kind==='expiry' && row.status==='done'" class="btn" :disabled="busy" @click="verify(row)">{{ $t("核验实际合同动作") }}</button><button v-if="canWrite && row.kind==='measure' && row.status==='done'" class="btn" :disabled="busy" @click="openBenefit(row)">{{ $t("登记收益核验") }}</button></div>
  </article>
  <div v-if="!contextual && total>25" class="execution-buttons"><button class="btn" :disabled="page===1||loading" @click="page--">{{ $t("上一页") }}</button><span>{{ $t(page) }} / {{ $t(Math.ceil(total/25)) }}{{ $t(" · 共 ") }}{{ $t(total) }}{{ $t(" 条") }}</span><button class="btn" :disabled="page*25>=total||loading" @click="page++">{{ $t("下一页") }}</button></div>
  <BaseModal :open="opened" :title="$t(&quot;安排与更新处理&quot;)" size="wide" @close="close">
   <form class="execution-form" @submit.prevent="save">
    <p>{{ $t("更新后须重新核验结果；同一事项始终保留同一条记录。填写登录账号可进入对应账号的“我的待办”。") }}</p><p v-if="error" role="alert">{{ $t(error) }}</p>
    <label>{{ $t("负责人") }}<input v-model="draft.owner" required maxlength="100" :disabled="!canWrite||busy" /></label><button type="button" class="btn" :disabled="busy" @click="draft.owner=currentUser">{{ $t("由我认领") }}</button>
    <label>{{ $t("截止日期") }}<input v-model="draft.due_on" type="date" required :disabled="busy" /></label><label>{{ $t("下次跟进日期") }}<input v-model="draft.next_follow_up_on" type="date" :disabled="busy" /></label>
    <label>{{ $t("处理状态") }}<select v-model="draft.status" :disabled="busy"><option value="open">{{ $t("处理中") }}</option><option value="waiting">{{ $t("等待对方") }}</option><option value="done">{{ $t("任务完成，结果待核验") }}</option></select></label>
    <label v-if="contractId||selected?.kind==='expiry'">{{ $t("到期决定") }}<select v-model="draft.decision" :disabled="busy"><option value="undecided">{{ $t("待确定") }}</option><option value="renew">{{ $t("续租") }}</option><option value="vacate">{{ $t("退租") }}</option></select></label>
    <label>{{ $t("下一步") }}<textarea v-model="draft.next_action" required maxlength="2000" :disabled="busy" /></label><label>{{ $t("处理结果") }}<textarea v-model="draft.result_note" :required="draft.status==='done'" maxlength="2000" :disabled="busy" /></label>
    <label v-if="draft.decision==='renew'">{{ $t("实际续签合同") }}<select v-model="draft.result_contract_id" :disabled="busy"><option value="">{{ $t("请选择已完成续签的合同") }}</option><option v-for="item in choices.contracts" :key="item.id" :value="item.id">{{ $t(item.label) }}</option></select></label><label>{{ $t("关联现有客户跟进（可选）") }}<select v-model="draft.follow_up_id" :disabled="busy"><option value="">{{ $t("不关联") }}</option><option v-for="item in choices.follow_ups" :key="item.id" :value="item.id">{{ $t(item.label) }}</option></select></label>
    <p v-if="contractId||selected?.kind==='expiry'">{{ $t("续租或退租请在合同管理使用原有操作办理；填写决定不会自动变更合同。续签关系可在合同详情查看。") }}</p>
    <div class="execution-buttons"><button type="button" class="btn" :disabled="busy" @click="close">{{ $t("取消") }}</button><button class="btn btn-primary" :disabled="busy||!canWrite">{{ $t(busy?'保存中…':'保存处理记录') }}</button></div>
   </form>
  </BaseModal>
  <BaseModal :open="discard" :title="$t(&quot;保留未保存的处理内容&quot;)" size="small" @close="discard=false"><p>{{ $t("当前处理内容尚未保存。") }}</p><div class="execution-buttons"><button class="btn" @click="discard=false">{{ $t("继续编辑") }}</button><button class="btn" @click="discardChanges">{{ $t("放弃修改") }}</button></div></BaseModal>
  <BaseModal :open="benefitOpen" :title="$t(&quot;单独核验已实现收益&quot;)" size="wide" @close="closeBenefit">
   <form class="execution-form" @submit.prevent="saveBenefit"><p>{{ $t("这是有依据的人工核验记录，不修改账目，不自动确认收入，也不改写历史预测。请在依据中注明期间、比较基准和核对方式。") }}</p><p v-if="error" role="alert">{{ $t(error) }}</p><label>{{ $t("核验金额（元）") }}<input v-model="benefit.amount_yuan" type="number" min="0" step="0.01" required :disabled="busy" /></label><template v-if="selected?.measure_key==='newLease'"><label>{{ $t("查找实际合同") }}<input v-model="proofKeyword" :placeholder="$t(&quot;合同号、客户或房号&quot;)" :disabled="busy" /></label><button type="button" class="btn" :disabled="busy" @click="loadChoices()">{{ $t("查询合同") }}</button><label>{{ $t("实际合同") }}<select v-model="benefit.contract_id" required :disabled="busy"><option value="">{{ $t("请选择，最多显示30条") }}</option><option v-for="item in choices.contracts" :key="item.id" :value="item.id">{{ $t(item.label) }}</option></select></label></template><label v-else>{{ $t("对应年度的费用记录") }}<select v-model="benefit.expense_id" required :disabled="busy"><option value="">{{ $t("请选择费用记录") }}</option><option v-for="item in choices.expenses" :key="item.id" :value="item.id">{{ $t(item.label) }}</option></select></label><label>{{ $t("核验依据") }}<textarea v-model="benefit.evidence" minlength="3" maxlength="2000" required :disabled="busy" /></label><button class="btn btn-primary" :disabled="busy">{{ $t(busy?'保存中…':'保存人工核验') }}</button></form>
  </BaseModal>
 </section>
</template>
<style scoped>
.execution-actions{padding:16px;border:1px solid var(--border-color,#cbd5e1);border-radius:12px;margin-block:16px;background:var(--surface-color,transparent);color:inherit;min-width:0}.execution-heading,.execution-buttons{display:flex;align-items:center;flex-wrap:wrap;gap:8px}.execution-heading h3{margin:0 auto 0 0}.execution-hint,.execution-row p{font-size:14px;line-height:1.6;overflow-wrap:anywhere}.execution-row{display:flex;justify-content:space-between;gap:16px;border-top:1px solid #94a3b84d;padding:12px 0}.execution-row>div{min-width:0}.execution-form{display:grid;gap:12px}.execution-form label{display:grid;gap:5px}.execution-form input,.execution-form textarea,.execution-form select,.execution-actions select{width:100%;min-height:44px;box-sizing:border-box;padding:8px;border:1px solid #94a3b8;border-radius:7px;background:var(--input-bg,#fff);color:var(--text-primary,#172033)}.execution-form textarea{min-height:80px}.execution-actions button{min-height:44px}@media(max-width:640px){.execution-row{display:block}.execution-row .execution-buttons{margin-top:10px}}
</style>
