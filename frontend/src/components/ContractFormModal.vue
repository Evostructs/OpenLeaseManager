<script setup>
import { t } from '../i18n/index.js'
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { api } from '../lib/api'
import { profileSpaceOptions } from '../lib/roomsDomain'
import BaseModal from './BaseModal.vue'
import DatePickerInput from './DatePickerInput.vue'
import InfoHint from './InfoHint.vue'

const DEFAULT_BROKER_FEE_MONTHS = 2.5
const signingTypeOptions = [
  { value: '新签', label: '新签（首次签约）' },
  { value: '续签', label: '续签（原合同延续）' },
  { value: '换签', label: '换签（主体变更）' },
  { value: '变更签', label: '变更签（改价/改期）' },
  { value: '扩租', label: '扩租（增加面积）' },
  { value: '减租', label: '减租（减少面积）' }
]

const blank = () => ({
  id: null,
  contract_no: '',
  customer_id: null,
  tower: '',
  room_no: '',
  room_items: [],
  tenant_name: '',
  area: 0,
  unit_price: 0,
  lease_type: '新签',
  free_period: 0,
  monthly_rent: 0,
  broker_fee: 0,
  start_date: '',
  end_date: '',
  disposal: '',
  contact_person: '',
  contact_phone: '',
  contact_email: '',
  internal_contact_person: '',
  internal_contact_feishu_id: '',
  deposit: 0,
  deposit_status: '',
  property_fee_rate: 0,
  prev_contract_no: '',
  payment_due_day: null,
  notes: '',
  last_updated: ''
})

const props = defineProps({
  open: Boolean,
  contract: {
    type: Object,
    default: null
  },
  canEdit: Boolean,
  saving: Boolean,
  operationsProfile: {
    type: Object,
    default: () => ({})
  }
})

const emit = defineEmits(['close', 'save'])
const form = reactive(blank())
const brokerFeeManual = ref(false)
const initialFormSnapshot = ref('')
const contactPhoneWarn = ref('')
const contactEmailWarn = ref('')
const availableRooms = ref([])
const roomsLoading = ref(false)
const roomAvailabilityError = ref('')
const customerOptions = ref([])
const customersLoading = ref(false)
let availabilityRequestId = 0

const configuredSpaceOptions = computed(() => profileSpaceOptions(props.operationsProfile))
const defaultSpaceValue = computed(() => (
  configuredSpaceOptions.value.length === 1 ? configuredSpaceOptions.value[0].value : ''
))
const contractSpaceOptions = computed(() => {
  const options = [...configuredSpaceOptions.value]
  const known = new Set(options.map((item) => item.value))
  const values = [
    form.tower,
    ...form.room_items.map((item) => item.tower),
    ...availableRooms.value.map((item) => item.tower)
  ]
  for (const rawValue of values) {
    const value = String(rawValue || '').trim()
    if (!value || known.has(value) || value === '多房源') continue
    known.add(value)
    options.push({ code: value, value, label: value, kind: '' })
  }
  return options
})

const leaseWindowReady = computed(() => Boolean(form.start_date || form.end_date))
const roomOptions = computed(() => availableRooms.value.filter((room) => room.is_leasable !== false))
const roomAvailabilityByNo = computed(() => {
  const byNo = new Map()
  for (const room of availableRooms.value) {
    if (room.room_no) byNo.set(String(room.room_no).trim(), room)
  }
  return byNo
})
const roomConflictItems = computed(() => form.room_items
  .map((item, index) => ({ item, index, room: selectedRoomForItem(item) }))
  .filter(({ item, room }) => {
    const roomNo = String(item.room_no || '').trim()
    const hasWindow = leaseWindowReady.value || item.start_date || item.end_date
    return hasWindow && roomNo && room?.occupied && roomContractOverlapsItem(item, room.contract)
  }))
const roomDateErrorItems = computed(() => form.room_items
  .map((item, index) => ({ item, index, message: roomDateIssue(item) }))
  .filter((entry) => entry.message))
