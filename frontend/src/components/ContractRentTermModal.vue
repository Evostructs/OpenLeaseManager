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
  term: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['close', 'save'])
const ruleTypes = [
  { value: 'area_unit_price', label: '按面积单价' },
  { value: 'fixed_monthly', label: '固定月租' },
  { value: 'stepped_rent', label: '阶梯租金' },
  { value: 'escalation', label: '递增租金' },
  { value: 'rent_free', label: '自然日免租' },
  { value: 'temporary_adjustment', label: '临时减免' }
]
const form = reactive({
  rule_type: 'area_unit_price',
  room_scope: '',
  amount_mode: 'area_unit_price',
  start_date: '',
  end_date: '',
  area: null,
  unit_price: null,
  monthly_amount: null,
  adjustment_type: 'percent',
  adjustment_value: null,
  frequency: 'once',
  notes: ''
})

const title = computed(() => (props.term?.id ? '编辑租金条款' : '新增租金条款'))
const roomOptions = computed(() => (props.contract?.room_items || []).filter((item) => item.id))
const usesAreaPrice = computed(() => form.rule_type === 'area_unit_price' || (form.rule_type === 'stepped_rent' && form.amount_mode === 'area_unit_price'))
const usesMonthlyAmount = computed(() => form.rule_type === 'fixed_monthly' || (form.rule_type === 'stepped_rent' && form.amount_mode === 'fixed_monthly'))
const usesAdjustmentValue = computed(() => form.rule_type === 'escalation' || form.rule_type === 'temporary_adjustment')
const validAdjustmentTypes = ['percent', 'fixed_amount']
const validEscalationFrequencies = ['once', 'annual']
const selectedBaseMonthly = computed(() => {
  const room = selectedRoom()
  return Number(room?.monthly_rent || props.contract?.monthly_rent || 0)
})
const adjustmentValueLabel = computed(() => {
  if (form.rule_type === 'temporary_adjustment') {
    return form.adjustment_type === 'percent' ? '抵减百分比' : '月抵减金额'
  }
  return form.adjustment_type === 'percent' ? '递增百分比' : '递增月金额'
})
const adjustmentNeedsAttention = computed(() => {
  if (form.rule_type !== 'temporary_adjustment') return false
  const value = Number(form.adjustment_value || 0)
  if (!value) return false
  if (form.adjustment_type === 'percent') return value >= 50
  const base = selectedBaseMonthly.value
  return base > 0 ? value >= base * 0.5 : value >= 50000
})
function numberMissing(value) {
  return value === null || value === undefined || value === '' || !Number.isFinite(Number(value))
}

const formError = computed(() => {
  if (!form.rule_type) return '请选择条款类型'
  if (form.room_scope && !selectedRoom()) return '请选择有效作用房源'
  if (!form.start_date || !form.end_date) return '请填写起止日期'
  if (form.end_date < form.start_date) return '终止日不能早于起始日'
  if (
    usesAreaPrice.value
    && (
      numberMissing(form.area)
      || Number(form.area) <= 0
      || numberMissing(form.unit_price)
      || Number(form.unit_price) < 0
    )
  ) return '请填写有效面积和非负日租单价'
  if (
    usesMonthlyAmount.value
    && (numberMissing(form.monthly_amount) || Number(form.monthly_amount) < 0)
  ) return '请填写非负固定月租金'
  if (usesAdjustmentValue.value && !validAdjustmentTypes.includes(form.adjustment_type)) return '请选择有效调整方式'
  if (usesAdjustmentValue.value && form.adjustment_type === 'percent' && Number(form.adjustment_value || 0) > 100) return '比例不能超过 100%'
  if (form.rule_type === 'escalation' && !validEscalationFrequencies.includes(form.frequency)) return '请选择有效递增频率'
  if (form.rule_type === 'escalation' && !Number(form.adjustment_value || 0)) return '请填写有效递增值'
  if (form.rule_type === 'temporary_adjustment' && !Number(form.adjustment_value || 0)) return '请填写有效抵减值'
  return ''
})
const canSubmit = computed(() => !formError.value)

function selectedRoom() {
  return roomOptions.value.find((item) => String(item.id) === String(form.room_scope)) || null
}

function resetForm() {
  const term = props.term || {}
  form.rule_type = term.rule_type || 'area_unit_price'
  form.room_scope = term.contract_room_id ? String(term.contract_room_id) : ''
  form.amount_mode = term.rule_type === 'stepped_rent' && term.monthly_amount != null ? 'fixed_monthly' : 'area_unit_price'
  form.start_date = term.start_date || props.contract?.start_date || ''
  form.end_date = term.end_date || props.contract?.end_date || ''
  form.area = term.area ?? null
  form.unit_price = term.unit_price ?? null
  form.monthly_amount = term.monthly_amount ?? null
  form.adjustment_type = term.adjustment_type || 'percent'
  form.adjustment_value = term.adjustment_value ?? null
  form.frequency = term.frequency || 'once'
  form.notes = term.notes || ''
}

function applyRoomDefaults() {
  const room = selectedRoom()
  if (!room) return
  if (!form.area) form.area = Number(room.area || 0)
  if (!form.unit_price) form.unit_price = Number(room.unit_price || 0)
  if (!form.start_date) form.start_date = room.start_date || props.contract?.start_date || ''
  if (!form.end_date) form.end_date = room.end_date || props.contract?.end_date || ''
}

