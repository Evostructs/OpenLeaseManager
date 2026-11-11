<script setup>
import { computed, reactive, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import DatePickerInput from './DatePickerInput.vue'

const props = defineProps({
  open: Boolean,
  contract: {
    type: Object,
    default: null
  },
  defaultEndDate: {
    type: String,
    default: ''
  },
  submitting: Boolean
})

const emit = defineEmits(['close', 'confirm'])

const form = reactive({
  end_date: ''
})

const displayName = computed(() => props.contract?.tenant_name || props.contract?.customer_name || props.contract?.customer?.name || '')

function resetForm() {
  form.end_date = props.defaultEndDate || ''
}

function close() {
  if (props.submitting) return
  emit('close')
}

function submit() {
  if (props.submitting) return
  emit('confirm', {
    end_date: form.end_date || null
  })
}

watch(
  () => [props.open, props.contract?.id, props.defaultEndDate],
  () => {
    if (props.open) resetForm()
  },
  { immediate: true }
)
</script>

<template>
  <BaseModal :open="open" title="标记退租" size="small" @close="close">
    <div data-testid="contract-terminate-modal" class="detail-list">
      <div v-if="contract" class="form-field">
        <span class="section-label">当前合同</span>
        <div>{{ contract.contract_no || '--' }}<template v-if="displayName"> · {{ displayName }}</template></div>
      </div>

      <label class="form-field">
        <span class="section-label">退租日期</span>
        <DatePickerInput v-model="form.end_date" data-testid="contract-terminate-date" />
      </label>

      <div class="helper-text">
        留空则仅标记为已退租，不调整合同终止日；填写日期会同步截断房源明细截止日。
      </div>
      <div class="helper-text warning">
        确认后合同处置将变为“已退租”，并刷新合同列表与仪表盘。
      </div>
    </div>

    <template #footer>
      <button class="btn" data-testid="contract-terminate-cancel" :disabled="submitting" @click="close">取消</button>
      <button class="btn btn-primary" data-testid="contract-terminate-confirm" :disabled="submitting" @click="submit">
        {{ submitting ? '提交中' : '确认退租' }}
      </button>
    </template>
  </BaseModal>
</template>