const canSubmit = computed(() => props.canEdit && roomConflictItems.value.length === 0 && roomDateErrorItems.value.length === 0)
const selectedCustomer = computed(() => customerOptions.value.find((customer) => Number(customer.id) === Number(form.customer_id)) || null)
const selectedRoomItems = computed(() => form.room_items.filter((item) => String(item.room_no || '').trim()))
const summaryCustomerName = computed(() => selectedCustomer.value?.name || form.tenant_name || '未选择')
const summaryRoomCount = computed(() => selectedRoomItems.value.length ? `${selectedRoomItems.value.length} 间` : '未选择')
const summaryAreaText = computed(() => `${formatAmount(form.area)}㎡`)
const summaryRentText = computed(() => `¥${formatAmount(form.monthly_rent)}`)
const summaryLeaseWindowText = computed(() => {
  if (form.start_date && form.end_date) return `${form.start_date} 至 ${form.end_date}`
  return form.start_date || form.end_date || '未填写'
})
const summaryConflictText = computed(() => {
  if (roomConflictItems.value.length) return `${roomConflictItems.value.length} 项冲突`
  if (roomDateErrorItems.value.length) return `${roomDateErrorItems.value.length} 项日期`
  if (roomsLoading.value) return '检查中'
  if (roomAvailabilityError.value) return '待复核'
  if (!selectedRoomItems.value.length) return '待选房'
  return '可保存'
})
const summaryConflictTone = computed(() => ({
  danger: roomConflictItems.value.length > 0 || roomDateErrorItems.value.length > 0,
  warning: !roomConflictItems.value.length && !roomDateErrorItems.value.length && (roomsLoading.value || roomAvailabilityError.value || !selectedRoomItems.value.length),
  success: !roomConflictItems.value.length && !roomDateErrorItems.value.length && !roomsLoading.value && !roomAvailabilityError.value && selectedRoomItems.value.length > 0
}))
const availabilityStatusText = computed(() => {
  if (roomsLoading.value) return '房态读取中'
  if (roomAvailabilityError.value) return roomAvailabilityError.value
  if (!leaseWindowReady.value) return '房态：当前'
  return `房态：${form.start_date || form.end_date} 至 ${form.end_date || form.start_date}`
})

function formatAmount(value) {
  return (Number(value) || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2
  })
}

function roundCurrency(value) {
  return Math.round((Number(value) || 0) * 100) / 100
}

function calculateMonthlyRent(area, unitPrice) {
  const areaValue = Number(area) || 0
  const unitPriceValue = Number(unitPrice) || 0
  return roundCurrency(areaValue * unitPriceValue * 365 / 12)
}

function calculateBrokerFee(monthlyRent) {
  const monthlyRentValue = Number(monthlyRent) || 0
  return roundCurrency(monthlyRentValue * DEFAULT_BROKER_FEE_MONTHS)
}

function blankRoomItem(overrides = {}) {
  const inheritedTower = configuredSpaceOptions.value.some((item) => item.value === form.tower) ? form.tower : ''
  return {
    tower: overrides.tower || inheritedTower || defaultSpaceValue.value,
    room_no: overrides.room_no || '',
    area: Number(overrides.area || 0),
    unit_price: Number(overrides.unit_price || 0),
    monthly_rent: Number(overrides.monthly_rent || 0),
    start_date: overrides.start_date || form.start_date || '',
    end_date: overrides.end_date || form.end_date || '',
    notes: overrides.notes || ''
  }
}

function normalizeRoomItems(value) {
  const rawItems = Array.isArray(value?.room_items) ? value.room_items : []
  if (rawItems.length) {
    form.room_items = rawItems.map((item) => blankRoomItem(item))
    return
  }

  form.room_items = [
    blankRoomItem({
      tower: value?.tower || '',
      room_no: value?.room_no || '',
      area: value?.area || 0,
      unit_price: value?.unit_price || 0,
      monthly_rent: value?.monthly_rent || 0
    })
  ]
}

