<script setup>
import { computed } from 'vue'
import BaseModal from './BaseModal.vue'
import { formatCurrencyFixed, formatDate } from '../lib/format'

const props = defineProps({
  open: Boolean,
  preview: {
    type: Object,
    default: null
  },
  payload: {
    type: Object,
    default: null
  },
  submitting: Boolean
})

const emit = defineEmits(['close', 'confirm'])

const previous = computed(() => props.preview?.previous_contract || {})
const next = computed(() => props.preview?.new_contract || props.payload || {})
const receivableImpacts = computed(() => (
  Array.isArray(props.preview?.receivable_impacts) ? props.preview.receivable_impacts : []
))
const newContractNo = computed(() => next.value.contract_no || props.payload?.contract_no || '--')
const newCustomerName = computed(() => next.value.tenant_name || next.value.customer_name || props.payload?.tenant_name || '--')
const newRoomNo = computed(() => next.value.room_no || props.payload?.room_no || '--')
const effectiveDate = computed(() => props.preview?.change_effective_date || next.value.start_date || props.payload?.start_date || '')
const previousContractNo = computed(() => previous.value.contract_no || props.payload?.prev_contract_no || '--')
const previousEndBefore = computed(() => props.preview?.previous_end_date_before || '')
const previousEndAfter = computed(() => props.preview?.previous_end_date_after || '')

function close() {
  if (props.submitting) return
  emit('close')
}

function confirm() {
  if (props.submitting) return
  emit('confirm')
}
</script>

<template>
  <BaseModal :open="open" :title="$t(&quot;确认合同变更&quot;)" size="small" @close="close">
    <div data-testid="contract-change-preview-modal" class="detail-list">
      <div class="form-field">
        <span class="section-label">{{ $t("新合同") }}</span>
        <div>{{ $t(newContractNo) }}</div>
        <p class="helper-text">
          {{ $t(newCustomerName) }} · {{ $t(newRoomNo) }} · {{ $t(formatDate(effectiveDate)) }}{{ $t(" 生效 ") }}</p>
      </div>

      <div class="form-field">
        <span class="section-label">{{ $t("上一份合同") }}</span>
        <div>{{ $t(previousContractNo) }}</div>
        <p class="helper-text">{{ $t(" 终止日 ") }}{{ $t(formatDate(previousEndBefore)) }} -> {{ $t(formatDate(previousEndAfter)) }}
        </p>
      </div>

      <div v-if="receivableImpacts.length" class="form-field">
        <span class="section-label">{{ $t("应收影响") }}</span>
        <ul class="detail-list">
          <li v-for="item in receivableImpacts" :key="item.month || item.period_month">
            {{ $t(item.month || item.period_month || '--') }}{{ $t("：收款/核销 ") }}{{ $t(item.payment_count || 0) }}{{ $t(" 笔，已结算 ") }}{{ $t(formatCurrencyFixed(item.settlement_amount || 0)) }}
          </li>
        </ul>
      </div>
      <p v-else class="helper-text">{{ $t("无已登记的后续应收或收款/核销记录") }}</p>

      <p v-if="preview?.receivable_policy" class="helper-text warning">{{ $t(preview.receivable_policy) }}</p>
    </div>

    <template #footer>
      <button class="btn" data-testid="contract-change-preview-cancel" :disabled="submitting" @click="close">{{ $t("取消") }}</button>
      <button class="btn btn-primary" data-testid="contract-change-preview-confirm" :disabled="submitting" @click="confirm">
        {{ $t(submitting ? '提交中' : '确认生效') }}
      </button>
    </template>
  </BaseModal>
</template>
