<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { api } from '../lib/api'
import BaseModal from './BaseModal.vue'
import DatePickerInput from './DatePickerInput.vue'

const props = defineProps({
  open: Boolean,
  mode: {
    type: String,
    default: 'expand'
  },
  contract: {
    type: Object,
    default: null
  },
  roomItem: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['close', 'save'])
const form = reactive({
  cutoff_date: '',
  room_no: '',
  tower: '',
  area: 0,
  unit_price: 0,
  start_date: '',
  end_date: '',
  notes: ''
})
const rooms = ref([])
const roomsLoading = ref(false)
const roomError = ref('')
let roomsRequestId = 0

const title = computed(() => {
  if (props.mode === 'reduce') return '减租'
  if (props.mode === 'swap') return '换房'
  return '扩租'
})
const isReduce = computed(() => props.mode === 'reduce')
const isSwap = computed(() => props.mode === 'swap')
const roomOptions = computed(() => rooms.value.filter((room) => room.is_leasable !== false))
const selectedRoom = computed(() => {
  const roomNo = String(form.room_no || '').trim()
  return roomNo ? roomOptions.value.find((room) => room.room_no === roomNo) : null
})
const formError = computed(() => {
  if (!props.contract?.id) return '合同信息不完整'
  if (isReduce.value) {
    if (!form.cutoff_date) return '请填写房源截止日'
    return ''
  }
  if (isSwap.value && !form.cutoff_date) return '请填写旧房源截止日'
  if (!String(form.room_no || '').trim()) return '请填写新房源'
  if (!form.start_date || !form.end_date) return '请填写新房源起止日期'
  if (form.end_date < form.start_date) return '新房源终止日不能早于起租日'
  if (isSwap.value && form.start_date <= form.cutoff_date) return '新房源起租日必须晚于旧房源截止日'
  if (selectedRoom.value?.occupied) return '新房源在所选期间已占用'
  if (Number(form.unit_price || 0) <= 0) return '请填写有效单价'
  return ''
})
const canSubmit = computed(() => !formError.value)

function todayInputValue() {
  const today = new Date()
  const year = today.getFullYear()
  const month = String(today.getMonth() + 1).padStart(2, '0')
  const day = String(today.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function nextDate(value) {
  if (!value) return ''
  const [year, month, day] = String(value).split('-').map((part) => Number(part))
  if (!year || !month || !day) return ''
  const date = new Date(Date.UTC(year, month - 1, day))
  date.setUTCDate(date.getUTCDate() + 1)
  return date.toISOString().slice(0, 10)
}

function defaultCutoffDate() {
  const start = props.roomItem?.start_date || props.contract?.start_date || ''
  const end = props.roomItem?.end_date || props.contract?.end_date || ''
  const today = todayInputValue()
  if (start && end && start <= today && today <= end) return today
  return end
}

function resetForm() {
  const cutoff = defaultCutoffDate()
  const nextStart = nextDate(cutoff)
  form.cutoff_date = isReduce.value || isSwap.value ? cutoff : ''
  form.room_no = ''
  form.tower = ''
  form.area = 0
  form.unit_price = Number(props.roomItem?.unit_price || 0)
  form.start_date = isSwap.value ? nextStart : (props.contract?.start_date || '')
  form.end_date = props.contract?.end_date || ''
  form.notes = ''
}

function applySelectedRoom() {
  const room = selectedRoom.value
  if (!room) return
  form.tower = room.tower || form.tower
  form.area = Number(room.area || 0)
}

async function fetchRooms() {
  if (!props.open || isReduce.value || !form.start_date || !form.end_date) return
  const requestId = ++roomsRequestId
  roomsLoading.value = true
  roomError.value = ''
  const params = new URLSearchParams({
    start_date: form.start_date,
    end_date: form.end_date,
    exclude_contract_id: String(props.contract?.id || '')
  })
  try {
    const data = await api(`/api/rooms?${params}`)
    if (requestId === roomsRequestId) rooms.value = Array.isArray(data) ? data : []
  } catch (error) {
    if (requestId === roomsRequestId) {
      rooms.value = []
      roomError.value = error.message || '房态读取失败'
    }
  } finally {
    if (requestId === roomsRequestId) roomsLoading.value = false
  }
}

function submit() {
  if (!canSubmit.value) return
  if (isReduce.value) {
    emit('save', { end_date: form.cutoff_date, notes: form.notes || '' })
    return
  }
  const newRoom = {
    room_no: String(form.room_no || '').trim(),
    tower: form.tower || '',
    area: Number(form.area || 0),
    unit_price: Number(form.unit_price || 0),
    start_date: form.start_date,
    end_date: form.end_date,
    notes: form.notes || ''
  }
  if (isSwap.value) {
    emit('save', { end_date: form.cutoff_date, new_room: newRoom, notes: form.notes || '' })
    return
  }
  emit('save', newRoom)
}

watch(
  () => [props.open, props.mode, props.contract?.id, props.roomItem?.id],
  () => {
    if (props.open) resetForm()
  },
  { immediate: true }
)

watch(
  () => [props.open, form.start_date, form.end_date],
  () => fetchRooms(),
  { immediate: true }
)
</script>

<template>
  <BaseModal :open="open" :title="$t(title)" size="small" @close="emit('close')">
    <div class="form-grid single">
      <div v-if="roomItem" class="form-field">
        <span class="section-label">{{ $t("当前房源") }}</span>
        <div>{{ roomItem.room_no || '--' }} · {{ $t(roomItem.start_date || contract?.start_date || '--') }} - {{ $t(roomItem.end_date || contract?.end_date || '--') }}</div>
      </div>

      <div v-if="!isReduce" class="form-field">
        <span class="section-label">{{ $t("新房源") }}</span>
        <input v-model="form.room_no" class="input" list="room-change-room-options" @change="applySelectedRoom" />
        <small v-if="roomsLoading" class="helper-text">{{ $t("房态读取中") }}</small>
        <small v-else-if="roomError" class="helper-text danger">{{ $t(roomError) }}</small>
        <small v-else-if="selectedRoom?.occupied" class="helper-text danger">{{ $t("该期间已占用") }}</small>
        <small v-else-if="selectedRoom" class="helper-text success">{{ $t(selectedRoom.tower || '--') }} · {{ $t(selectedRoom.area || 0) }}{{ $t("㎡ · 可租") }}</small>
        <datalist id="room-change-room-options">
          <option v-for="room in roomOptions" :key="room.id" :value="room.room_no" :label="$t(`${room.tower || '--'} · ${room.area || 0}㎡${room.occupied ? ' · 已占用' : ' · 可租'}`)" />
        </datalist>
      </div>

      <div v-if="!isReduce" class="form-field">
        <span class="section-label">{{ $t("塔楼") }}</span>
        <input v-model="form.tower" class="input" />
      </div>
      <label v-if="!isReduce" class="form-field">
        <span class="section-label">{{ $t("面积") }}</span>
        <input v-model.number="form.area" type="number" class="input" />
      </label>
      <label v-if="!isReduce" class="form-field">
        <span class="section-label">{{ $t("日租单价") }}</span>
        <input v-model.number="form.unit_price" type="number" class="input" />
      </label>

      <div v-if="isReduce || isSwap" class="form-field">
        <span class="section-label">{{ $t(isSwap ? '旧房源截止日' : '房源截止日') }}</span>
        <DatePickerInput v-model="form.cutoff_date" />
      </div>
      <div v-if="!isReduce" class="form-field">
        <span class="section-label">{{ $t("新房源起租日") }}</span>
        <DatePickerInput v-model="form.start_date" />
      </div>
      <div v-if="!isReduce" class="form-field">
        <span class="section-label">{{ $t("新房源终止日") }}</span>
        <DatePickerInput v-model="form.end_date" />
      </div>
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