function syncRoomTotals() {
  const activeItems = form.room_items.filter((item) => String(item.room_no || '').trim())
  if (!activeItems.length) {
    form.area = 0
    form.unit_price = 0
    form.monthly_rent = 0
    return
  }

  let totalArea = 0
  let totalMonthlyRent = 0
  const towers = new Set()
  for (const item of activeItems) {
    item.area = Number(item.area || 0)
    item.unit_price = Number(item.unit_price || 0)
    const itemRent = calculateMonthlyRent(item.area, item.unit_price)
    if (item.monthly_rent !== itemRent) item.monthly_rent = itemRent
    totalArea += item.area
    totalMonthlyRent += itemRent
    if (item.tower) towers.add(item.tower)
  }

  form.area = roundCurrency(totalArea)
  form.monthly_rent = roundCurrency(totalMonthlyRent)
  form.unit_price = form.area > 0 ? roundCurrency(form.monthly_rent * 12 / 365 / form.area) : 0
  form.room_no = activeItems.map((item) => item.room_no).join('、')
  form.tower = towers.size === 1 ? [...towers][0] : (towers.size > 1 ? '多房源' : form.tower)
  if (!brokerFeeManual.value) {
    form.broker_fee = calculateBrokerFee(form.monthly_rent)
  }
}

function addRoomItem() {
  form.room_items.push(blankRoomItem())
}

function removeRoomItem(index) {
  if (form.room_items.length <= 1) return
  form.room_items.splice(index, 1)
  syncRoomTotals()
}

function selectedRoomForItem(item) {
  const roomNo = String(item.room_no || '').trim()
  return roomNo ? roomAvailabilityByNo.value.get(roomNo) || null : null
}

function roomContractLabel(room) {
  const contract = room?.contract || {}
  return contract.tenant_name || contract.contract_no || '其他合同'
}

function effectiveItemStart(item) {
  return item.start_date || form.start_date || ''
}

function effectiveItemEnd(item) {
  return item.end_date || form.end_date || ''
}

function periodsOverlap(firstStart, firstEnd, secondStart, secondEnd) {
  if (!firstStart || !firstEnd || !secondStart || !secondEnd) return false
  return firstStart <= secondEnd && firstEnd >= secondStart
}

function roomContractOverlapsItem(item, contract) {
  if (!contract) return true
  const itemStart = effectiveItemStart(item)
  const itemEnd = effectiveItemEnd(item)
  if (!itemStart || !itemEnd) return true
  return periodsOverlap(itemStart, itemEnd, contract.start_date, contract.end_date)
}

function roomDateIssue(item) {
  const start = effectiveItemStart(item)
  const end = effectiveItemEnd(item)
  if (start && end && end < start) return '终止日不能早于起租日'
  if (item.start_date && form.start_date && item.start_date < form.start_date) return '起租日不能早于合同起租日'
  if (item.end_date && form.end_date && item.end_date > form.end_date) return '终止日不能晚于合同终止日'
  return ''
}

function roomOptionLabel(room) {
  const area = Number(room.area || 0)
  const status = room.occupied
    ? `${leaseWindowReady.value ? '已占用' : '当前占用'}：${roomContractLabel(room)}`
    : '可租'
  return `${room.tower || '未标注'} · ${area}㎡ · ${status}`
}

function roomAvailabilityText(item) {
  const roomNo = String(item.room_no || '').trim()
  if (!roomNo) return ''
  const room = selectedRoomForItem(item)
  if (!room) return '未匹配房间清单'
  if (room.occupied && roomContractOverlapsItem(item, room.contract)) {
    return `已占用：${roomContractLabel(room)}`
  }
  const area = Number(room.area || 0)
  return `${leaseWindowReady.value ? '可租' : '清单'} · ${room.tower || '未标注'} · ${area}㎡`
}

function roomAvailabilityClass(item) {
  const room = selectedRoomForItem(item)
  return {
    success: leaseWindowReady.value && room && (!room.occupied || !roomContractOverlapsItem(item, room.contract)),
    danger: leaseWindowReady.value && room?.occupied && roomContractOverlapsItem(item, room.contract),
    muted: !leaseWindowReady.value || !room,
  }
}