function applyRuleTypeDefaults() {
  if (form.rule_type === 'temporary_adjustment') {
    form.adjustment_type = 'fixed_amount'
  } else if (form.rule_type === 'escalation') {
    form.adjustment_type = 'percent'
  }
}

function submit() {
  if (!canSubmit.value) return
  const room = selectedRoom()
  emit('save', {
    contract_room_id: room ? room.id : null,
    room_no: room ? room.room_no : '',
    rule_type: form.rule_type,
    start_date: form.start_date,
    end_date: form.end_date,
    area: usesAreaPrice.value ? Number(form.area || 0) : null,
    unit_price: usesAreaPrice.value ? Number(form.unit_price || 0) : null,
    monthly_amount: usesMonthlyAmount.value ? Number(form.monthly_amount || 0) : null,
    adjustment_type: usesAdjustmentValue.value ? form.adjustment_type : null,
    adjustment_value: usesAdjustmentValue.value ? Number(form.adjustment_value || 0) : null,
    frequency: form.rule_type === 'escalation' ? form.frequency : null,
    notes: form.notes || ''
  })
}

watch(
  () => [props.open, props.term?.id, props.contract?.id],
  () => {
    if (props.open) resetForm()
  },
  { immediate: true }
)
</script>

<template>
  <BaseModal :open="open" :title="$t(title)" size="small" @close="emit('close')">
    <div class="form-grid single">
      <label class="form-field">
        <span class="section-label">{{ $t("条款类型") }}</span>
        <select v-model="form.rule_type" class="select" @change="applyRuleTypeDefaults">
          <option v-for="item in ruleTypes" :key="item.value" :value="item.value">{{ $t(item.label) }}</option>
        </select>
      </label>
      <label class="form-field">
        <span class="section-label">{{ $t("作用范围") }}</span>
        <select v-model="form.room_scope" class="select" @change="applyRoomDefaults">
          <option value="">{{ $t("合同整体（合同级）") }}</option>
          <option v-for="room in roomOptions" :key="room.id" :value="String(room.id)">{{ $t(" 房源级 ") }}{{ room.room_no }} · {{ $t(room.start_date || contract?.start_date || '--') }} - {{ $t(room.end_date || contract?.end_date || '--') }}
          </option>
        </select>
      </label>
      <label v-if="form.rule_type === 'stepped_rent'" class="form-field">
        <span class="section-label">{{ $t("分段金额方式") }}</span>
        <select v-model="form.amount_mode" class="select" @change="applyRoomDefaults">
          <option value="area_unit_price">{{ $t("面积单价") }}</option>
          <option value="fixed_monthly">{{ $t("固定月租") }}</option>
        </select>
      </label>
      <div class="form-field">
        <span class="section-label">{{ $t("起始日") }}</span>
        <DatePickerInput v-model="form.start_date" />
      </div>
      <div class="form-field">
        <span class="section-label">{{ $t("终止日") }}</span>
        <DatePickerInput v-model="form.end_date" />
      </div>
      <label v-if="usesAreaPrice" class="form-field">
        <span class="section-label">{{ $t("面积") }}</span>
        <input v-model.number="form.area" type="number" class="input" />
      </label>
      <label v-if="usesAreaPrice" class="form-field">
        <span class="section-label">{{ $t("日租单价") }}</span>
        <input v-model.number="form.unit_price" type="number" class="input" />
      </label>
      <label v-if="usesMonthlyAmount" class="form-field">
        <span class="section-label">{{ $t("固定月租金") }}</span>
        <input v-model.number="form.monthly_amount" type="number" class="input" />
      </label>
      <label v-if="form.rule_type === 'escalation'" class="form-field">
        <span class="section-label">{{ $t("递增方式") }}</span>
        <select v-model="form.adjustment_type" class="select">
          <option value="percent">{{ $t("百分比") }}</option>
          <option value="fixed_amount">{{ $t("固定金额") }}</option>
        </select>
      </label>
      <label v-if="form.rule_type === 'temporary_adjustment'" class="form-field">
        <span class="section-label">{{ $t("抵减方式") }}</span>
        <select v-model="form.adjustment_type" class="select">
          <option value="fixed_amount">{{ $t("固定金额") }}</option>
          <option value="percent">{{ $t("百分比") }}</option>
        </select>
      </label>
      <label v-if="form.rule_type === 'escalation'" class="form-field">
        <span class="section-label">{{ $t("递增频率") }}</span>
        <select v-model="form.frequency" class="select">
          <option value="once">{{ $t("指定日期生效") }}</option>
          <option value="annual">{{ $t("年度递增") }}</option>
        </select>
      </label>
      <label v-if="usesAdjustmentValue" class="form-field">
        <span class="section-label">{{ $t(adjustmentValueLabel) }}</span>
        <input v-model.number="form.adjustment_value" type="number" class="input" />
      </label>
      <div v-if="adjustmentNeedsAttention" class="helper-text warning">{{ $t("抵减幅度较大，请核对金额。") }}</div>
      <label class="form-field">
        <span class="section-label">{{ $t("备注") }}</span>
        <textarea v-model="form.notes" class="textarea" rows="3" />
      </label>
      <div v-if="formError" class="helper-text danger">{{ $t(formError) }}</div>
    </div>

    <template #footer>
      <button class="btn" @click="emit('close')">{{ $t("取消") }}</button>
      <button class="btn btn-primary" :disabled="!canSubmit" @click="submit">{{ $t("保存") }}</button>
    </template>
  </BaseModal>
</template>
