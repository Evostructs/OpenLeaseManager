<script setup>
import { ref, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import { api } from '../lib/api'
const props = defineProps({ open: Boolean, scopeKey: String, scopeLabel: String, allowedTypes: { type: Array, default: () => [] } })
const emit = defineEmits(['close','select'])
const keyword = ref(''); const groups = ref([]); const busy = ref(false); const message = ref(''); let generation = 0
watch(() => [props.open, props.scopeKey, props.allowedTypes.join(',')], () => { generation++; keyword.value = ''; groups.value = []; busy.value = false; message.value = '' })
async function search() {
  const text = keyword.value.trim(); const current = ++generation
  groups.value = []; message.value = ''
  if (!text) { busy.value = false; return }
  busy.value = true
  const kinds = [{type:'contract', permission:'contracts', label:'合同', path:'/api/contracts'}, {type:'customer', permission:'customers', label:'客户', path:'/api/customers'}, {type:'room',permission:'rooms', label:'房源',path:'/api/rooms/search'}].filter(kind => props.allowedTypes.includes(kind.permission))
  const results = await Promise.allSettled(kinds.map(kind => api(`${kind.path}?page=1&page_size=6&keyword=${encodeURIComponent(text)}`)))
  if (current !== generation || !props.open) return
  groups.value = results.flatMap((result,index) => result.status === 'fulfilled' ? [{ ...kinds[index], total:result.value.total || 0, items:result.value.items || [] }] : [])
  if (results.some(result => result.status === 'rejected')) message.value = '部分对象暂时无法读取，请重试；仅显示已获授权且查询成功的结果。'
  else if (!groups.value.some(group => group.items.length)) message.value = '当前范围没有匹配结果'
  busy.value = false
}
function choose(type, item) { if (type === 'room') emit('close'); emit('select', { type, item }) }
</script>
<template>
  <BaseModal :open="open" :title="$t(&quot;查找合同、客户或房号&quot;)" :layer-index="0" @close="emit('close')">
    <p class="helper-text">{{ $t("范围：") }}{{ $t(scopeLabel) }}{{ $t("。只查询当前可访问的业务资料，每类最多显示 6 条。") }}</p>
    <form class="search-query" @submit.prevent="search">
      <input v-model="keyword" class="input" maxlength="160" :aria-label="$t(&quot;统一查找关键词&quot;)" :placeholder="$t(&quot;合同号、客户名称或房号&quot;)" />
      <button class="btn btn-primary" :disabled="busy || !keyword.trim()">{{ $t(busy ? '查找中…' : '查找') }}</button>
    </form>
    <p v-if="message" role="status">{{ $t(message) }}</p>
    <section v-for="group in groups" :key="group.type" class="search-group">
      <h4>{{ $t(group.label) }} · {{ $t(group.total) }}{{ $t(" 条") }}</h4>
      <button v-for="item in group.items" :key="item.id" class="search-result" @click="choose(group.type,item)">
        <strong>{{ item.contract_no || item.name || item.room_no }}</strong>
        <span>{{ item.tenant_name || item.tower || item.status || group.label }}</span>
      </button>
    </section>
  </BaseModal>
</template>
<style scoped>
.search-query { display: flex; gap: 8px; } .search-query .input { min-width: 0; flex: 1; }
.search-result { width: 100%; display: flex; gap: 12px; justify-content: space-between; padding: 12px; margin: 4px 0; border: 1px solid var(--border); background: var(--surface); color: inherit; border-radius: 8px; text-align:left; cursor:pointer; overflow-wrap:anywhere; }
.search-result span { color: var(--muted); } .search-result strong { min-width: 0; }
</style>