function applyRoomSelection(index) {
  const item = form.room_items[index]
  if (!item) return
  const room = selectedRoomForItem(item)
  if (!room) {
    syncRoomTotals()
    return
  }
  item.room_no = room.room_no
  item.tower = room.tower || item.tower
  item.area = Number(room.area || 0)
  syncRoomTotals()
}

function applyCustomerSelection() {
  const customer = selectedCustomer.value
  if (!customer) return
  form.tenant_name = customer.name || form.tenant_name
  if (!form.contact_person) form.contact_person = customer.contact_name || ''
  if (!form.contact_phone) form.contact_phone = customer.contact_phone || ''
  if (!form.contact_email) form.contact_email = customer.contact_email || ''
}

async function fetchAvailableRooms() {
  if (!props.open) return
  const requestId = ++availabilityRequestId
  roomsLoading.value = true
  roomAvailabilityError.value = ''

  const params = new URLSearchParams()
  if (form.start_date) params.set('start_date', form.start_date)
  if (form.end_date) params.set('end_date', form.end_date)
  if (form.id) params.set('exclude_contract_id', String(form.id))
  const suffix = params.toString()

  try {
    const rows = await api(`/api/rooms${suffix ? `?${suffix}` : ''}`)
    if (requestId === availabilityRequestId) {
      availableRooms.value = Array.isArray(rows) ? rows : []
    }
  } catch (error) {
    if (requestId === availabilityRequestId) {
      availableRooms.value = []
      roomAvailabilityError.value = error.message || '房态读取失败'
    }
  } finally {
    if (requestId === availabilityRequestId) {
      roomsLoading.value = false
    }
  }
}

async function fetchCustomerOptions() {
  if (!props.open) return
  customersLoading.value = true
  try {
    const data = await api('/api/customers?page=1&page_size=100')
    customerOptions.value = data.items || []
  } catch {
    customerOptions.value = []
  } finally {
    customersLoading.value = false
  }
}

watch(
  () => props.contract,
  (value) => {
    Object.assign(form, blank(), value || {})
    form.last_updated = value?.updated_at || ''
    brokerFeeManual.value = Boolean(value?.id || Number(value?.broker_fee || 0) > 0)
    normalizeRoomItems(value || {})
    syncRoomTotals()
  },
  { immediate: true }
)

watch(
  () => props.open,
  () => {
    if (props.open) fetchCustomerOptions()
  },
  { immediate: true }
)

// 打开弹窗后记录初始快照，用于关闭时的脏数据确认
watch(
  () => [props.open, props.contract],
  () => {
    if (!props.open) return
    contactPhoneWarn.value = ''
    contactEmailWarn.value = ''
    nextTick(() => {
      initialFormSnapshot.value = JSON.stringify(form)
    })
  },
  { immediate: true }
)

watch(
  () => [props.open, form.start_date, form.end_date, form.id],
  () => {
    if (props.open) fetchAvailableRooms()
  },
  { immediate: true }
)

watch(
  () => form.room_items,
  () => syncRoomTotals(),
  { deep: true }
)

function markBrokerFeeManual() {
  brokerFeeManual.value = true
}

function phoneFormatWarnText(value) {
  const raw = String(value || '').trim()
  if (!raw) return ''
  const digits = raw.replace(/[+\-\s()（）]/g, '')
  if (/^\d{6,20}$/.test(digits)) return ''
  return '电话格式可能有误：建议 6-20 位数字，可含 +、-、空格、括号'
}

function emailFormatWarnText(value) {
  const raw = String(value || '').trim()
  if (!raw) return ''
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(raw)) return ''
  return '邮箱格式可能有误：应类似 name@example.com'
}

function checkContactPhone() {
  contactPhoneWarn.value = phoneFormatWarnText(form.contact_phone)
}

function checkContactEmail() {
  contactEmailWarn.value = emailFormatWarnText(form.contact_email)
}

function isFormDirty() {
  return JSON.stringify(form) !== initialFormSnapshot.value
}

function requestClose() {
  if (props.saving) return
  if (isFormDirty() && !window.confirm(t('当前合同表单有未保存的修改，确定关闭？'))) return
  emit('close')
}

