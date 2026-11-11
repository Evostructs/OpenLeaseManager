<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { api } from '../lib/api'
const props = defineProps({ profile: { type: Object, required: true }, canConfigure: Boolean })
const emit = defineEmits(['dirty', 'busy', 'saved'])
const expanded = ref(false), draft = ref({}), banks = ref([]), busy = ref(false), error = ref(''), notice = ref('')
const accounts = {rent:'租金', property_fee:'物业费', deposit:'押金', opex:'经营费用', capex:'资本支出'}
const baseline = ref('')
let sequence = 0
const payload = () => ({...draft.value, bank_accounts: Object.fromEntries(banks.value.map(row => [row.label.trim(), row.code.trim()]))})
const state = () => JSON.stringify({draft:draft.value, banks:banks.value})
const dirty = computed(() => baseline.value !== '' && state() !== baseline.value)
function changed(event) { if(event.target.type!=='checkbox')draft.value.confirmed=false }
function accept(profile) {
 draft.value = Object.fromEntries(['company_name','company_code','erp_system','book_code','tax_basis','note','confirmed'].map(key => [key, profile[key]]))
 draft.value.accounts = {...profile.accounts}; draft.value.expected_revision = profile.revision
 banks.value = Object.entries(profile.bank_accounts).map(([label,code]) => ({label,code}))
 baseline.value = state()
}
watch(() => props.profile, profile => { if (!dirty.value) accept(profile) }, {immediate:true})
watch(dirty, value => emit('dirty', value), {flush:'sync'})
watch(busy, value => emit('busy', value), {flush:'sync'})
async function save() {
 if (busy.value || !props.canConfigure) return
 error.value='';notice.value=''
 const labels=banks.value.map(row=>row.label.trim())
 if(labels.some(label=>!label)||new Set(labels).size!==labels.length){error.value='银行简称不能为空或重复。';return}
 const token=++sequence;busy.value=true
 try {
  const result=await api('/api/finance-operations/handoff/mapping',{method:'PUT',body:JSON.stringify(payload())})
  if(token!==sequence)return
  accept(result);notice.value='映射已保存，交接预览已更新。';emit('saved')
 } catch(e) {if(token===sequence)error.value=e.message} finally {if(token===sequence)busy.value=false}
}
async function readRevision() {
 if(busy.value)return
 const token=++sequence;busy.value=true
 try {
  const result=await api('/api/finance-operations/handoff/mapping',{cache:'no-store'})
  if(token!==sequence)return
  draft.value.expected_revision=result.revision;draft.value.confirmed=false
  notice.value='已读取最新修订，保留你的填写。请对照最新配置核对后重新确认。'
  error.value=''
 } catch(e){if(token===sequence)error.value=e.message}finally{if(token===sequence)busy.value=false}
}
onBeforeUnmount(()=>{sequence++;emit('dirty',false);emit('busy',false)})
</script>
<template>
 <section class="mapping-panel">
  <div class="mapping-heading"><div><span class="mapping-eyebrow">{{ $t("客户财务配置 · 第 ") }}{{ $t(profile.revision) }}{{ $t(" 次修订") }}</span><h3>{{ $t("业务交得清，财务接得上。") }}</h3><p>{{ $t(profile.ready?'法人、账套与科目映射已确认。':'配置法人、账套、科目和银行简称，供财务人员核对交接。') }}</p></div><button type="button" :disabled="busy" @click="expanded=!expanded">{{ $t(expanded?'收起配置':'查看财务映射') }}</button></div>
  <p v-if="profile.missing.length" class="mapping-gaps">{{ $t("待补齐：") }}{{ $t(profile.missing.join('、')) }}</p>
  <form v-if="expanded" @submit.prevent="save">
   <p v-if="error" class="mapping-error" role="alert">{{ $t(error) }}</p><p v-if="notice" role="status">{{ $t(notice) }}</p>
   <fieldset :disabled="!canConfigure||busy" @input="changed"><div class="mapping-grid">
    <label>{{ $t("法人名称") }}<input v-model="draft.company_name" maxlength="150"></label><label>{{ $t("财务组织编码") }}<input v-model="draft.company_code" maxlength="40"></label>
    <label>{{ $t("财务系统") }}<input v-model="draft.erp_system" maxlength="80" :placeholder="$t(&quot;例如：用友、金蝶、SAP&quot;)"></label><label>{{ $t("账套编码") }}<input v-model="draft.book_code" maxlength="40"></label>
    <label>{{ $t("金额含税口径") }}<select v-model="draft.tax_basis"><option value="">{{ $t("请选择") }}</option><option value="gross">{{ $t("含税金额") }}</option><option value="net">{{ $t("不含税金额") }}</option><option value="not_applicable">{{ $t("不适用，由财务另行确认") }}</option></select></label>
    <label v-for="(label,key) in accounts" :key="key">{{ $t(label) }}{{ $t("科目编码") }}<input v-model="draft.accounts[key]" maxlength="40"></label>
   </div><h4>{{ $t("银行简称 → 财务账户编码") }}</h4>
   <div v-for="(row,index) in banks" :key="index" class="mapping-bank"><label>{{ $t("银行简称") }}<input v-model="row.label" maxlength="100" :placeholder="$t(&quot;与银行流水中的账户简称一致&quot;)"></label><label>{{ $t("财务账户编码") }}<input v-model="row.code" maxlength="40"></label><button type="button" @click="banks.splice(index,1);draft.confirmed=false">{{ $t("移除此映射") }}</button></div>
   <button type="button" @click="banks.push({label:'',code:''});draft.confirmed=false">{{ $t("增加银行映射") }}</button>
   <label class="mapping-note">{{ $t("核对依据") }}<textarea v-model="draft.note" maxlength="2000" :placeholder="$t(&quot;科目表版本、客户财务确认依据&quot;)"></textarea></label>
   <label class="mapping-check"><input v-model="draft.confirmed" type="checkbox">{{ $t("已与客户财务核对法人、金额口径、科目和银行映射") }}</label>
   </fieldset>
   <div v-if="canConfigure" class="mapping-actions"><button type="submit" :disabled="busy||!dirty">{{ $t(busy?'正在保存…':'保存财务映射') }}</button><button v-if="error" type="button" :disabled="busy" @click="readRevision">{{ $t("读取最新修订并保留填写") }}</button></div>
   <p v-else>{{ $t("财务映射由具有本楼编辑权限的管理员维护。") }}</p>
   <p class="mapping-footnote">{{ $t("映射随交接包保存。科目编码需按客户财务制度核定，金额口径仅作登记。") }}</p>
  </form>
 </section>
