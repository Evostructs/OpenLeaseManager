<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  modelValue: {
    type: String,
    default: ''
  },
  mode: {
    type: String,
    default: 'date'
  },
  placeholder: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['update:modelValue'])

const root = ref(null)
const open = ref(false)
const viewMode = ref('calendar')
const cursorYear = ref(new Date().getFullYear())
const cursorMonth = ref(new Date().getMonth())
const yearPageStart = ref(Math.floor(cursorYear.value / 12) * 12)

const pickerMode = computed(() => (props.mode === 'month' ? 'month' : 'date'))
const isMonthMode = computed(() => pickerMode.value === 'month')
const resolvedPlaceholder = computed(() => props.placeholder || (isMonthMode.value ? '选择月份' : '选择日期'))
const weekdays = ['日', '一', '二', '三', '四', '五', '六']
const monthLabels = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月']

function pad(value) {
  return String(value).padStart(2, '0')
}

function formatDateValue(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function formatMonthValue(year, monthIndex) {
  return `${year}-${pad(monthIndex + 1)}`
}

function todayDate() {
  return formatDateValue(new Date())
}

function todayMonth() {
  const today = new Date()
  return formatMonthValue(today.getFullYear(), today.getMonth())
}

function parseValue(value) {
  const raw = String(value || '').trim()
  if (!raw) return null

  if (isMonthMode.value) {
    const match = raw.match(/^(\d{4})-(\d{2})$/)
    if (!match) return null
    const year = Number(match[1])
    const month = Number(match[2])
    if (month < 1 || month > 12) return null
    return { year, monthIndex: month - 1 }
  }

  const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!match) return null
  const year = Number(match[1])
  const monthIndex = Number(match[2]) - 1
  const day = Number(match[3])
  const date = new Date(year, monthIndex, day)
  if (date.getFullYear() !== year || date.getMonth() !== monthIndex || date.getDate() !== day) return null
  return { year, monthIndex, day }
}

function syncCursor(value = props.modelValue) {
  const parsed = parseValue(value)
  const fallback = new Date()
  cursorYear.value = parsed?.year ?? fallback.getFullYear()
  cursorMonth.value = parsed?.monthIndex ?? fallback.getMonth()
  yearPageStart.value = Math.floor(cursorYear.value / 12) * 12
}

const displayValue = computed(() => {
  const parsed = parseValue(props.modelValue)
  if (!parsed) return ''
  if (isMonthMode.value) return `${parsed.year}年${pad(parsed.monthIndex + 1)}月`
  return `${parsed.year}年${parsed.monthIndex + 1}月${parsed.day}日`
})

const panelTitle = computed(() => {
  if (viewMode.value === 'years') return `${yearPageStart.value} - ${yearPageStart.value + 11}`
  if (isMonthMode.value) return `${cursorYear.value}年`
  return `${cursorYear.value}年 ${cursorMonth.value + 1}月`
})

const calendarDays = computed(() => {
  const firstDay = new Date(cursorYear.value, cursorMonth.value, 1).getDay()
  const today = todayDate()
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(cursorYear.value, cursorMonth.value, index - firstDay + 1)
    const value = formatDateValue(date)
    return {
      value,
      day: date.getDate(),
      outside: date.getMonth() !== cursorMonth.value,
      selected: value === props.modelValue,
      today: value === today
    }
  })
})

const monthOptions = computed(() => {
  const currentMonth = todayMonth()
  return monthLabels.map((label, index) => {
    const value = formatMonthValue(cursorYear.value, index)
    return {
      label,
      value,
      selected: value === props.modelValue,
      today: value === currentMonth
    }
  })
})

const yearOptions = computed(() =>
  Array.from({ length: 12 }, (_, index) => {
    const year = yearPageStart.value + index
    return {
      year,
      selected: year === cursorYear.value,
      current: year === new Date().getFullYear()
    }
  })
)

watch(
  () => props.modelValue,
  (value) => {
    if (!open.value) syncCursor(value)
  },
  { immediate: true }
)

function toggleOpen() {
  open.value = !open.value
  if (open.value) {
    viewMode.value = 'calendar'
    syncCursor()
  }
}

