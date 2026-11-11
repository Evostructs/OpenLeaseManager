<script setup>
import { computed, reactive, watch } from 'vue'
import BaseModal from './BaseModal.vue'
import DatePickerInput from './DatePickerInput.vue'
import { formatCurrencyFixed, formatDate } from '../lib/format'

const props = defineProps({
  open: Boolean,
  mode: {
    type: String,
    default: 'create'
  },
  contract: {
    type: Object,
    default: null
  },
  deposit: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['close', 'save'])

const depositTypes = ['租赁押金', '履约保证金', '其他']
const form = reactive({
  deposit_type: '租赁押金',
  expected_amount: 0,
  received_amount: 0,
  refunded_amount: 0,
  offset_amount: 0,
  due_date: '',
  received_date: '',
  refund_date: '',
  amount: 0,
  action_date: '',
  notes: ''
})

const isCreate = computed(() => props.mode === 'create')
const isEdit = computed(() => props.mode === 'edit')
const isAction = computed(() => ['receive', 'refund', 'offset'].includes(props.mode))
const availableAmount = computed(() => Math.max(Number(props.deposit?.available_amount || 0), 0))
const title = computed(() => {
  if (props.mode === 'edit') return '编辑押金'
  if (props.mode === 'receive') return '登记押金收取'
  if (props.mode === 'refund') return '登记押金退还'
  if (props.mode === 'offset') return '登记押金冲抵'
  return '登记应收押金'
})
const actionLabel = computed(() => {
  if (props.mode === 'receive') return '收取金额'
  if (props.mode === 'refund') return '退还金额'
  if (props.mode === 'offset') return '冲抵金额'
  return '金额'
})
const validationMessage = computed(() => {
  if (!props.contract?.id) return '合同信息不完整'
  if (isCreate.value || isEdit.value) {
    const amounts = [form.expected_amount, form.received_amount, form.refunded_amount, form.offset_amount].map((value) => Number(value || 0))
    if (amounts.some((value) => value < 0)) return '押金金额不得为负数'
    if (Number(form.refunded_amount || 0) + Number(form.offset_amount || 0) > Number(form.received_amount || 0)) {
      return '已退加已冲抵不得超过已收押金'
    }
  }
  if (isAction.value) {
    const amount = Number(form.amount || 0)
    if (amount <= 0) return `${actionLabel.value}必须大于 0`
    if (['refund', 'offset'].includes(props.mode) && amount > availableAmount.value) {
      return `${actionLabel.value}不得超过可用押金`
    }
  }
  return ''
})
const canSubmit = computed(() => !validationMessage.value)

function todayValue() {
  const now = new Date()
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset())
  return now.toISOString().slice(0, 10)
}

function resetForm() {
  const deposit = props.deposit || {}
  form.deposit_type = deposit.deposit_type || '租赁押金'
  form.expected_amount = Number(deposit.expected_amount || 0)
  form.received_amount = Number(deposit.received_amount || 0)
  form.refunded_amount = Number(deposit.refunded_amount || 0)
  form.offset_amount = Number(deposit.offset_amount || 0)
  form.due_date = deposit.due_date || ''
  form.received_date = deposit.received_date || ''
  form.refund_date = deposit.refund_date || ''
  form.amount = 0
  form.action_date = todayValue()
  form.notes = isEdit.value ? (deposit.notes || '') : ''
}

function submit() {
  if (!canSubmit.value) return
  if (isAction.value) {
    emit('save', {
      amount: Number(form.amount || 0),
      action_date: form.action_date || null,
      notes: form.notes || ''
    })
    return
  }
  const payload = {
    deposit_type: form.deposit_type,
    expected_amount: Number(form.expected_amount || 0),
    due_date: form.due_date || null,
    notes: form.notes || ''
  }
  if (isEdit.value) {
    payload.received_amount = Number(form.received_amount || 0)
    payload.refunded_amount = Number(form.refunded_amount || 0)
    payload.offset_amount = Number(form.offset_amount || 0)
    payload.received_date = form.received_date || null
    payload.refund_date = form.refund_date || null
  }
  emit('save', payload)
}

watch(
  () => [props.open, props.mode, props.deposit?.id],
  () => {
    if (props.open) resetForm()
  },
  { immediate: true }
)
</script>

