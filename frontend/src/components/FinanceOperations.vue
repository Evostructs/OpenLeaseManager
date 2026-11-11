<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import BaseModal from './BaseModal.vue'
import FinanceMappingPanel from './FinanceMappingPanel.vue'
import { api } from '../lib/api'
import { createPaymentOperationId } from '../lib/receivableSubmission'
import { onPropertyScopeContextChange } from '../lib/propertyScope'
import { financeKinds, financeStates, parseBankCsv } from '../lib/financeOperations'
const emit=defineEmits(['open-contract'])
const opened=ref(false), tab=ref('overview'), month=ref(new Date().toLocaleDateString('en-CA').slice(0,7)), page=ref(1)
const data=ref({cash:[],banks:[]}), period=ref(null), expenses=ref([]), outcomes=ref([]), outcomeTotal=ref(0), loading=ref(false), busy=ref(false), error=ref(''), notice=ref('')
const handoff=ref(null), handoffMode=ref('draft')
const mappingDirty=ref(false), mappingBusy=ref(false), requireMapping=ref(false)
const form=ref(''), draft=ref({}), selected=ref(null), choices=ref({contracts:[],deposits:[],payments:[]}), keyword=ref(''), confirmations=ref({}), reason=ref(''), csvRows=ref([]), discard=ref(false)
let generation=0, choiceSequence=0, baseline='', closeAll=false, periodBaseline='{}', periodMonth=''
const tabs={overview:'月度核对',cash:'收付款分配',bank:'银行对账',expenses:'费用与付款',close:'提交与关账',handoff:'财务交接',outcomes:'措施复盘'}
const canWrite=computed(()=>data.value.can_write===true), money=v=>Number(v||0).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})
const call=(path,options={})=>api('/api/finance-operations'+path,options)
const formDirty=computed(()=>Boolean(form.value)&&JSON.stringify(draft.value)!==baseline)
const periodDirty=computed(()=>Boolean(period.value)&&(JSON.stringify(confirmations.value)!==periodBaseline||Boolean(reason.value.trim())))
const dirty=computed(()=>formDirty.value||periodDirty.value||mappingDirty.value)
const count=computed(()=>tab.value==='outcomes'?outcomeTotal.value:tab.value==='bank'?data.value.bank_total:data.value.cash_total)
async function load(){
 if(mappingDirty.value)return
 const token=++generation;loading.value=true;error.value='';handoff.value=null;requireMapping.value=false
 try{
  const result=await call(`/ledger?month=${month.value}&page=${page.value}`)
  if(token!==generation)return;data.value=result
  if(['overview','close'].includes(tab.value)){const value=await call('/period?month='+month.value);if(token!==generation)return;if(periodMonth!==month.value||!periodDirty.value){period.value=value;confirmations.value={...value.confirmations};periodBaseline=JSON.stringify(confirmations.value);periodMonth=month.value}else if(period.value.revision!==value.revision){error.value='月度状态已被更新，填写内容仍保留；请关闭并重新打开后核对。'}}
  if(['expenses','cash'].includes(tab.value)){const value=await api('/api/financial-planning/expenses?year='+month.value.slice(0,4));if(token!==generation)return;expenses.value=value.items}
  if(tab.value==='outcomes'){const value=await call(`/outcomes?month=${month.value}&page=${page.value}`);if(token!==generation)return;outcomes.value=value.items;outcomeTotal.value=value.total}
  if(tab.value==='handoff'){const value=await call(`/handoff/preview?month=${month.value}&mode=${handoffMode.value}`);if(token!==generation)return;handoff.value=value}
 }catch(e){if(token===generation)error.value=e.message}finally{if(token===generation)loading.value=false}
}
function open(){opened.value=true;load()}
function close(){if(busy.value||mappingBusy.value)return;if(dirty.value){closeAll=true;discard.value=true;return}opened.value=false;form.value='';generation++}
function closeForm(){if(busy.value)return;if(formDirty.value){closeAll=false;discard.value=true;return}form.value=''}
function discardDraft(){form.value='';discard.value=false;if(closeAll){opened.value=false;reason.value='';confirmations.value={...period.value?.confirmations};periodBaseline=JSON.stringify(confirmations.value);generation++}}
function edit(type,row=null){
 selected.value=row;form.value=type;csvRows.value=[];error.value='';notice.value=''
 const common={entry_key:createPaymentOperationId()}
 draft.value=type==='cash'?{...common,direction:'in',occurred_on:month.value+'-01',amount:'',counterparty:'',reference:'',note:''}:
 type==='allocate'?{...common,expected_revision:row.revision,kind:row.direction==='out'?'expense':'rent',amount:row.remaining,period_month:month.value,contract_id:'',expense_id:'',deposit_id:'',existing_payment_id:'',note:''}:
 type==='expense'?{...common,name:'',category:'opex',amount_yuan:'',occurrence_month:month.value,payment_month:month.value,owner:'',supplier:'',cost_category:'',cost_center:'',service_from:month.value,service_to:month.value,evidence:''}:
 type==='detail'?{expected_revision:row.revision,supplier:row.detail?.supplier||'',cost_category:row.detail?.cost_category||'',cost_center:row.detail?.cost_center||'',service_from:row.detail?.service_from||row.occurrence_month,service_to:row.detail?.service_to||row.occurrence_month,evidence:row.detail?.evidence||''}:
 type==='match'?{...common,expected_revision:row.revision,cash_id:'',cash_revision:1,amount:row.remaining,note:''}:
 type==='bank'?{}:{expected_revision:row.revision,reason:''}
 baseline=JSON.stringify(draft.value);if(type==='allocate')loadChoices()
}
async function loadChoices(){const token=generation, sequence=++choiceSequence;try{const v=await call('/options?keyword='+encodeURIComponent(keyword.value)+(draft.value.contract_id?'&contract_id='+draft.value.contract_id:''));if(token===generation&&sequence===choiceSequence)choices.value=v}catch(e){error.value=e.message}}
async function lookupCash(){try{const c=await call('/cash/'+Number(draft.value.cash_id));if(c.voided||c.direction!==selected.value.direction)throw new Error('流水方向不一致或已作废');selected.value.suggestions=[c,...selected.value.suggestions.filter(x=>x.id!==c.id)];chooseCash();error.value=''}catch(e){error.value=e.message}}
function chooseExpense(){const e=expenses.value.find(e=>e.id===Number(draft.value.expense_id));if(e){draft.value.period_month=e.occurrence_month;draft.value.amount=Math.min(selected.value.remaining,e.unpaid_yuan)}}
function chooseCash(){const c=selected.value.suggestions.find(c=>c.id===Number(draft.value.cash_id));if(c){draft.value.cash_revision=c.revision;draft.value.amount=Math.min(c.bank_remaining,selected.value.remaining)}}
async function readCsv(event){try{csvRows.value=parseBankCsv(await event.target.files[0].text());draft.value={rows:csvRows.value};error.value=''}catch(e){error.value=e.message;csvRows.value=[]}}
function template(){const blob=new Blob(['\uFEFF流水编号,日期,方向,金额,往来方,摘要,账户简称\n示例编号请替换,2026-09-01,收入,100.00,示例往来方,示例摘要,结算账户\n'],{type:'text/csv;charset=utf-8'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='银行流水模板.csv';a.click();URL.revokeObjectURL(url)}
async function refreshDraft(){
 if(busy.value)return;busy.value=true;const token=generation
 try{
  const kind=form.value;const row=selected.value
  const path=kind==='detail'?`/expenses/${row.id}`:kind==='match'?`/bank/${row.id}`:kind==='unmatch'?`/bank/${row.bank_id}`:`/cash/${kind==='reverse'?row.cash_id:row.id}`
  const latest=await call(path);if(token!==generation)return
  draft.value.expected_revision=latest.revision
  if(kind==='allocate'||kind==='void'||kind==='match')Object.assign(selected.value,latest)
  if(kind==='match'&&draft.value.cash_id){const cash=await call('/cash/'+Number(draft.value.cash_id));if(token!==generation)return;draft.value.cash_revision=cash.revision}
  error.value='已读取最新版本，填写内容仍保留。请核对最新余额后保存。'
 }catch(e){if(token===generation)error.value=e.message}finally{busy.value=false}
}
async function save(){
 if(busy.value||!canWrite.value)return;busy.value=true;error.value='';const token=generation
 try{
  let path='',method='POST',body={...draft.value}
  if(form.value==='cash')path='/cash'
  if(form.value==='allocate'){path=`/cash/${selected.value.id}/allocate`;for(const k of ['contract_id','expense_id','deposit_id','existing_payment_id'])body[k]=Number(body[k])||null;if(body.kind==='expense'){body.contract_id=null;body.deposit_id=null}else body.expense_id=null;if(body.kind!=='deposit')body.deposit_id=null;if(body.kind!=='rent')body.existing_payment_id=null}
  if(form.value==='bank'){path='/bank/import';body={rows:csvRows.value}}
  if(form.value==='match'){path=`/bank/${selected.value.id}/match`;body.cash_id=Number(body.cash_id)}
  if(form.value==='detail'){path=`/expenses/${selected.value.id}/detail`;method='PUT'}
  if(form.value==='expense'){
   path='/expenses';body={entry_key:body.entry_key,expense:{name:body.name,category:body.category,amount_yuan:Number(body.amount_yuan),occurrence_month:body.occurrence_month,payment_month:body.payment_month,owner:body.owner},detail:{expected_revision:1,...Object.fromEntries(['supplier','cost_category','cost_center','service_from','service_to','evidence'].map(k=>[k,body[k]]))}}
  }
  if(['reverse','void','unmatch'].includes(form.value))path=form.value==='reverse'?`/allocations/${selected.value.id}/reverse`:form.value==='void'?`/cash/${selected.value.id}/void`:`/matches/${selected.value.id}/reverse`
  await call(path,{method,body:JSON.stringify(body)})
  if(token!==generation)return;form.value='';notice.value='已保存，相关台账和预测将按新记录计算。';window.dispatchEvent(new Event('financial-planning-changed'));await load()
 }catch(e){if(token===generation)error.value=e.message}finally{busy.value=false}
}
async function periodAction(action){if(busy.value)return;busy.value=true;error.value='';try{await call('/period?month='+month.value,{method:'POST',body:JSON.stringify({action,expected_revision:period.value.revision,reason:reason.value,confirmations:confirmations.value})});reason.value='';periodBaseline=JSON.stringify(confirmations.value);notice.value='月度状态已更新';await load()}catch(e){error.value=e.message}finally{busy.value=false}}
async function exportHandoff(){
 if(busy.value||loading.value||mappingDirty.value||mappingBusy.value||!canWrite.value||!handoff.value)return
 const token=generation, current={...handoff.value};busy.value=true;error.value='';notice.value=''
 try{
  const params=new URLSearchParams({month:current.month,mode:current.mode,expected_revision:String(current.revision),expected_digest:current.handoff_digest,require_mapping:String(requireMapping.value)})
  const blob=await call('/handoff/export?'+params,{responseType:'blob',cache:'no-store'})
  if(token!==generation)return
  const url=URL.createObjectURL(blob), link=document.createElement('a')
  link.href=url;link.download=`财务交接_${current.building.code}_${current.month}_${current.mode==='closed'?'已关账':'核对草稿'}_r${current.revision}.zip`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
  notice.value=current.mode==='closed'?'已导出关账交接包，包含来源、逐笔明细与复核记录。':'已导出核对草稿；完成独立复核关账后可导出正式交接包。'
 }catch(e){if(token===generation)error.value=e.message}finally{busy.value=false}
}
const unlisten=onPropertyScopeContextChange(()=>{generation++;opened.value=false;form.value='';draft.value={};selected.value=null;choices.value={contracts:[],deposits:[],payments:[]};csvRows.value=[];keyword.value='';discard.value=false;outcomeTotal.value=0;data.value={cash:[],banks:[]};period.value=null;reason.value='';confirmations.value={};periodBaseline='{}';periodMonth='';expenses.value=[];outcomes.value=[];error.value=''})
const unlistenHandoff=onPropertyScopeContextChange(()=>{handoff.value=null;handoffMode.value='draft';notice.value=''})
function beforeUnload(e){if(opened.value&&dirty.value){e.preventDefault();e.returnValue=''}}
window.addEventListener('beforeunload',beforeUnload)
onBeforeUnmount(()=>{generation++;unlisten();unlistenHandoff();window.removeEventListener('beforeunload',beforeUnload)})
watch([tab,month,page,handoffMode],()=>{if(opened.value)load()})
</script>
<template>
 <button class="btn" type="button" @click="open">{{ $t("经营财务核对") }}</button>
 <BaseModal :open="opened" :title="$t(&quot;经营财务核对&quot;)" size="wide" @close="close">
  <div class="finance-ops">
   <div class="fo-toolbar"><label>{{ $t("核对月份 ") }}<input v-model="month" type="month" min="2000-01" max="2100-12" :disabled="busy||loading||periodDirty||mappingDirty||mappingBusy"></label><button type="button" :disabled="busy||loading||mappingDirty||mappingBusy" @click="load">{{ $t("刷新") }}</button><span v-if="loading" role="status">{{ $t("正在读取台账…") }}</span></div>
   <nav class="fo-tabs" :aria-label="$t(&quot;经营财务功能&quot;)"><button v-for="(label,key) in tabs" :key="key" type="button" :aria-pressed="tab===key" :disabled="busy||mappingBusy||mappingDirty" @click="tab=key;page=1">{{ $t(label) }}</button></nav>
   <p v-if="error" class="error-text" role="alert">{{ $t(error) }}</p><p v-if="notice" role="status">{{ $t(notice) }}</p>
   <template v-if="tab==='handoff'">
    <div class="fo-toolbar"><label>{{ $t("交接内容") }}<select v-model="handoffMode" :disabled="busy||loading||mappingDirty||mappingBusy"><option value="draft">{{ $t("本月核对草稿") }}</option><option value="closed">{{ $t("已关账快照") }}</option></select></label><button type="button" :disabled="busy||loading||mappingDirty||mappingBusy||!canWrite||!handoff" @click="exportHandoff">{{ $t(busy?'正在准备交接包…':'导出财务交接包') }}</button></div>
    <template v-if="handoff">
     <article class="fo-handoff-hero"><span>{{ handoff.building.name }} · {{ $t(handoff.month) }}</span><h3>{{ $t(handoff.mode==='closed'?'把已核对的账，交清楚。':'先看清差异，再交接。') }}</h3><p>{{ $t(handoff.mode==='closed'?'逐笔明细和业务依据均取自保存的关账快照。':'草稿按当前台账生成，待处理差异完整保留。') }}</p><p>{{ $t("月度状态：") }}{{ $t({open:'待核对',submitted:'待独立复核',closed:'已关账'}[handoff.status]) }}{{ $t(" · 第 ") }}{{ $t(handoff.revision) }}{{ $t(" 次修订") }}</p></article>
     <div class="fo-metrics"><article><span>{{ $t("本月资金收入") }}</span><strong>{{ $t(money(handoff.cash_in_fen/100)) }}{{ $t(" 元") }}</strong></article><article><span>{{ $t("本月资金支出") }}</span><strong>{{ $t(money(handoff.cash_out_fen/100)) }}{{ $t(" 元") }}</strong></article><article><span>{{ $t("业务来源") }}</span><strong>{{ $t(handoff.counts.sources) }}{{ $t(" 条") }}</strong></article><article><span>{{ $t("待处理差异") }}</span><strong>{{ $t(handoff.issue_count) }}{{ $t(" 项") }}</strong></article></div>
     <div class="fo-card"><h3>{{ $t("一包交接，保留整条依据。") }}</h3><p>{{ $t("Excel 核对表 · 逐笔资金与分配 · 银行流水与匹配 · 来源快照 · 复核记录 · 文件校验清单") }}</p><p>{{ $t("资金 ") }}{{ $t(handoff.counts.cash) }}{{ $t(" 笔 · 分配 ") }}{{ $t(handoff.counts.allocations) }}{{ $t(" 笔 · 银行流水 ") }}{{ $t(handoff.counts.banks) }}{{ $t(" 笔 · 匹配 ") }}{{ $t(handoff.counts.matches) }}{{ $t(" 笔") }}</p><p v-if="handoff.mode==='closed'">{{ $t("经办：") }}{{ $t(handoff.submitted_by) }}{{ $t(" · 独立复核：") }}{{ $t(handoff.reviewed_by) }}</p></div>
     <FinanceMappingPanel v-if="opened" :profile="handoff.customer_mapping" :can-configure="canWrite&&data.can_review&&!busy" @dirty="mappingDirty=$event" @busy="mappingBusy=$event" @saved="load" />
     <label class="fo-mapped-check"><input v-model="requireMapping" type="checkbox" :disabled="busy||mappingDirty||mappingBusy||handoff.mode!=='closed'||!handoff.customer_mapping.ready">{{ $t("附客户映射的交接（须独立关账、映射完整并确认）") }}</label>
     <p v-if="mappingDirty" class="fo-handoff-note">{{ $t("财务映射尚未保存，请保存后再切换或导出。") }}</p>
     <p class="fo-handoff-note">{{ $t("合同管理口径，押金单列。交接包保留客户映射版本，财务人员据此核对入账。") }}</p>
    </template>
   </template>
   <template v-if="tab==='overview'&&period">
    <p>{{ $t("合同管理口径 · 金额单位万元。资料完整性见下方状态；押金单列。") }}</p>
    <div class="fo-metrics"><article v-for="(label,key) in {revenue:'管理收入',actual_cash_received:'租金与已录物业费实收',operating_cost:'经营费用',actual_opex_paid:'费用实付'}" :key="key"><span>{{ $t(label) }}</span><strong>{{ $t(money(period.facts.metrics[key])) }}</strong></article></div>
    <p>{{ $t("月度状态：") }}{{ $t({open:'待核对',submitted:'待独立复核',closed:'已关账'}[period.status]) }} <strong v-if="period.stale">{{ $t("来源已变化，需要重新核对") }}</strong></p>
    <div class="fo-metrics"><article v-for="(label,key) in {income:'收入资料',receipts:'收款资料',expenses:'费用资料',payments:'付款资料'}" :key="key">{{ $t(label) }}<strong>{{ $t(financeStates[period.confirmations[key]||'missing']) }}</strong></article></div>
    <h3>{{ $t("待处理差异") }}</h3><p v-if="!period.live_issues.length">{{ $t("未发现台账差异；仍须由经办人确认四类资料完整性。") }}</p>
    <ul><li v-for="(item,index) in period.live_issues" :key="index">{{ $t(item.label) }} · {{ $t(money(item.amount_yuan)) }}{{ $t(" 元 · ") }}{{ $t(item.owner) }} <button v-if="item.contract_id" @click="emit('open-contract',{id:item.contract_id})">{{ $t("查看合同") }}</button></li></ul>
    <details><summary>{{ $t("查看来源明细（") }}{{ $t(period.facts.sources.length) }}{{ $t(" 条）") }}</summary><article v-for="s in period.facts.sources" :key="s.key" class="fo-card"><strong>{{ $t(s.label) }}</strong><p>{{ $t("收入 ") }}{{ $t(money(s.values.revenue)) }}{{ $t(" · 费用 ") }}{{ $t(money(s.values.operating_cost)) }}{{ $t(" · 实收 ") }}{{ $t(money(s.values.actual_cash_received)) }}{{ $t(" 万元") }}</p><span v-if="s.evidence.owner">{{ $t("负责人：") }}{{ $t(s.evidence.owner) }}</span></article></details>
   </template>
   <template v-if="tab==='cash'">
    <p>{{ $t("先登记整笔收付款，再分配到一个或多个账期。关联已有租金收款时不重复入账。未分配的预收款会保留余额。") }}</p>
    <button v-if="canWrite" type="button" @click="edit('cash')">{{ $t("登记收付款") }}</button><p v-if="!data.cash.length&&!loading">{{ $t("本月尚无资金流水。") }}</p>
    <article v-for="row in data.cash" :key="row.id" class="fo-card"><h3>#{{ $t(row.id) }} {{ row.counterparty }} · {{ $t(row.direction==='in'?'收入':'支出') }} {{ $t(money(row.amount)) }}{{ $t(" 元 ") }}<span v-if="row.voided">{{ $t("（已作废）") }}</span></h3><p>{{ $t(row.occurred_on) }} · {{ $t(row.reference||'无摘要') }}{{ $t(" · 待分配 ") }}{{ $t(money(row.remaining)) }}{{ $t(" · 待银行核对 ") }}{{ $t(money(row.bank_remaining)) }}</p><p>{{ row.note }}</p><div class="fo-actions" v-if="canWrite&&!row.voided"><button :disabled="busy||row.remaining<=0" @click="edit('allocate',row)">{{ $t("分配资金") }}</button><button :disabled="busy" @click="edit('void',row)">{{ $t("作废流水") }}</button></div><ul><li v-for="a in row.allocations" :key="a.id">{{ $t(financeKinds[a.kind]) }} · {{ $t(a.period_month) }} · {{ $t(money(a.amount)) }}{{ $t(" 元 · ") }}{{ $t(a.voided?'已撤销':'已分配') }}<button v-if="canWrite&&!a.voided" :disabled="busy" @click="edit('reverse',{...a,revision:row.revision})">{{ $t("撤销分配") }}</button></li></ul></article>
   </template>
   <template v-if="tab==='bank'">
    <p>{{ $t("导入银行明细后确认匹配。匹配只做核对，不新增收付款。相同流水编号可重复导入，内容不同会提示冲突。") }}</p><div class="fo-actions"><button @click="template">{{ $t("下载 CSV 模板") }}</button><button v-if="canWrite" @click="edit('bank')">{{ $t("导入银行流水") }}</button></div>
    <p v-if="!data.banks.length&&!loading">{{ $t("本月尚无银行明细。") }}</p><article v-for="row in data.banks" :key="row.id" class="fo-card"><h3>{{ row.counterparty }} · {{ $t(row.direction==='in'?'收入':'支出') }} {{ $t(money(row.amount)) }}{{ $t(" 元") }}</h3><p>{{ $t(row.occurred_on) }} · {{ $t(row.reference) }} · {{ $t(row.account_label) }}{{ $t(" · 待匹配 ") }}{{ $t(money(row.remaining)) }}{{ $t(" 元") }}</p><button v-if="canWrite&&row.remaining>0" @click="edit('match',row)">{{ $t("确认匹配（") }}{{ $t(row.suggestions.length) }}{{ $t(" 个候选）") }}</button><ul><li v-for="m in row.matches" :key="m.id">{{ $t("资金 #") }}{{ $t(m.cash_id) }} · {{ $t(money(m.amount)) }}{{ $t(" 元 · ") }}{{ $t(m.voided?'已撤销':'已匹配') }} <button v-if="canWrite&&!m.voided" @click="edit('unmatch',{...m,revision:row.revision})">{{ $t("撤销匹配") }}</button></li></ul></article>
   </template>
   <template v-if="tab==='expenses'">
    <p>{{ $t("费用按服务期间分摊，逐笔付款通过「收付款分配」关联。下列为本年度相关费用。") }}</p><button v-if="canWrite" @click="edit('expense')">{{ $t("新增费用") }}</button>
    <article v-for="row in expenses" :key="row.id" class="fo-card"><h3>{{ row.name }} · {{ $t(money(row.amount_yuan)) }}{{ $t(" 元") }}</h3><p>{{ $t(row.category==='opex'?'经营费用':'资本支出') }} · {{ row.detail?.supplier||'供应商待补' }} · {{ $t(row.detail?.cost_category||'分类待补') }} · {{ $t(row.detail?.cost_center||'成本中心未设') }}</p><p>{{ $t("服务期间 ") }}{{ $t(row.detail?.service_from||row.occurrence_month) }}{{ $t(" 至 ") }}{{ $t(row.detail?.service_to||row.occurrence_month) }}{{ $t(" · 已付 ") }}{{ $t(money(row.paid_yuan)) }}{{ $t(" · 未付 ") }}{{ $t(money(row.unpaid_yuan)) }}{{ $t(" 元 · 负责人 ") }}{{ $t(row.owner||'未分配') }}</p><p>{{ $t("凭据：") }}{{ $t(row.detail?.evidence||'待补') }}</p><button v-if="canWrite" @click="edit('detail',row)">{{ $t("编辑费用资料") }}</button></article>
   </template>
   <template v-if="tab==='close'&&period">
    <p>{{ $t("经办确认 → 提交 → 另一位项目管理员复核关账。关账锁定相关历史业务；更正须先重开并留下原因。") }}</p><p>{{ $t("状态：") }}{{ $t({open:'待核对',submitted:'待复核',closed:'已关账'}[period.status]) }}{{ $t(" · 提交人 ") }}{{ $t(period.submitted_by||'尚未提交') }}{{ $t(" · 复核人 ") }}{{ $t(period.reviewed_by||'尚未复核') }}</p>
    <div class="fo-grid"><label v-for="(label,key) in {income:'收入资料',receipts:'收款资料',expenses:'费用资料',payments:'付款资料'}" :key="key">{{ $t(label) }}<select v-model="confirmations[key]" :disabled="!canWrite||period.status!=='open'||busy"><option :value="undefined">{{ $t("未提供") }}</option><option v-for="(v,k) in financeStates" :value="k" :key="k">{{ $t(v) }}</option></select></label><label class="fo-wide">{{ $t("核对说明 / 操作原因（至少三个字）") }}<textarea v-model="reason" maxlength="2000" :disabled="!canWrite||busy"/></label></div>
    <div v-if="canWrite" class="fo-actions"><template v-if="period.status==='open'"><button :disabled="busy||reason.trim().length<3" @click="periodAction('save')">{{ $t("保存资料确认") }}</button><button :disabled="busy||reason.trim().length<3" @click="periodAction('submit')">{{ $t("提交复核") }}</button></template><template v-if="period.status==='submitted'&&data.can_review&&period.submitted_by!==data.current_user"><button :disabled="busy||reason.trim().length<3" @click="periodAction('close')">{{ $t("复核并关账") }}</button><button :disabled="busy||reason.trim().length<3" @click="periodAction('return')">{{ $t("退回更正") }}</button></template><button v-if="period.status==='closed'&&data.can_review" :disabled="busy||reason.trim().length<3" @click="periodAction('reopen')">{{ $t("重开本月") }}</button></div>
    <p v-if="periodDirty">{{ $t("资料确认尚未保存，切换月份前请保存，或关闭工作台并放弃本次填写。") }}</p><h3>{{ $t("月度操作记录") }}</h3><article v-for="event in period.events" :key="event.id" class="fo-card">{{ $t({save:'保存确认',submit:'提交',close:'关账',reopen:'重开',return:'退回'}[event.action]) }} · {{ $t(event.actor) }} · {{ $t(event.created_at) }}<p>{{ event.reason }}</p></article>
   </template>
   <template v-if="tab==='outcomes'"><p>{{ $t("对照固定方案与实际业务。关联合同、费用和人工核验在「处理与核验」维护，差额不自动视为措施收益。") }}</p><p v-if="!outcomes.length&&!loading">{{ $t("本页暂无本年度已保存措施。先保存启用措施的方案，并建立执行事项。") }}</p><article v-for="row in outcomes" :key="row.action.id" class="fo-card"><h3>{{ $t(row.action.title) }}</h3><p>{{ $t("负责人 ") }}{{ $t(row.action.owner) }}{{ $t(" · 基准版本 #") }}{{ $t(row.baseline_version_id) }} · {{ $t(row.action.benefit?.amount_yuan!==undefined?'人工核验 '+money(row.action.benefit.amount_yuan)+' 元':'尚未人工核验') }}</p><p>{{ $t(row.basis) }}</p><p v-if="!row.comparison_available">{{ $t("尚无可比业务关联或固定费用基准，实际收益不推定为零。") }}</p><div class="fo-scroll"><table><thead><tr><th>{{ $t("月份") }}</th><th>{{ $t("计划（元）") }}</th><th>{{ $t("业务观察值") }}</th><th>{{ $t("差额") }}</th><th>{{ $t("实收") }}</th><th>{{ $t("成本") }}</th><th>{{ $t("实付") }}</th></tr></thead><tbody><tr v-for="m in row.months.filter(m=>m.expected_yuan||m.observed_yuan||m.cash_yuan||m.cost_yuan||m.paid_yuan||m.month===Number(month.slice(-2)))" :key="m.month"><td>{{ $t(m.month) }}{{ $t(" 月") }}</td><td>{{ $t(money(m.expected_yuan)) }}</td><td>{{ $t(m.observed_yuan===null?'待关联':money(m.observed_yuan)) }}</td><td>{{ $t(m.delta_yuan===null?'—':money(m.delta_yuan)) }}</td><td>{{ $t(money(m.cash_yuan)) }}</td><td>{{ $t(money(m.cost_yuan)) }}</td><td>{{ $t(money(m.paid_yuan)) }}</td></tr></tbody></table></div></article></template>
   <div v-if="['cash','bank','outcomes'].includes(tab)" class="fo-actions"><button :disabled="page===1||loading" @click="page--">{{ $t("上一页") }}</button><span>{{ $t("第 ") }}{{ $t(page) }}{{ $t(" 页 · 共 ") }}{{ $t(count||0) }}{{ $t(" 条") }}</span><button :disabled="page*25>=(count||0)||loading" @click="page++">{{ $t("下一页") }}</button></div>
  </div>
  <BaseModal :open="Boolean(form)" :title="({cash:'登记收付款',allocate:'分配资金',bank:'导入银行明细',match:'人工确认匹配',expense:'新增费用',detail:'费用资料',reverse:'撤销分配',void:'作废流水',unmatch:'撤销银行匹配'})[form]||''" @close="closeForm">
   <form class="finance-ops" @submit.prevent="save"><p v-if="error" class="error-text" role="alert">{{ $t(error) }}</p><button v-if="error&&['allocate','match','detail','reverse','void','unmatch'].includes(form)" type="button" :disabled="busy" @click="refreshDraft">{{ $t("读取最新版本并保留填写") }}</button><div class="fo-grid" :inert="busy">
    <template v-if="form==='cash'"><label>{{ $t("收付方向") }}<select v-model="draft.direction"><option value="in">{{ $t("收入") }}</option><option value="out">{{ $t("支出") }}</option></select></label><label>{{ $t("实际日期") }}<input v-model="draft.occurred_on" type="date" required></label><label>{{ $t("金额（元）") }}<input v-model="draft.amount" type="number" min="0.01" step="0.01" required></label><label>{{ $t("往来方") }}<input v-model="draft.counterparty" maxlength="150" required></label><label>{{ $t("摘要 / 单据号") }}<input v-model="draft.reference" maxlength="150"></label><label>{{ $t("备注") }}<textarea v-model="draft.note" maxlength="2000"/></label></template>
    <template v-if="form==='allocate'"><label>{{ $t("用途") }}<select v-model="draft.kind"><option v-for="(v,k) in financeKinds" :value="k" :key="k">{{ $t(v) }}</option></select></label><label>{{ $t("所属账期") }}<input v-model="draft.period_month" type="month" required></label><label>{{ $t("分配金额（元）") }}<input v-model="draft.amount" type="number" min="0.01" :max="selected.remaining" step="0.01" required></label><label v-if="draft.kind==='expense'">{{ $t("关联费用") }}<select v-model="draft.expense_id" required @change="chooseExpense"><option value="">{{ $t("请选择") }}</option><option v-for="e in expenses" :key="e.id" :value="e.id">#{{ $t(e.id) }} {{ e.name }}{{ $t(" · 未付 ") }}{{ $t(money(e.unpaid_yuan)) }}</option></select></label><template v-else><label>{{ $t("搜索合同") }}<input v-model="keyword" :placeholder="$t(&quot;合同编号或租户&quot;)"><button type="button" @click="loadChoices">{{ $t("搜索") }}</button></label><label>{{ $t("关联合同") }}<select v-model="draft.contract_id" required @change="draft.deposit_id='';draft.existing_payment_id='';loadChoices()"><option value="">{{ $t("请选择") }}</option><option v-for="c in choices.contracts" :key="c.id" :value="c.id">{{ $t(c.label) }}</option></select></label><label v-if="draft.kind==='deposit'">{{ $t("已有押金记录") }}<select v-model="draft.deposit_id" required><option value="">{{ $t("请选择") }}</option><option v-for="d in choices.deposits" :key="d.id" :value="d.id">#{{ $t(d.id) }} {{ $t(d.deposit_type) }}{{ $t(" · 已收 ") }}{{ $t(money(d.received_amount)) }}{{ $t(" · 已退 ") }}{{ $t(money(d.refunded_amount)) }}</option></select></label><label v-if="draft.kind==='rent'">{{ $t("租金入账方式") }}<select v-model="draft.existing_payment_id"><option value="">{{ $t("新增租金实收") }}</option><option v-for="p in choices.payments" :key="p.id" :value="p.id">{{ $t("关联已有 #") }}{{ $t(p.id) }} · {{ $t(p.period_month) }} · {{ $t(p.payment_date) }} · {{ $t(money(p.amount)) }}</option></select></label></template><label class="fo-wide">{{ $t("说明") }}<textarea v-model="draft.note" maxlength="2000"/></label></template>
    <template v-if="form==='bank'"><label class="fo-wide">{{ $t("UTF-8 CSV 文件") }}<input type="file" accept=".csv,text/csv" required @change="readCsv"></label><p>{{ $t("准备导入 ") }}{{ $t(csvRows.length) }}{{ $t(" 条，请核对预览后保存。") }}</p><div class="fo-scroll fo-wide"><table><thead><tr><th>{{ $t("日期") }}</th><th>{{ $t("往来方") }}</th><th>{{ $t("方向") }}</th><th>{{ $t("金额") }}</th></tr></thead><tbody><tr v-for="r in csvRows.slice(0,20)" :key="r.import_key"><td>{{ $t(r.occurred_on) }}</td><td>{{ r.counterparty }}</td><td>{{ $t(r.direction==='in'?'收入':'支出') }}</td><td>{{ $t(money(r.amount)) }}</td></tr></tbody></table><p v-if="csvRows.length>20">{{ $t("仅预览前 20 条，将导入全部 ") }}{{ $t(csvRows.length) }}{{ $t(" 条。") }}</p></div></template>
    <template v-if="form==='match'"><label class="fo-wide">{{ $t("候选资金流水") }}<select v-model="draft.cash_id" required @change="chooseCash"><option value="">{{ $t("请选择并核实日期与往来方") }}</option><option v-for="c in selected.suggestions" :key="c.id" :value="c.id">#{{ $t(c.id) }} · {{ $t(c.occurred_on) }} · {{ c.counterparty }}{{ $t(" · 待核对 ") }}{{ $t(money(c.bank_remaining)) }}</option></select></label><label>{{ $t("手动按资金编号查找") }}<input v-model="draft.cash_id" type="number" min="1"><button type="button" @click="lookupCash">{{ $t("读取资金流水") }}</button></label><label>{{ $t("匹配金额（元）") }}<input v-model="draft.amount" type="number" min="0.01" step="0.01" :max="selected.remaining" required></label><label>{{ $t("人工匹配依据") }}<textarea v-model="draft.note" maxlength="2000"/></label><p v-if="!selected.suggestions.length">{{ $t("没有候选时，请先登记对应资金流水并核对摘要、往来方或金额。") }}</p></template>
    <template v-if="['expense','detail'].includes(form)"><template v-if="form==='expense'"><label>{{ $t("费用名称") }}<input v-model="draft.name" maxlength="150" required></label><label>{{ $t("性质") }}<select v-model="draft.category"><option value="opex">{{ $t("经营费用") }}</option><option value="capex">{{ $t("资本支出") }}</option></select></label><label>{{ $t("金额（元）") }}<input v-model="draft.amount_yuan" type="number" min="0.01" step="0.01" required></label><label>{{ $t("发生月") }}<input v-model="draft.occurrence_month" type="month" required></label><label>{{ $t("计划付款月") }}<input v-model="draft.payment_month" type="month" required></label><label>{{ $t("负责人") }}<input v-model="draft.owner" maxlength="100" required></label></template><label>{{ $t("供应商") }}<input v-model="draft.supplier" maxlength="150" required></label><label>{{ $t("费用类别") }}<input v-model="draft.cost_category" maxlength="80" required></label><label>{{ $t("成本中心") }}<input v-model="draft.cost_center" maxlength="100"></label><label>{{ $t("服务开始月") }}<input v-model="draft.service_from" type="month" required></label><label>{{ $t("服务结束月") }}<input v-model="draft.service_to" type="month" :min="draft.service_from" required></label><label class="fo-wide">{{ $t("凭据编号 / 证据说明") }}<textarea v-model="draft.evidence" minlength="3" maxlength="2000" required/></label></template>
    <label v-if="['reverse','void','unmatch'].includes(form)" class="fo-wide">{{ $t("更正原因") }}<textarea v-model="draft.reason" minlength="3" maxlength="2000" required/></label>
   </div><div class="fo-actions"><button type="button" :disabled="busy" @click="closeForm">{{ $t("取消") }}</button><button type="submit" :disabled="busy||!canWrite||(form==='bank'&&!csvRows.length)">{{ $t(busy?'保存中…':'确认保存') }}</button></div></form>
  </BaseModal>
  <BaseModal :open="discard" :title="$t(&quot;保留未保存内容？&quot;)" @close="discard=false"><p>{{ $t("当前填写内容尚未保存。") }}</p><button @click="discard=false">{{ $t("继续填写") }}</button><button @click="discardDraft">{{ $t("放弃填写并关闭") }}</button></BaseModal>
 </BaseModal>
</template>
<style scoped>
.fo-handoff-hero{padding:24px;border:1px solid #d5e6fb;border-radius:14px;background:linear-gradient(120deg,#fff,#f0f7ff);color:#17395c}.fo-handoff-hero h3{font-size:26px;line-height:1.35;margin:12px 0}.fo-handoff-note{font-size:13px;color:#586c7f}
.fo-mapped-check{display:flex!important;align-items:center;gap:10px}.fo-mapped-check input{width:20px;min-height:20px;flex-shrink:0}
.finance-ops{display:grid;gap:16px;min-width:0;color:var(--text-primary,#213448)}.finance-ops button{min-height:44px;padding:8px 14px;border:1px solid #bbcbd2;border-radius:8px;background:#fff;color:#24434c;cursor:pointer}.finance-ops button:disabled{opacity:.5;cursor:default}.fo-tabs{display:flex;gap:6px;flex-wrap:wrap}.fo-tabs button[aria-pressed=true]{background:#164f51;color:white;border-color:#164f51}.fo-toolbar,.fo-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.fo-grid,.fo-metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.fo-wide{grid-column:1/-1}.finance-ops label{display:grid;gap:6px;min-width:0;font-weight:600}.finance-ops input,.finance-ops select,.finance-ops textarea{box-sizing:border-box;width:100%;min-width:0;min-height:44px;padding:10px;border:1px solid #bbcbd2;border-radius:7px;background:white;color:#213448;font:inherit}.finance-ops textarea{min-height:80px}.fo-metrics article{display:grid;gap:8px;background:#eff6f5;padding:16px;border-radius:10px}.fo-metrics strong{font-size:20px}.fo-card{padding:16px;border:1px solid #d6e0e3;border-radius:10px;overflow-wrap:anywhere}.fo-card h3{margin:0 0 10px;font-size:16px}.fo-card p{margin:8px 0}.fo-scroll{overflow-x:auto}.finance-ops table{width:100%;border-collapse:collapse;white-space:nowrap}.finance-ops th,.finance-ops td{padding:10px;text-align:right;border-bottom:1px solid #d6e0e3}.finance-ops th:first-child,.finance-ops td:first-child{text-align:left}.error-text{color:#a32727;background:#fff0ee;padding:12px;border-radius:8px}.finance-ops li{padding:6px 0}.finance-ops summary{cursor:pointer;padding:12px 0}@media(max-width:600px){.fo-grid,.fo-metrics{grid-template-columns:1fr}.fo-wide{grid-column:1}.fo-tabs button{flex:1 1 40%}.fo-card{padding:12px}}
</style>
