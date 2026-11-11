<script setup>
import { computed, ref, watch } from 'vue'
const props=defineProps({ coverage:{type:Object,default:()=>({})} })
const emit=defineEmits(['open-contract'])
const page=ref(1)
const issues=computed(()=>[...(props.coverage.excluded_contracts||[]).map(row=>({...row,type:'合同未纳入预测'})),...(props.coverage.unresolved_payments||[]).map(row=>({...row,type:'到账日期待补'}))])
const visible=computed(()=>issues.value.slice((page.value-1)*20,page.value*20))
watch(()=>props.coverage,()=>{page.value=1})
</script>
<template>
 <section class="dw-source-coverage" aria-label="财务现金覆盖范围">
  <p v-if="coverage.property_fee_cash_included===false"><strong>现金口径暂不包含物业费收款。</strong>收入中的物业费不等于已收到现金；押金独立管理，不作为经营收入。</p>
  <p v-else-if="coverage.property_fee_cash_included===true">{{ coverage.property_fee_cash_basis }}</p>
  <p v-else-if="coverage.property_fee_cash_included===undefined">此历史版本未记录物业费现金覆盖标记，请按保存时口径核对。</p>
  <details v-if="issues.length"><summary>查看 {{ issues.length }} 条待补资料与排除记录</summary><ul><li v-for="(row,index) in visible" :key="`${page}-${index}`">{{ row.type }} · {{ row.contract_no||'关联合同' }} {{ row.period_month||'' }} · {{ row.reason }} <span v-if="row.amount_yuan!==undefined"> · {{ Number(row.amount_yuan).toLocaleString() }} 元</span> <button type="button" class="dw-text-button" @click="emit('open-contract',{id:row.contract_id,initialSection:row.type==='到账日期待补'?'receivables':''})">打开对应记录</button></li></ul><button type="button" :disabled="page===1" @click="page--">上一页</button><span> {{ page }} / {{ Math.ceil(issues.length/20) }} </span><button type="button" :disabled="page*20>=issues.length" @click="page++">下一页</button><p>补录通过原合同或应收流程完成；已保存的历史版本保持原样，新版本重新读取后反映修正。</p></details>
 </section>
</template>