<template>
  <BaseModal :open="open" :title="$t(title)" size="small" @close="emit('close')">
    <div class="detail-list">
      <div v-if="contract" class="form-field">
        <span class="section-label">{{ $t("当前合同") }}</span>
        <div>{{ contract.contract_no }} · {{ contract.tenant_name || '--' }}</div>
      </div>

      <div v-if="isAction && deposit" class="stat-grid">
        <div>
          <div class="section-label">{{ $t("押金类型") }}</div>
          <div>{{ $t(deposit.deposit_type || '--') }}</div>
        </div>
        <div>
          <div class="section-label">{{ $t("可用押金") }}</div>
          <div>{{ $t(formatCurrencyFixed(availableAmount)) }}</div>
        </div>
        <div>
          <div class="section-label">{{ $t("已收") }}</div>
          <div>{{ $t(formatCurrencyFixed(deposit.received_amount || 0)) }}</div>
        </div>
        <div>
          <div class="section-label">{{ $t("已退 / 已冲抵") }}</div>
          <div>{{ $t(formatCurrencyFixed(deposit.refunded_amount || 0)) }} / {{ $t(formatCurrencyFixed(deposit.offset_amount || 0)) }}</div>
        </div>
      </div>

      <div v-if="isCreate || isEdit" class="form-grid">
        <label class="form-field">
          <span class="section-label">{{ $t("押金类型") }}</span>
          <select v-model="form.deposit_type" class="select">
            <option v-for="type in depositTypes" :key="type" :value="type">{{ $t(type) }}</option>
          </select>
        </label>
        <label class="form-field">
          <span class="section-label">{{ $t("应收押金") }}</span>
          <input v-model.number="form.expected_amount" type="number" min="0" class="input" />
        </label>
        <label class="form-field">
          <span class="section-label">{{ $t("应收日期") }}</span>
          <DatePickerInput v-model="form.due_date" />
        </label>
        <label v-if="isEdit" class="form-field">
          <span class="section-label">{{ $t("已收金额") }}</span>
          <input v-model.number="form.received_amount" type="number" min="0" class="input" />
        </label>
        <label v-if="isEdit" class="form-field">
          <span class="section-label">{{ $t("收取日期") }}</span>
          <DatePickerInput v-model="form.received_date" />
        </label>
        <label v-if="isEdit" class="form-field">
          <span class="section-label">{{ $t("已退金额") }}</span>
          <input v-model.number="form.refunded_amount" type="number" min="0" class="input" />
        </label>
        <label v-if="isEdit" class="form-field">
          <span class="section-label">{{ $t("退还日期") }}</span>
          <DatePickerInput v-model="form.refund_date" />
        </label>
        <label v-if="isEdit" class="form-field">
          <span class="section-label">{{ $t("已冲抵金额") }}</span>
          <input v-model.number="form.offset_amount" type="number" min="0" class="input" />
        </label>
      </div>

      <div v-if="isAction" class="form-grid single">
        <label class="form-field">
          <span class="section-label">{{ $t(actionLabel) }}</span>
          <input v-model.number="form.amount" type="number" min="0" class="input" />
        </label>
        <label class="form-field">
          <span class="section-label">{{ $t("登记日期") }}</span>
          <DatePickerInput v-model="form.action_date" />
        </label>
      </div>

      <label class="form-field">
        <span class="section-label">{{ $t("备注") }}</span>
        <textarea v-model="form.notes" class="textarea" rows="3" />
      </label>
      <div v-if="deposit && (isEdit || isAction)" class="helper-text">{{ $t(" 当前状态 ") }}{{ $t(deposit.status || '--') }}{{ $t("，最近收取 ") }}{{ $t(formatDate(deposit.received_date)) }}{{ $t("，最近退还 ") }}{{ $t(formatDate(deposit.refund_date)) }}
      </div>
      <div v-if="validationMessage" class="helper-text danger">{{ $t(validationMessage) }}</div>
    </div>

    <template #footer>
      <button class="btn" @click="emit('close')">{{ $t("取消") }}</button>
      <button class="btn btn-primary" :disabled="!canSubmit" @click="submit">{{ $t("保存") }}</button>
    </template>
  </BaseModal>
</template>
