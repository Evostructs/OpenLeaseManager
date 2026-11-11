<script setup>
import { computed, reactive, ref, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import DatePickerInput from './DatePickerInput.vue'
import { api } from '../lib/api'

const props = defineProps({
  open: Boolean,
  request: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['close', 'notify'])

const form = reactive({
  document_type: 'payment_notice',
  contract_id: null,
  month: '',
  payment_id: null,
  notice_date: '',
  payment_instruction: '',
  notes: '',
  contract_no: '',
  tenant_name: '',
  payment_summary: ''
})

const documentTypeLabels = {
  payment_notice: '缴款通知书',
  dunning_notice: '催收通知',
  receipt: '收据'
}

const generating = ref(false)
const generatedDocument = ref(null)
const generateError = ref('')
let generationRequestId = 0

const title = computed(() => ({
  payment_notice: '生成缴款通知书',
  dunning_notice: '生成催收通知',
  receipt: '生成收据'
}[form.document_type] || '生成文档'))

const isReceipt = computed(() => form.document_type === 'receipt')
const resultDocumentLabel = computed(() => generatedDocument.value?.document_label || documentTypeLabels[form.document_type] || '文档')
const resultFilename = computed(() => generatedDocument.value?.filename || 'lease-document.html')

function currentDateValue() {
  const today = new Date()
  return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
}

function resetForm() {
  const request = props.request || {}
  Object.assign(form, {
    document_type: request.document_type || 'payment_notice',
    contract_id: request.contract_id || null,
    month: request.month || '',
    payment_id: request.payment_id || null,
    notice_date: request.notice_date || currentDateValue(),
    payment_instruction: request.payment_instruction || '请按合同约定或双方确认的付款账户及时支付。',
    notes: request.notes || '',
    contract_no: request.contract_no || '',
    tenant_name: request.tenant_name || '',
    payment_summary: request.payment_summary || ''
  })
}

function clearDocumentState() {
  generationRequestId += 1
  generating.value = false
  generatedDocument.value = null
  generateError.value = ''
}

watch(
  () => [props.open, props.request],
  () => {
    if (props.open) {
      clearDocumentState()
      resetForm()
    } else {
      clearDocumentState()
    }
  },
  { immediate: true, deep: true }
)

function buildPayload() {
  return {
    document_type: form.document_type,
    contract_id: form.contract_id,
    month: form.month || null,
    payment_id: form.payment_id || null,
    notice_date: form.notice_date || null,
    payment_instruction: isReceipt.value ? '' : form.payment_instruction,
    notes: form.notes
  }
}

function documentBlob(result) {
  return new Blob([result.html || ''], { type: result.content_type || 'text/html;charset=utf-8' })
}

function openHtml(result) {
  const url = URL.createObjectURL(documentBlob(result))
  window.open(url, '_blank', 'noopener,noreferrer')
  window.setTimeout(() => URL.revokeObjectURL(url), 60000)
}

function downloadHtml(result) {
  const url = URL.createObjectURL(documentBlob(result))
  const link = document.createElement('a')
  link.href = url
  link.download = result.filename || 'lease-document.html'
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function previewGeneratedDocument() {
  if (generatedDocument.value) openHtml(generatedDocument.value)
}

function downloadGeneratedDocument() {
  if (generatedDocument.value) downloadHtml(generatedDocument.value)
}

function closeModal() {
  clearDocumentState()
  emit('close')
}

async function generate() {
  if (generating.value) return
  const requestId = generationRequestId + 1
  generationRequestId = requestId
  generating.value = true
  generatedDocument.value = null
  generateError.value = ''
  try {
    const result = await api('/api/contracts/documents/generate', {
      method: 'POST',
      body: JSON.stringify(buildPayload())
    })
    if (generationRequestId !== requestId || !props.open) return
    generatedDocument.value = result
    emit('notify', { text: `${result.document_label || '文档'}已生成` })
  } catch {
    if (generationRequestId !== requestId || !props.open) return
    generateError.value = '文档未生成，请检查合同、账期或收款信息后重试。'
  } finally {
    if (generationRequestId === requestId) generating.value = false
  }
}
</script>

<template>
  <BaseModal :open="open" :title="$t(title)" size="small" @close="closeModal">
    <div class="document-form" data-testid="document-generate-modal" :aria-busy="generating">
      <div class="document-summary">
        <div>
          <span class="section-label">{{ $t("合同") }}</span>
          <strong>{{ form.contract_no || '--' }}</strong>
        </div>
        <div>
          <span class="section-label">{{ $t("客户") }}</span>
          <strong>{{ form.tenant_name || '--' }}</strong>
        </div>
        <div v-if="form.payment_summary">
          <span class="section-label">{{ $t("收款") }}</span>
          <strong>{{ form.payment_summary }}</strong>
        </div>
      </div>

      <div class="form-grid">
        <label v-if="!isReceipt" class="form-field">
          <span class="section-label">{{ $t("账期") }}</span>
          <input v-model="form.month" class="input" type="month" />
        </label>
        <div class="form-field">
          <span class="section-label">{{ $t("通知日期") }}</span>
          <DatePickerInput v-model="form.notice_date" />
        </div>
        <label v-if="!isReceipt" class="form-field full">
          <span class="section-label">{{ $t("付款说明") }}</span>
          <textarea v-model="form.payment_instruction" class="textarea compact-textarea" />
        </label>
        <label class="form-field full">
          <span class="section-label">{{ $t("备注") }}</span>
          <textarea v-model="form.notes" class="textarea compact-textarea" />
        </label>
      </div>

      <p v-if="generateError" class="document-generate-error" data-testid="document-generate-error" role="alert">
        {{ $t(generateError) }}
      </p>

      <section v-if="generatedDocument" class="document-result-panel" data-testid="document-result-panel" aria-live="polite">
        <div class="document-result-meta">
          <div>
            <span class="section-label">{{ $t("文档类型") }}</span>
            <strong>{{ $t(resultDocumentLabel) }}</strong>
          </div>
          <div>
            <span class="section-label">{{ $t("文件名") }}</span>
            <strong>{{ $t(resultFilename) }}</strong>
          </div>
        </div>
        <div class="document-result-actions">
          <button class="btn" data-testid="document-result-preview" type="button" @click="previewGeneratedDocument">{{ $t(" 预览 ") }}</button>
          <button class="btn" data-testid="document-result-download" type="button" @click="downloadGeneratedDocument">{{ $t(" 下载 ") }}</button>
        </div>
      </section>
    </div>
    <template #footer>
      <button class="btn" type="button" @click="closeModal">{{ $t("关闭") }}</button>
      <button
        class="btn btn-primary document-generate-submit"
        data-testid="document-generate-submit"
        type="button"
        :disabled="generating"
        @click="generate"
      >
        {{ $t(generating ? '正在生成...' : generatedDocument ? '重新生成' : '生成文档') }}
      </button>
    </template>
  </BaseModal>
</template>
