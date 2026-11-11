<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { createFinancialPlanningApi } from '../lib/financialPlanningApi.js'
import { applyStrategyReference, detachStrategyReference } from '../lib/strategyReference.js'
const props=defineProps({ modelValue:Object, versions:Array, buildingCode:String, disabled:Boolean })
const emit=defineEmits(['update:modelValue'])
const versionId=ref(''),hold=ref(0),renovate=ref(0),reason=ref(''),busy=ref(false),error=ref('')
let generation=0
const forecasts=computed(()=>(props.versions||[]).filter(row=>row.kind==='forecast'))
watch(()=>[props.buildingCode,props.modelValue?.reference],()=>{generation++;const source=props.modelValue?.reference;versionId.value=source?.version_id||'';hold.value=source?.hold_adjustment_wan||0;renovate.value=source?.renovate_adjustment_wan||0;reason.value=source?.reason||'';error.value=''},{immediate:true})
onBeforeUnmount(()=>{generation++})
async function apply(){
 if(busy.value||props.disabled||!versionId.value)return
 const token=generation;busy.value=true;error.value=''
 try{const version=await createFinancialPlanningApi(props.buildingCode).version(versionId.value);if(token!==generation)return;emit('update:modelValue',applyStrategyReference(props.modelValue,version,{hold:hold.value,renovate:renovate.value,reason:reason.value}))}
 catch(e){if(token===generation)error.value=e.message}finally{busy.value=false}
}
</script>
<template>
 <section class="dw-panel strategy-reference" aria-label="固定预测来源">
  <h3>引用已保存预测</h3><p>使用该版本的基准净现金流作为首年起点，不含未签约的假设措施。物业费现金、押金和资产层必要支出需按覆盖范围另行核对；可在下方明确调整。</p>
  <p v-if="modelValue.reference"><strong>当前固定来源：{{ modelValue.reference.version_name }}</strong> · {{ modelValue.reference.year }} 年第 {{ modelValue.reference.revision }} 版 · 基准 {{ modelValue.reference.net_cash_flow }} 万元。新预测不会自动改变这条引用。</p>
  <div class="strategy-reference-fields"><label>预测版本<select v-model="versionId" :disabled="disabled||busy"><option value="">选择固定预测版本</option><option v-for="row in forecasts" :key="row.id" :value="row.id">{{ row.year }} · {{ row.name }} · 第 {{ row.revision }} 版</option></select></label><label>持有首年调整（万元）<input v-model="hold" type="number" step="0.000001" :disabled="disabled||busy" /></label><label>改造后首年调整（万元）<input v-model="renovate" type="number" step="0.000001" :disabled="disabled||busy" /></label><label>调整依据<textarea v-model="reason" maxlength="2000" placeholder="例如：补入经常性资本开支或物业费现金；无调整可留空" :disabled="disabled||busy" /></label></div>
  <p v-if="error" role="alert">{{ error }}</p><div class="dw-actions"><button type="button" :disabled="disabled||busy||!versionId" @click="apply">{{ busy?'读取固定版本…':'应用引用与调整' }}</button><button v-if="modelValue.reference" type="button" :disabled="disabled||busy" @click="emit('update:modelValue',detachStrategyReference(modelValue))">改为手工输入，保留金额</button></div><p>应用后，使用上方“另存具名方案”保存来源版本及调整依据。首年停租损失仍单独扣除一次。</p>
 </section>
</template>
<style scoped>.strategy-reference-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.strategy-reference-fields label{display:grid;gap:6px}.strategy-reference-fields input,.strategy-reference-fields select,.strategy-reference-fields textarea{min-height:44px;width:100%;box-sizing:border-box}.strategy-reference p{line-height:1.65;overflow-wrap:anywhere}@media(max-width:640px){.strategy-reference-fields{grid-template-columns:1fr}}</style>
