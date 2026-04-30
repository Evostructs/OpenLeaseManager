<script setup>
import { ref } from 'vue'
import BaseModal from './BaseModal.vue'

const props = defineProps({
  open: Boolean,
  contract: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['close', 'upload', 'view'])
const file = ref(null)

function onChange(event) {
  file.value = event.target.files?.[0] || null
}

function submit() {
  if (!file.value) return
  emit('upload', file.value)
  file.value = null
}
</script>

<template>
  <BaseModal :open="open" title="合同 PDF" size="small" @close="emit('close')">
    <div class="detail-list">
      <div>
        <div class="section-label">当前合同</div>
        <h3 class="card-title" style="margin-top:6px">{{ contract?.contract_no }}</h3>
        <p class="helper-text">{{ contract?.tenant_name }}</p>
      </div>
      <div class="form-field">
        <span class="section-label">上传文件</span>
        <input type="file" accept=".pdf" class="input" @change="onChange" />
      </div>
    </div>
    <template #footer>
      <button class="btn" @click="emit('view')">查看已有 PDF</button>
      <button class="btn" @click="emit('close')">关闭</button>
      <button class="btn btn-primary" @click="submit">上传</button>
    </template>
  </BaseModal>
</template>