function closePanel() {
  open.value = false
  viewMode.value = 'calendar'
}

function shiftPeriod(delta) {
  if (viewMode.value === 'years') {
    yearPageStart.value += delta * 12
    return
  }

  if (isMonthMode.value) {
    cursorYear.value += delta
    yearPageStart.value = Math.floor(cursorYear.value / 12) * 12
    return
  }

  const next = new Date(cursorYear.value, cursorMonth.value + delta, 1)
  cursorYear.value = next.getFullYear()
  cursorMonth.value = next.getMonth()
  yearPageStart.value = Math.floor(cursorYear.value / 12) * 12
}

function openYearPicker() {
  yearPageStart.value = Math.floor(cursorYear.value / 12) * 12
  viewMode.value = 'years'
}

function selectYear(year) {
  cursorYear.value = year
  yearPageStart.value = Math.floor(year / 12) * 12
  viewMode.value = 'calendar'
}

function selectDate(value) {
  emit('update:modelValue', value)
  closePanel()
}

function selectMonth(value) {
  emit('update:modelValue', value)
  closePanel()
}

function selectToday() {
  emit('update:modelValue', isMonthMode.value ? todayMonth() : todayDate())
  closePanel()
}

function clearValue() {
  emit('update:modelValue', '')
  closePanel()
}

function handleDocumentMouseDown(event) {
  if (!open.value || root.value?.contains(event.target)) return
  closePanel()
}

function handleDocumentKeyDown(event) {
  if (event.key === 'Escape') closePanel()
}

onMounted(() => {
  document.addEventListener('mousedown', handleDocumentMouseDown)
  document.addEventListener('keydown', handleDocumentKeyDown)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', handleDocumentMouseDown)
  document.removeEventListener('keydown', handleDocumentKeyDown)
})
</script>

<template>
  <div ref="root" class="date-picker" :class="`date-picker-${pickerMode}`">
    <button type="button" class="date-picker-trigger" :aria-expanded="open" @click="toggleOpen">
      <span :class="{ 'date-picker-placeholder': !displayValue }">{{ displayValue || resolvedPlaceholder }}</span>
      <span class="date-picker-calendar-icon" aria-hidden="true"></span>
    </button>

    <div v-if="open" class="date-picker-panel">
      <div class="date-picker-header">
        <button type="button" class="date-picker-nav" @click="shiftPeriod(-1)">‹</button>
        <button type="button" class="date-picker-title" @click="openYearPicker">{{ panelTitle }}</button>
        <button type="button" class="date-picker-nav" @click="shiftPeriod(1)">›</button>
      </div>

      <div v-if="viewMode === 'years'" class="date-picker-year-grid">
        <button
          v-for="item in yearOptions"
          :key="item.year"
          type="button"
          class="date-picker-year"
          :class="{ selected: item.selected, today: item.current }"
          @click="selectYear(item.year)"
        >
          {{ item.year }}
        </button>
      </div>

      <div v-else-if="isMonthMode" class="date-picker-month-grid">
        <button
          v-for="item in monthOptions"
          :key="item.value"
          type="button"
          class="date-picker-month"
          :class="{ selected: item.selected, today: item.today }"
          @click="selectMonth(item.value)"
        >
          {{ item.label }}
        </button>
      </div>

      <div v-else class="date-picker-day-panel">
        <div class="date-picker-weekdays">
          <span v-for="weekday in weekdays" :key="weekday">{{ weekday }}</span>
        </div>
        <div class="date-picker-day-grid">
          <button
            v-for="item in calendarDays"
            :key="item.value"
            type="button"
            class="date-picker-day"
            :class="{ outside: item.outside, selected: item.selected, today: item.today }"
            @click="selectDate(item.value)"
          >
            {{ item.day }}
          </button>
        </div>
      </div>

      <div class="date-picker-footer">
        <button type="button" class="date-picker-link" @click="clearValue">清除</button>
        <button type="button" class="date-picker-link strong" @click="selectToday">今天</button>
      </div>
    </div>
  </div>
</template>