// v-model.number 在输入框被清空时会留下 ''，直接提交会触发后端 422
function toNumberOrZero(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function submit() {
  if (!canSubmit.value || props.saving) return
  emit('save', {
    ...form,
    customer_id: form.customer_id || null,
    area: toNumberOrZero(form.area),
    unit_price: toNumberOrZero(form.unit_price),
    monthly_rent: toNumberOrZero(form.monthly_rent),
    free_period: toNumberOrZero(form.free_period),
    payment_due_day: (() => {
      const day = Number(form.payment_due_day)
      return Number.isInteger(day) && day >= 1 && day <= 31 ? day : null
    })(),
    broker_fee: toNumberOrZero(form.broker_fee),
    deposit: toNumberOrZero(form.deposit),
    property_fee_rate: toNumberOrZero(form.property_fee_rate),
    start_date: form.start_date || null,
    end_date: form.end_date || null,
    room_items: form.room_items.map((item) => ({
      ...item,
      area: toNumberOrZero(item.area),
      unit_price: toNumberOrZero(item.unit_price),
      monthly_rent: toNumberOrZero(item.monthly_rent),
      start_date: item.start_date || null,
      end_date: item.end_date || null
    }))
  })
}
</script>

<template>
  <BaseModal :open="open" :title="form.id ? '编辑合同' : '新建合同'" @close="requestClose">
    <div class="contract-form-shell" data-testid="contract-form-shell">
      <section class="contract-form-summary" data-testid="contract-form-summary">
        <div class="contract-form-summary-item">
          <span>{{ $t("客户") }}</span>
          <strong :title="$t(summaryCustomerName)">{{ $t(summaryCustomerName) }}</strong>
        </div>
        <div class="contract-form-summary-item">
          <span>{{ $t("房源") }}</span>
          <strong>{{ $t(summaryRoomCount) }}</strong>
        </div>
        <div class="contract-form-summary-item">
          <span>{{ $t("面积") }}</span>
          <strong>{{ $t(summaryAreaText) }}</strong>
        </div>
        <div class="contract-form-summary-item">
          <span>{{ $t("月租") }}</span>
          <strong>{{ $t(summaryRentText) }}</strong>
        </div>
        <div class="contract-form-summary-item wide">
          <span>{{ $t("租期") }}</span>
          <strong :title="$t(summaryLeaseWindowText)">{{ $t(summaryLeaseWindowText) }}</strong>
        </div>
        <div class="contract-form-summary-item status" :class="summaryConflictTone" data-testid="contract-form-conflict-status">
          <span>{{ $t("房态") }}</span>
          <strong>{{ $t(summaryConflictText) }}</strong>
        </div>
      </section>

      <section class="contract-form-section" data-testid="contract-form-section-object">
        <div class="contract-form-section-heading">
          <h4>{{ $t("合同对象") }}</h4>
        </div>
        <div class="contract-form-grid">
          <label class="form-field">
            <span class="section-label">{{ $t("合同编号") }}</span>
            <input v-model="form.contract_no" class="input" />
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("客户资料") }}</span>
            <select v-model="form.customer_id" class="select" @change="applyCustomerSelection">
              <option :value="null">{{ $t(customersLoading ? '客户读取中' : '不关联客户资料') }}</option>
              <option v-for="customer in customerOptions" :key="customer.id" :value="customer.id">
                {{ customer.name }}{{ $t(customer.short_name ? ` / ${customer.short_name}` : '') }}
              </option>
            </select>
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("客户名称") }}</span>
            <input v-model="form.tenant_name" class="input" />
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("签约类型") }}</span>
            <select v-model="form.lease_type" class="select">
              <option v-for="option in signingTypeOptions" :key="option.value" :value="option.value">{{ $t(option.label) }}</option>
            </select>
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("到期处置") }}</span>
            <select v-model="form.disposal" class="select">
              <option value="">{{ $t("未确定") }}</option>
              <option value="已续租">{{ $t("已续租") }}</option>
              <option value="已变更">{{ $t("已变更") }}</option>
              <option value="已退租">{{ $t("已退租") }}</option>
              <option value="逾期占用">{{ $t("逾期占用") }}</option>
            </select>
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("关联合同编号") }}</span>
            <input v-model="form.prev_contract_no" class="input" />
          </label>
        </div>
      </section>

      <section class="contract-form-section" data-testid="contract-form-section-rooms">
        <div class="contract-form-section-heading">
          <div>
            <h4>{{ $t("房源明细") }}</h4>
            <span class="contract-form-heading-meta" :class="{ error: roomAvailabilityError }">{{ $t(availabilityStatusText) }}</span>
          </div>
          <button type="button" class="btn btn-compact" @click="addRoomItem">{{ $t("新增房源") }}</button>
        </div>
        <div class="contract-room-items">
          <div class="contract-room-row contract-room-row-head">
            <span>{{ $t("塔楼") }}</span>
            <span>{{ $t("室号") }}</span>
            <span>{{ $t("起租日") }}</span>
            <span>{{ $t("终止日") }}</span>
            <span>{{ $t("面积") }}</span>
            <span>{{ $t("日租单价") }}</span>
            <span>{{ $t("月租金") }}</span>
            <span>{{ $t("操作") }}</span>
          </div>
          <div v-for="(item, index) in form.room_items" :key="index" class="contract-room-row">
            <select v-model="item.tower" class="select">
              <option value="">{{ $t("选择分区") }}</option>
              <option v-for="option in contractSpaceOptions" :key="option.code" :value="option.value">{{ $t(option.label) }}</option>
            </select>
            <div class="contract-room-selector">
              <input
                v-model="item.room_no"
                class="input"
                list="contract-room-options"
                @change="applyRoomSelection(index)"
              />
              <small
                v-if="roomAvailabilityText(item)"
                class="room-availability-note"
                :class="roomAvailabilityClass(item)"
                :title="$t(roomAvailabilityText(item))"
              >
                {{ $t(roomAvailabilityText(item)) }}
              </small>
            </div>
            <DatePickerInput v-model="item.start_date" :placeholder="$t(&quot;继承合同&quot;)" />
            <DatePickerInput v-model="item.end_date" :placeholder="$t(&quot;继承合同&quot;)" />
            <input v-model.number="item.area" type="number" class="input" />
            <input v-model.number="item.unit_price" type="number" class="input" />
            <input :value="item.monthly_rent" type="number" class="input" readonly />
            <button type="button" class="btn btn-danger btn-compact" :disabled="form.room_items.length <= 1" @click="removeRoomItem(index)">{{ $t("删除") }}</button>
          </div>
          <datalist id="contract-room-options">
            <option v-for="room in roomOptions" :key="room.id" :value="room.room_no" :label="$t(roomOptionLabel(room))" />
          </datalist>
        </div>
        <div v-if="roomConflictItems.length" class="contract-room-conflicts">
          <div v-for="conflict in roomConflictItems" :key="`${conflict.index}-${conflict.item.room_no}`" class="contract-room-conflict">
            {{ conflict.item.room_no }}{{ $t(" 已被 ") }}{{ $t(roomContractLabel(conflict.room)) }}{{ $t(" 占用 ") }}</div>
        </div>
        <div v-if="roomDateErrorItems.length" class="contract-room-conflicts">
          <div v-for="error in roomDateErrorItems" :key="`date-${error.index}`" class="contract-room-conflict">
            {{ error.item.room_no || `第 ${error.index + 1} 行` }}：{{ $t(error.message) }}
          </div>
        </div>
      </section>

      <section class="contract-form-section" data-testid="contract-form-section-rent">
        <div class="contract-form-section-heading">
          <h4>{{ $t("租期租金") }}</h4>
          <InfoHint :text="$t(&quot;月租由房源面积和日租单价汇总；中介佣金可按实际约定调整。&quot;)" />
        </div>
        <div class="contract-form-grid">
          <div class="form-field">
            <span class="section-label">{{ $t("起租日") }}</span>
            <DatePickerInput v-model="form.start_date" />
          </div>
          <div class="form-field">
            <span class="section-label">{{ $t("终止日") }}</span>
            <DatePickerInput v-model="form.end_date" />
          </div>
          <label class="form-field">
            <span class="section-label">{{ $t("合计面积") }}</span>
            <input v-model.number="form.area" type="number" class="input" readonly />
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("平均日租") }}</span>
            <input v-model.number="form.unit_price" type="number" class="input" readonly />
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("月租金") }}</span>
            <input v-model.number="form.monthly_rent" type="number" class="input" readonly />
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("免租期（月）") }}</span>
            <input v-model.number="form.free_period" type="number" class="input" :title="$t(&quot;自起租日起连续免租 N 个自然月，如 1/25 起租免 1 个月即免至 2/24&quot;)" />
            <span class="form-field-hint">{{ $t("自起租日起连续免租，如 1/25 起免 1 个月即免至 2/24") }}</span>
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("中介佣金") }}</span>
            <input v-model.number="form.broker_fee" type="number" class="input" @input="markBrokerFeeManual" />
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("付款日（每月）") }}</span>
            <input v-model.number="form.payment_due_day" type="number" min="1" max="31" class="input" :placeholder="$t(&quot;留空=月末&quot;)" />
            <span class="form-field-hint">{{ $t("租金每月到期日（1-31），留空按月末到期") }}</span>
          </label>
        </div>
      </section>

      <section class="contract-form-section" data-testid="contract-form-section-deposit">
        <div class="contract-form-section-heading">
          <h4>{{ $t("押金物业") }}</h4>
        </div>
        <div class="contract-form-grid compact">
          <label class="form-field">
            <span class="section-label">{{ $t("押金") }}</span>
            <input v-model.number="form.deposit" type="number" class="input" />
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("押金状态") }}</span>
            <select v-model="form.deposit_status" class="select">
              <option value="">{{ $t("未标注") }}</option>
              <option value="已收">{{ $t("已收") }}</option>
              <option value="已退">{{ $t("已退") }}</option>
              <option value="部分退还">{{ $t("部分退还") }}</option>
              <option value="未收">{{ $t("未收") }}</option>
              <option value="待收">{{ $t("待收") }}</option>
            </select>
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("物业费单价") }}</span>
            <input v-model.number="form.property_fee_rate" type="number" class="input" />
          </label>
        </div>
      </section>

      <section class="contract-form-section" data-testid="contract-form-section-contact">
        <div class="contract-form-section-heading">
          <h4>{{ $t("联系人") }}</h4>
        </div>
        <div class="contract-form-grid">
          <label class="form-field">
            <span class="section-label">{{ $t("客户方对接人") }}</span>
            <input v-model="form.contact_person" class="input" />
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("联系电话") }}</span>
            <input v-model="form.contact_phone" type="tel" maxlength="30" class="input" @blur="checkContactPhone" @input="contactPhoneWarn = ''" />
            <span v-if="contactPhoneWarn" class="form-field-warn">{{ $t(contactPhoneWarn) }}</span>
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("邮箱") }}</span>
            <input v-model="form.contact_email" type="email" maxlength="100" class="input" @blur="checkContactEmail" @input="contactEmailWarn = ''" />
            <span v-if="contactEmailWarn" class="form-field-warn">{{ $t(contactEmailWarn) }}</span>
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("我方对接人") }}</span>
            <input v-model="form.internal_contact_person" class="input" />
          </label>
          <label class="form-field">
            <span class="section-label">{{ $t("我方飞书ID") }}</span>
            <input v-model="form.internal_contact_feishu_id" class="input" />
          </label>
        </div>
      </section>

      <section class="contract-form-section" data-testid="contract-form-section-notes">
        <div class="contract-form-section-heading">
          <h4>{{ $t("备注") }}</h4>
        </div>
        <label class="form-field">
          <span class="section-label">{{ $t("内部备注") }}</span>
          <textarea v-model="form.notes" class="textarea" />
        </label>
      </section>
    </div>

    <template #footer>
      <button class="btn" :disabled="saving" @click="requestClose">{{ $t("取消") }}</button>
      <button class="btn btn-primary" data-testid="contract-form-save" :disabled="!canSubmit || saving" @click="submit">{{ $t(saving ? '保存中…' : '保存') }}</button>
    </template>
  </BaseModal>
</template>