</template>
<style scoped>
.mapping-panel{padding:22px;border:1px solid #d6e4f4;border-radius:14px;background:#fff;color:#17395c;min-width:0}.mapping-heading{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}.mapping-heading h3{font-size:22px;margin:8px 0}.mapping-heading p{margin:8px 0}.mapping-eyebrow,.mapping-footnote{font-size:13px;color:#59718b}.mapping-gaps{padding:12px;background:#f0f6fd;border-radius:8px;overflow-wrap:anywhere}.mapping-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.mapping-bank{display:grid;grid-template-columns:1fr 1fr auto;gap:12px;align-items:end;margin:12px 0}.mapping-panel fieldset{border:0;padding:0;margin:16px 0;min-width:0}.mapping-panel label{display:grid;gap:6px;font-weight:600;min-width:0}.mapping-panel input,.mapping-panel select,.mapping-panel textarea{box-sizing:border-box;width:100%;min-width:0;min-height:44px;padding:10px;border:1px solid #bccddd;border-radius:7px;background:#fff;color:#213448;font:inherit}.mapping-panel button{min-height:44px;padding:8px 14px;border:1px solid #b9cddd;border-radius:8px;background:#f5f9ff;color:#245a92;cursor:pointer}.mapping-panel button:disabled{opacity:.5;cursor:default}.mapping-note{margin-top:16px}.mapping-check{display:flex!important;align-items:center;margin-top:16px}.mapping-check input{width:20px;min-height:20px;flex-shrink:0}.mapping-actions{display:flex;gap:10px;flex-wrap:wrap}.mapping-error{color:#a32727;background:#fff0ee;padding:12px;border-radius:8px}.mapping-panel h4{margin-bottom:8px}@media(max-width:600px){.mapping-panel{padding:16px}.mapping-grid,.mapping-bank{grid-template-columns:1fr}}
</style>
