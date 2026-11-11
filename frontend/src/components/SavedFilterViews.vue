<script setup>
import { computed, ref, watch } from 'vue'
import { buildViewLink, readSavedViews, saveView, viewStorageKey } from '../lib/savedViews'
const props = defineProps({ user: Object, scope: Object, page: String, filters: Object })
const emit = defineEmits(['apply'])
const name = ref(''); const selected = ref(''); const rows = ref([]); const message = ref('')
const key = computed(() => viewStorageKey(props.user, props.scope, props.page))
watch(key, () => { try { rows.value = readSavedViews(window.localStorage, key.value) } catch { rows.value = [] }; name.value = ''; selected.value = ''; message.value = '' }, { immediate: true })
const link = computed(() => buildViewLink(window.location.origin, props.page, props.scope, props.filters))
function save() {
  try { rows.value = saveView(window.localStorage, key.value, name.value, props.filters); selected.value = name.value.trim().slice(0,40); name.value = ''; message.value = '已保存到此浏览器，按账号与楼宇隔离' }
  catch (error) { message.value = error.message || '当前浏览器无法保存视图' }
}
function apply() { const row = rows.value.find(row => row.name === selected.value); if (row) emit('apply', { ...row.filters }) }
</script>
<template>
  <details class="saved-filter-views">
    <summary>常用筛选与链接</summary>
    <div class="saved-filter-controls">
      <label>常用视图 <select v-model="selected" class="select" @change="apply"><option value="">选择视图</option><option v-for="row in rows" :key="row.name">{{ row.name }}</option></select></label>
      <label>视图名称 <input v-model="name" class="input" maxlength="40" placeholder="例如：本楼即将到期" @keyup.enter="save" /></label>
      <button class="btn" :disabled="!name.trim() || !key" @click="save">保存当前筛选</button>
      <a v-if="link" class="btn" :href="link" target="_blank" rel="noopener">打开筛选链接</a>
    </div>
    <p v-if="message" class="helper-text" role="status">{{ message }}</p>
  </details>
</template>
<style scoped>
.saved-filter-views { padding: 8px 0; min-width: 0; }
summary { cursor: pointer; min-height: 36px; padding: 8px 0; }
.saved-filter-controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: end; }
label { display: grid; gap: 4px; min-width: 0; flex: 1 1 180px; }
@media(max-width:640px) { summary, .btn { min-height: 44px; } }
</style>
