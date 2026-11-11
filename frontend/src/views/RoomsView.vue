<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { api, openDownload } from '../lib/api'
import { formatArea, formatCurrencyFixed, formatDate } from '../lib/format'
import { sortTableRows } from '../lib/table'
import { cachedApi, invalidateApiCache } from '../lib/prefetchCache'
import {
  displayTowerLabel,
  floorOrder,
  getBusinessSubtype,
  getFloorZone,
  getPhysicalTower,
  getPossibleTowerFloorZones,
  getRoomStatusTone,
  getTowerFloorZone,
  getUseCategory,
  isCommercialRoom,
  normalizeHeatmapFloor,
  normalizeRoom,
  normalizeText,
  parseFloorNumber,
  profileAnalysisSegments,
  profileSpaceOptions,
  roomAnalysisSegment,
  roomHasBlockedSignal,
  roomHeatmapTestId,
  roomMatchesPrioritySignal,
  roomMonthlyRent,
  roomPriorityScore,
  roomSignalBadges,
  segmentTone,
  usesLegacyDemoAFloorPlan
} from '../lib/roomsDomain'
import BaseModal from '../components/BaseModal.vue'
import RoomFormModal from '../components/RoomFormModal.vue'
import DatePickerInput from '../components/DatePickerInput.vue'
import TableSkeleton from '../components/TableSkeleton.vue'

const props = defineProps({
  user: {
    type: Object,
    required: true
  },
  operationsProfile: {
    type: Object,
    default: () => ({})
  },
  propertyScope: {
    type: Object,
    default: () => ({})
  },
  drilldownIntent: {
    type: Object,
    default: null
  },
  drilldownToken: {
    type: Number,
    default: 0
  }
})

const emit = defineEmits(['notify', 'open-customer', 'open-contract'])
const canEdit = computed(() => props.user?.role === 'admin' && props.propertyScope?.type === 'building')
const canExport = computed(() => ['admin', 'editor'].includes(props.user?.role))
const pageSizeOptions = [
  { label: '10', value: 10 },
  { label: '25', value: 25 },
  { label: '50', value: 50 },
  { label: '100', value: 100 },
  { label: '不限', value: 0 }
]

const ROOM_LIST_CACHE_TTL_MS = 20000

const rooms = ref([])
const stats = ref(null)
const roomsLoading = ref(false)
const roomsLoaded = ref(false)
const selectedRoomIds = ref([])
const roomPager = reactive({ page: 1, pageSize: 25 })
const filters = reactive({ keyword: '', occupancy: '', tower: '', use: '', subtype: '', zone: '' })
const availabilityRange = reactive({ start_date: '', end_date: '' })
const appliedAvailabilityRange = reactive({ start_date: '', end_date: '' })
const roomSort = reactive({
  key: '',
  direction: ''
})
const roomFormOpen = ref(false)
const editingRoom = ref(null)
const importInput = ref(null)
const roomBatchActionsOpen = ref(false)
const roomRowMoreOpenId = ref(null)
const roomContractsOpen = ref(false)
const roomContractsLoading = ref(false)
const roomContractDetail = ref(null)
const roomContractsScrollY = ref(0)
const expandedPriorityLanes = ref([])
const selectedRoomId = ref(null)
const roomDeleteTarget = ref(null)
const roomDeleteSubmitting = ref(false)
let roomFilterScrollTimer = null
let appliedDrilldownToken = 0
const heatmapTowerOrder = ['北塔', '南塔']
const towerFloorPlans = {
  北塔: { min: 1, max: 22, missing: new Set([4, 13, 14]) },
  南塔: { min: 1, max: 36, missing: new Set([4, 13, 14, 24, 25]) }
}
const floorZoneOrder = ['retail', 'podium', 'low', 'mid', 'high', 'basement', 'other']
const showLegacyFloorWorkspace = computed(() => usesLegacyDemoAFloorPlan(props.operationsProfile))
const roomSegments = computed(() => profileAnalysisSegments(props.operationsProfile))
const availabilityRangeActive = computed(() => Boolean(appliedAvailabilityRange.start_date || appliedAvailabilityRange.end_date))
const roomsInitialLoading = computed(() => roomsLoading.value && !roomsLoaded.value && !rooms.value.length)
const roomsRefreshing = computed(() => roomsLoading.value && (roomsLoaded.value || rooms.value.length > 0))
const availabilityRangeLabel = computed(() => {
  if (!availabilityRangeActive.value) return '当前房态'
  const start = appliedAvailabilityRange.start_date || appliedAvailabilityRange.end_date
  const end = appliedAvailabilityRange.end_date || appliedAvailabilityRange.start_date
  return start === end ? `${start} 房态` : `${start} 至 ${end}`
})
const roomContractTitle = computed(() => {
  const roomNo = roomContractDetail.value?.room?.room_no || ''
  return roomNo ? `${roomNo} 合同记录` : '房源合同记录'
})







function getRoomStatus(room) {
  if (!room.occupied || room.signals?.vacant) return '空置'
  if (room.signals?.overdue) return '历史逾期'
  if (room.signals?.expiring_90) return '即将到期'
  if (room.signals?.missing_attachment) return '资料缺失'
  if (availabilityRangeActive.value) return room.contract?.availability_status || '已占用'
  const contractStatus = normalizeText(room.contract?.status)
  return contractStatus || '已出租'
}

function roomSegment(room) {
  return roomAnalysisSegment(room, props.operationsProfile)
}

function roomSegmentLabel(room) {
  return roomSegment(room)?.label || displayTowerLabel(room?.tower)
}

function roomFloorLabel(room) {
  const floor = normalizeText(room?.floor)
  return floor ? `${floor}F` : '未登记楼层'
}

function roomMatchesTowerFilter(room, value) {
  if (!value) return true
  const target = roomSegments.value.find((item) => (
    item.filterValue === value || item.label === value || item.aliases.includes(value)
  ))
  const current = roomSegment(room)
  if (target && current) return target.code === current.code
  return normalizeText(room?.tower) === normalizeText(value)
}







function roomTooltip(room) {
  const parts = [
    room.room_no,
    getRoomStatus(room),
    room.contract?.tenant_name || '暂无客户',
    room.contract?.end_date ? `到期 ${formatDate(room.contract.end_date)}` : '',
    room.contract?.overdue_balance > 0 ? `历史逾期 ${formatCurrencyFixed(room.contract.overdue_balance)}` : '',
    room.contract?.missing_contract_body ? '缺合同正文' : '',
    room.contract?.next_follow_up_date ? `跟进 ${formatDate(room.contract.next_follow_up_date)}` : ''
  ]
  return parts.filter(Boolean).join(' · ')
}

function heatmapFloorSummary(floor) {
  const total = floor.items.length
  if (!total) return '暂无房源'
  const occupied = floor.items.filter((room) => room.occupied && !room.signals?.vacant).length
  const vacant = total - occupied
  const priority = floor.items.filter(roomMatchesPrioritySignal).length
  return [
    `${total}间`,
    `${occupied}租`,
    vacant ? `${vacant}空` : '',
    priority ? `${priority}待办` : ''
  ].filter(Boolean).join(' · ')
}

function roomMapMeta(room) {
  if (!room.occupied || room.signals?.vacant) return `${formatArea(room.area)}㎡可招商`
  if (room.signals?.overdue) return `历史逾期 ${formatCurrencyFixed(room.contract?.overdue_balance || 0)}`
  if (room.signals?.expiring_90) return room.contract?.end_date ? `${formatDate(room.contract.end_date)}到期` : '即将到期'
  if (room.signals?.missing_attachment) return '资料待补'
  return room.contract?.tenant_name || `${formatArea(room.area)}㎡`
}


function attachmentSignalLabel(operation) {
  const missing = operation?.attachment_summary?.missing_required || []
  if (missing.length) return `${missing.join('、')}待补`
  return '资料齐备'
}

function followUpSignalLabel(operation) {
  if (operation?.follow_up?.next_action) return operation.follow_up.next_action
  if (operation?.follow_up?.content) return operation.follow_up.content
  return '暂无跟进'
}








const filteredRooms = computed(() =>
  rooms.value.filter((room) => {
    if (filters.keyword) {
      const keyword = filters.keyword.toLowerCase()
      const haystack = [
        room.room_no,
        room.floor,
        room.room_type,
        displayTowerLabel(room.tower),
        roomSegmentLabel(room),
        room.contract?.tenant_name,
        room.contract?.contract_no,
        room.contract?.internal_contact_person
      ].join(' ').toLowerCase()
      if (!haystack.includes(keyword)) return false
    }
    if (filters.occupancy && !(room.operating_statuses || []).includes(filters.occupancy)) return false
    if (!roomMatchesTowerFilter(room, filters.tower)) return false
    if (filters.use && getUseCategory(room) !== filters.use) return false
    if (filters.subtype && getBusinessSubtype(room) !== filters.subtype) return false
    if (filters.zone && getFloorZone(room) !== filters.zone) return false
    return true
  })
)

const sortedRooms = computed(() => {
  if (!roomSort.key || !roomSort.direction) return filteredRooms.value

  const accessors = {
    room_no: (room) => room.room_no,
    tower: (room) => room.tower,
    floor: (room) => parseFloorNumber(room.floor) ?? room.floor,
    room_type: (room) => room.room_type,
    area: (room) => room.area,
    occupancy: (room) => getRoomStatus(room),
    tenant_name: (room) => room.contract?.tenant_name || ''
  }

  return sortTableRows(filteredRooms.value, accessors[roomSort.key], roomSort.direction)
})

const roomTotalPages = computed(() => (roomPager.pageSize === 0 ? 1 : Math.max(1, Math.ceil(sortedRooms.value.length / roomPager.pageSize))))
const hasRoomPagination = computed(() => roomPager.pageSize !== 0 && roomTotalPages.value > 1)
const paginatedRooms = computed(() => {
  if (roomPager.pageSize === 0) return sortedRooms.value
  const start = (roomPager.page - 1) * roomPager.pageSize
  return sortedRooms.value.slice(start, start + roomPager.pageSize)
})
const roomsPageLabel = computed(() => (roomPager.pageSize === 0 ? '全部' : `${roomPager.page} / ${roomTotalPages.value}`))
const roomsShowingText = computed(() => {
  const total = sortedRooms.value.length
  if (roomPager.pageSize === 0) return `显示全部 ${total} 间`
  const start = total ? (roomPager.page - 1) * roomPager.pageSize + 1 : 0
  const end = Math.min(roomPager.page * roomPager.pageSize, total)
  return `显示 ${start}-${end} 间`
})

const towerOptions = computed(() => {
  const options = [...profileSpaceOptions(props.operationsProfile)]
  const known = new Set(options.map((item) => item.value))
  for (const room of rooms.value) {
    const value = normalizeText(room.tower)
    if (!value || known.has(value)) continue
    known.add(value)
    options.push({ code: value, value, label: displayTowerLabel(value), kind: '' })
  }
  return options
})

const subtypeOptions = computed(() => {
  const options = new Set()
  for (const room of rooms.value) {
    const subtype = getBusinessSubtype(room)
    if (subtype) options.add(subtype)
  }
  return [...options]
})

const zoneOptions = computed(() => {
  const options = new Set()
  for (const room of rooms.value) {
    const zone = getFloorZone(room)
    if (zone) options.add(zone)
  }
  return [...options].sort((left, right) => {
    const leftIndex = floorZoneOrder.indexOf(left)
    const rightIndex = floorZoneOrder.indexOf(right)
    return (leftIndex === -1 ? 999 : leftIndex) - (rightIndex === -1 ? 999 : rightIndex)
  })
})

const groupedHeatmap = computed(() => {
  if (!showLegacyFloorWorkspace.value) return []
  const buckets = {}
  for (const room of sortedRooms.value) {
    const tower = getPhysicalTower(room)
    const floor = normalizeHeatmapFloor(room.floor || '0')
    if (isMissingHeatmapFloor(tower, floor)) continue
    buckets[tower] ||= {}
    buckets[tower][floor] ||= []
    const labelStatus = getRoomStatus(room)
    const tone = getRoomStatusTone(labelStatus)

    buckets[tower][floor].push({
      ...room,
      labelStatus,
      tone
    })
  }

  const towers = new Set(Object.keys(buckets))
  if (!filters.tower) {
    for (const tower of heatmapTowerOrder) towers.add(tower)
  } else if (heatmapTowerOrder.includes(filters.tower)) {
    towers.add(filters.tower)
  }

  return [...towers].map((tower) => [tower, buckets[tower] || {}])
    .sort(([left], [right]) => {
      const leftIndex = heatmapTowerOrder.indexOf(left)
      const rightIndex = heatmapTowerOrder.indexOf(right)
      return (leftIndex === -1 ? 999 : leftIndex) - (rightIndex === -1 ? 999 : rightIndex)
    })
    .map(([tower, floors]) => {
      const floorEntries = buildHeatmapFloors(tower, floors)
      return {
        tower,
        title: displayTowerLabel(tower),
        floors: floorEntries,
        count: floorEntries.reduce((total, floor) => total + floor.items.length, 0)
      }
    })
})

const totalArea = computed(() =>
  rooms.value
    .filter((room) => room.is_leasable !== false)
    .reduce((sum, room) => sum + Number(room.area || 0), 0)
)

const occupiedArea = computed(() =>
  rooms.value
    .filter((room) => room.is_leasable !== false && room.occupied)
    .reduce((sum, room) => sum + Number(room.area || 0), 0)
)

const occupancyRate = computed(() => {
  if (!totalArea.value) return 0
  return Math.round((occupiedArea.value / totalArea.value) * 1000) / 10
})

const occupancyStatusStrip = computed(() => {
  const leasable = rooms.value.filter((room) => room.is_leasable !== false)
  const denominator = leasable.reduce((sum, room) => sum + Number(room.area || 0), 0)
  const statusBuckets = [
    {
      key: 'occupied',
      label: availabilityRangeActive.value ? '已占用' : '在租',
      tone: 'success',
      filter: availabilityRangeActive.value ? '已占用' : '在租',
      match: (room) => room.occupied && !room.signals?.vacant
    },
    {
      key: 'vacant',
      label: '空置',
      tone: 'info',
      filter: '空置',
      match: (room) => !room.occupied || room.signals?.vacant
    },
    {
      key: 'expiring',
      label: '到期',
      tone: 'warning',
      filter: '即将到期',
      match: (room) => room.signals?.expiring_90
    },
    {
      key: 'overdue',
      label: '收款',
      tone: 'error',
      filter: '历史逾期',
      match: (room) => room.signals?.overdue
    },
    {
      key: 'missing',
      label: '资料',
      tone: 'warning',
      filter: '资料缺失',
      match: (room) => room.signals?.missing_attachment
    },
    {
      key: 'blocked',
      label: '逾期占用',
      tone: 'warning',
      filter: '逾期占用',
      match: roomHasBlockedSignal
    }
  ]

  return statusBuckets.map((bucket) => {
    const matchedRooms = leasable.filter(bucket.match)
    const area = matchedRooms.reduce((sum, room) => sum + Number(room.area || 0), 0)
    return {
      ...bucket,
      count: matchedRooms.length,
      area,
      rent: matchedRooms.reduce((sum, room) => sum + roomMonthlyRent(room), 0),
      share: denominator ? Math.round((area / denominator) * 1000) / 10 : 0
    }
  })
})

const heatmapSignalSummary = computed(() => {
  const visibleRooms = sortedRooms.value.filter((room) => room.is_leasable !== false)
  const baseRooms = rooms.value.filter((room) => {
    if (room.is_leasable === false) return false
    if (filters.keyword) {
      const keyword = filters.keyword.toLowerCase()
      const haystack = [
        room.room_no,
        room.floor,
        room.room_type,
        displayTowerLabel(room.tower),
        roomSegmentLabel(room),
        room.contract?.tenant_name,
        room.contract?.contract_no,
        room.contract?.internal_contact_person
      ].join(' ').toLowerCase()
      if (!haystack.includes(keyword)) return false
    }
    if (!roomMatchesTowerFilter(room, filters.tower)) return false
    if (filters.use && getUseCategory(room) !== filters.use) return false
    if (filters.subtype && getBusinessSubtype(room) !== filters.subtype) return false
    if (filters.zone && getFloorZone(room) !== filters.zone) return false
    return true
  })
  const bucket = (key, label, filter, tone, predicate) => {
    const sourceRows = key === 'all' ? visibleRooms : baseRooms
    const rows = sourceRows.filter(predicate)
    return {
      key,
      label,
      filter,
      tone,
      count: rows.length,
      area: rows.reduce((sum, room) => sum + Number(room.area || 0), 0),
      active: filters.occupancy === filter || (!filter && !filters.occupancy)
    }
  }
  return [
    bucket('all', '当前显示', '', 'info', () => true),
    bucket('vacant', '空置', '空置', 'info', (room) => !room.occupied || room.signals?.vacant),
    bucket('expiring', '90天到期', '即将到期', 'warning', (room) => room.signals?.expiring_90),
    bucket('overdue', '历史逾期', '历史逾期', 'error', (room) => room.signals?.overdue),
    bucket('missing', '资料缺口', '资料缺失', 'warning', (room) => room.signals?.missing_attachment)
  ]
})

const segmentCards = computed(() => {
  const cards = roomSegments.value.map((segment) => ({
    key: segment.code,
    label: segment.label,
    filterValue: segment.filterValue,
    total: 0,
    area: 0,
    occupied: 0,
    occupiedArea: 0,
    vacant: 0,
    vacantArea: 0,
    expiring: 0,
    overdue: 0,
    missing: 0,
    priority: 0,
    rent: 0,
    pressureRent: 0
  }))
  const byKey = Object.fromEntries(cards.map((card) => [card.key, card]))

  for (const room of rooms.value) {
    if (room.is_leasable === false) continue
    const key = roomSegment(room)?.code
    const card = byKey[key]
    if (!card) continue
    const area = Number(room.area || 0)
    const monthlyRent = roomMonthlyRent(room)
    card.total += 1
    card.area += area
    card.rent += monthlyRent
    if (room.occupied) {
      card.occupied += 1
      card.occupiedArea += area
    } else {
      card.vacant += 1
      card.vacantArea += area
    }
    if (room.signals?.expiring_90) card.expiring += 1
    if (room.signals?.overdue) card.overdue += 1
    if (room.signals?.missing_attachment) card.missing += 1
    if (roomMatchesPrioritySignal(room)) {
      card.priority += 1
      card.pressureRent += monthlyRent
    }
  }

  return cards.map((card) => ({
    ...card,
    occupancyRate: card.area ? Math.round((card.occupiedArea / card.area) * 1000) / 10 : 0,
    vacancyRate: card.area ? Math.round((card.vacantArea / card.area) * 1000) / 10 : 0,
    pressureCount: card.priority
  }))
})

const vacancyPressureRows = computed(() => {
  const buckets = new Map()

  for (const room of rooms.value) {
    if (room.is_leasable === false) continue
    const segment = roomSegment(room)
    if (!segment) continue
    const zone = getFloorZone(room)
    const key = `${segment.code}::${zone}`
    const area = Number(room.area || 0)
    const vacant = !room.occupied || room.signals?.vacant
    const bucket = buckets.get(key) || {
      key,
      segment: segment.label,
      towerFilter: segment.filterValue,
      zone,
      totalCount: 0,
      totalArea: 0,
      vacantCount: 0,
      vacantArea: 0,
      priorityCount: 0,
      floors: new Set()
    }

    bucket.totalCount += 1
    bucket.totalArea += area
    if (roomMatchesPrioritySignal(room)) bucket.priorityCount += 1
    if (vacant) {
      bucket.vacantCount += 1
      bucket.vacantArea += area
      if (room.floor) bucket.floors.add(normalizeText(room.floor))
    }
    buckets.set(key, bucket)
  }

  return [...buckets.values()]
    .filter((bucket) => bucket.vacantCount > 0)
    .map((bucket) => ({
      ...bucket,
      vacancyRate: bucket.totalArea ? Math.round((bucket.vacantArea / bucket.totalArea) * 1000) / 10 : 0,
      floorHint: vacancyFloorHint(bucket.floors)
    }))
    .sort((left, right) => {
      if (right.vacantArea !== left.vacantArea) return right.vacantArea - left.vacantArea
      if (right.vacantCount !== left.vacantCount) return right.vacantCount - left.vacantCount
      return right.priorityCount - left.priorityCount
    })
    .slice(0, 3)
})

const priorityLanes = computed(() => {
  const bySegment = Object.fromEntries(roomSegments.value.map((segment) => [segment.code, []]))

  for (const room of rooms.value) {
    if (room.is_leasable === false) continue
    const score = roomPriorityScore(room)
    if (score <= 0) continue
    const segment = roomSegment(room)
    if (!segment || !bySegment[segment.code]) continue
    bySegment[segment.code].push({ room, score })
  }

  return roomSegments.value.map((segment) => {
    const items = bySegment[segment.code].sort((left, right) => {
      if (right.score !== left.score) return right.score - left.score
      return Number(right.room.area || 0) - Number(left.room.area || 0)
    })
    const expanded = expandedPriorityLanes.value.includes(segment.code)
    const visibleLimit = expanded ? 8 : 2
    const visibleItems = items.slice(0, visibleLimit)
    return {
      key: segment.code,
      label: segment.label,
      filterValue: segment.filterValue,
      count: items.length,
      expanded,
      hiddenCount: Math.max(items.length - visibleItems.length, 0),
      rooms: visibleItems.map((item) => item.room)
    }
  })
})

const priorityRoomTotal = computed(() =>
  priorityLanes.value.reduce((sum, lane) => sum + lane.count, 0)
)

const roomOperation = computed(() => roomContractDetail.value?.operation || null)


function roomActionReason(room) {
  if (room.signals?.overdue) return `历史逾期 ${formatCurrencyFixed(room.contract?.overdue_balance || 0)}`
  if (room.signals?.expiring_90) return `到期日 ${formatDate(room.contract?.end_date)}`
  if (room.signals?.missing_attachment) return '合同正文资料待补'
  if (!room.occupied || room.signals?.vacant) return `空置 ${formatArea(room.area)}㎡`
  if (room.contract?.conflict_count > 1) return `${room.contract.conflict_count} 份占用冲突`
  return '查看房源情况'
}

function setTowerFilter(value) {
  filters.tower = filters.tower === value ? '' : value
  preserveViewportAfterFilterChange()
}

function setOccupancyFilter(value) {
  filters.occupancy = filters.occupancy === value ? '' : value
  preserveViewportAfterFilterChange()
}

function togglePriorityLane(key) {
  expandedPriorityLanes.value = expandedPriorityLanes.value.includes(key)
    ? expandedPriorityLanes.value.filter((item) => item !== key)
    : [...expandedPriorityLanes.value, key]
}

function vacancyFloorHint(floors) {
  const values = [...floors].sort((left, right) => floorOrder(left) - floorOrder(right))
  if (!values.length) return '楼层待标注'
  if (values.length === 1) return `${values[0]}F`
  if (values.length === 2) return `${values[0]}F、${values[1]}F`
  return `${values[0]}F、${values[1]}F 等 ${values.length} 层`
}

function applyVacancyPressureRow(row) {
  filters.tower = row.towerFilter || ''
  filters.occupancy = '空置'
  if (row.zone) filters.zone = row.zone
  preserveViewportAfterFilterChange()
}

function towerFilterFromSegment(value) {
  const segment = roomSegments.value.find((item) => (
    item.code === value || item.label === value || item.filterValue === value || item.aliases.includes(value)
  ))
  if (segment) return segment.filterValue
  return value || ''
}


function isMissingHeatmapFloor(tower, floor) {
  const plan = towerFloorPlans[tower]
  const numericFloor = parseFloorNumber(floor)
  return Boolean(plan && numericFloor != null && plan.missing.has(numericFloor))
}

function plannedFloors(tower, floorBuckets = {}) {
  const plan = towerFloorPlans[tower]
  if (!plan) return null

  const floors = []
  for (let floor = plan.max; floor >= plan.min; floor -= 1) {
    if (plan.missing.has(floor)) continue
    if (filters.zone && !getPossibleTowerFloorZones(tower, floor).includes(filters.zone)) continue
    if (floor < 3 && !(floorBuckets[String(floor)] || []).length) continue
    floors.push(String(floor))
  }
  return floors
}

function buildHeatmapFloors(tower, floors) {
  const plan = plannedFloors(tower, floors)
  if (!plan) {
    return Object.entries(floors)
      .sort((a, b) => floorOrder(b[0]) - floorOrder(a[0]))
      .map(([floor, items]) => ({ floor, items, planned: false }))
  }

  return plan.map((floor) => ({ floor, items: floors[floor] || [], planned: true }))
}

async function fetchRooms() {
  roomsLoading.value = true
  try {
    const query = roomListQueryString()
    const suffix = query ? `?${query}` : ''
    const roomList = await cachedApi(`/api/rooms${suffix}`, { ttlMs: ROOM_LIST_CACHE_TTL_MS })
    rooms.value = roomList.map(normalizeRoom)
    stats.value = buildRoomStats(rooms.value)
    roomsLoaded.value = true
    selectedRoomIds.value = []
    roomPager.page = Math.min(roomPager.page, roomTotalPages.value)
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    roomsLoading.value = false
  }
}

function availabilityQueryString(source = appliedAvailabilityRange) {
  const params = new URLSearchParams()
  if (source.start_date) params.set('start_date', source.start_date)
  if (source.end_date) params.set('end_date', source.end_date)
  return params.toString()
}

function roomListQueryString() {
  const params = new URLSearchParams(availabilityQueryString())
  params.set('include_operations', 'true')
  return params.toString()
}

function buildRoomStats(items) {
  const leasable = items.filter((room) => room.is_leasable !== false)
  const occupied = leasable.filter((room) => room.occupied)
  const totalArea = leasable.reduce((sum, room) => sum + Number(room.area || 0), 0)
  const occupiedArea = occupied.reduce((sum, room) => sum + Number(room.area || 0), 0)
  return {
    total_rooms: leasable.length,
    rented_rooms: occupied.length,
    occupied_rooms: occupied.length,
    vacancy_rooms: leasable.length - occupied.length,
    vacancy_area: Math.max(totalArea - occupiedArea, 0),
    expiring_90_rooms: leasable.filter((room) => room.signals?.expiring_90).length,
    overdue_rooms: leasable.filter((room) => room.signals?.overdue).length,
    missing_attachment_rooms: leasable.filter((room) => room.signals?.missing_attachment).length
  }
}

function validateAvailabilityRange() {
  if (availabilityRange.start_date && availabilityRange.end_date && availabilityRange.end_date < availabilityRange.start_date) {
    emit('notify', { type: 'error', text: '房态查询终止日不能早于起始日' })
    return false
  }
  return true
}

async function applyAvailabilityRange() {
  if (!validateAvailabilityRange()) return
  appliedAvailabilityRange.start_date = availabilityRange.start_date
  appliedAvailabilityRange.end_date = availabilityRange.end_date
  if (availabilityRangeActive.value && !['', '空置', '已占用'].includes(filters.occupancy)) filters.occupancy = ''
  if (!availabilityRangeActive.value && filters.occupancy === '已占用') filters.occupancy = ''
  roomPager.page = 1
  selectedRoomIds.value = []
  await fetchRooms()
}

async function clearAvailabilityRange() {
  availabilityRange.start_date = ''
  availabilityRange.end_date = ''
  appliedAvailabilityRange.start_date = ''
  appliedAvailabilityRange.end_date = ''
  filters.occupancy = ''
  roomPager.page = 1
  selectedRoomIds.value = []
  await fetchRooms()
}

async function saveRoom(payload) {
  try {
    if (editingRoom.value?.id) {
      await api(`/api/rooms/${editingRoom.value.id}`, { method: 'PUT', body: JSON.stringify(payload) })
      emit('notify', { text: '房间更新成功' })
    } else {
      await api('/api/rooms', { method: 'POST', body: JSON.stringify(payload) })
      emit('notify', { text: '房间创建成功' })
    }
    roomFormOpen.value = false
    editingRoom.value = null
    invalidateApiCache('/api/rooms')
    await fetchRooms()
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  }
}

async function importRooms(event) {
  const file = event.target.files?.[0]
  if (!file) return

  const payload = new FormData()
  payload.append('file', file)

  try {
    const result = await api('/api/rooms/import', { method: 'POST', body: payload })
    invalidateApiCache('/api/rooms')
    emit('notify', { text: `房间导入成功：新增 ${result.added} 间` })
    await fetchRooms()
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    event.target.value = ''
  }
}

function deleteRoom(row) {
  if (!row?.id) return
  roomDeleteTarget.value = {
    id: row.id,
    room_no: row.room_no || '--'
  }
}

function closeRoomDeleteConfirm() {
  if (roomDeleteSubmitting.value) return
  roomDeleteTarget.value = null
}

async function confirmDeleteRoom() {
  const row = roomDeleteTarget.value
  if (!row?.id || roomDeleteSubmitting.value) return

  roomDeleteSubmitting.value = true
  try {
    await api(`/api/rooms/${row.id}`, { method: 'DELETE' })
    invalidateApiCache('/api/rooms')
    roomDeleteTarget.value = null
    emit('notify', { text: '房间已删除' })
    await fetchRooms()
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    roomDeleteSubmitting.value = false
  }
}

async function openRoomContracts(room) {
  const roomNo = normalizeText(room?.room_no)
  if (!roomNo) return
  roomContractsScrollY.value = typeof window === 'undefined' ? 0 : window.scrollY || 0
  selectedRoomId.value = room.id || null
  roomContractsOpen.value = true
  roomContractsLoading.value = true
  roomContractDetail.value = { room, contracts: [], range_conflicts: [] }
  try {
    const query = availabilityQueryString()
    const suffix = query ? `?${query}` : ''
    roomContractDetail.value = await api(`/api/rooms/${encodeURIComponent(roomNo)}/contracts${suffix}`)
  } catch (error) {
    emit('notify', { type: 'error', text: error.message })
  } finally {
    roomContractsLoading.value = false
  }
}

function closeRoomContracts() {
  const top = roomContractsScrollY.value || 0
  const restore = () => window.scrollTo({ top, left: 0, behavior: 'auto' })
  if (typeof document !== 'undefined' && document.activeElement instanceof HTMLElement) {
    document.activeElement.blur()
  }
  restore()
  roomContractsOpen.value = false
  nextTick(() => {
    restore()
    window.requestAnimationFrame(() => {
      restore()
      window.setTimeout(restore, 80)
      window.setTimeout(restore, 180)
      window.setTimeout(restore, 320)
    })
  })
}

function triggerImport() {
  importInput.value?.click()
}

function closeRoomBatchActions() {
  roomBatchActionsOpen.value = false
}

function toggleRoomBatchActions() {
  roomBatchActionsOpen.value = !roomBatchActionsOpen.value
}

function closeRoomRowMore() {
  roomRowMoreOpenId.value = null
}

function toggleRoomRowMore(room) {
  roomRowMoreOpenId.value = roomRowMoreOpenId.value === room.id ? null : room.id
}

function editRoomFromMenu(room) {
  editingRoom.value = room
  roomFormOpen.value = true
  closeRoomRowMore()
}

function deleteRoomFromMenu(room) {
  deleteRoom(room)
  closeRoomRowMore()
}

function downloadRoomTemplateFromBatch() {
  closeRoomBatchActions()
  openDownload('/api/rooms/export/template')
}

function triggerRoomImportFromBatch() {
  closeRoomBatchActions()
  triggerImport()
}

function exportRoomsFromBatch() {
  closeRoomBatchActions()
  exportRooms()
}

function onUseCategoryChange() {
  if (filters.use !== 'retail') {
    filters.subtype = ''
  }
  preserveViewportAfterFilterChange()
}

function applyDrilldownIntent() {
  const intent = props.drilldownIntent
  if (!intent || intent.target !== 'rooms' || props.drilldownToken === appliedDrilldownToken) return
  appliedDrilldownToken = props.drilldownToken
  const nextFilters = intent.filters || {}
  filters.keyword = nextFilters.keyword || ''
  filters.occupancy = nextFilters.occupancy || ''
  filters.tower = towerFilterFromSegment(nextFilters.tower)
  filters.use = nextFilters.use || ''
  filters.subtype = nextFilters.subtype || ''
  filters.zone = nextFilters.zone || ''
  roomPager.page = 1
  selectedRoomIds.value = []
  emit('notify', { text: intent.message || '已应用来自经营分析的房源筛选' })
}

function preserveViewportAfterFilterChange() {
  const stableY = window.scrollY
  roomPager.page = 1
  selectedRoomIds.value = []
  window.clearTimeout(roomFilterScrollTimer)
  nextTick(() => {
    window.scrollTo({ top: stableY })
    window.requestAnimationFrame(() => {
      window.scrollTo({ top: stableY })
    })
    roomFilterScrollTimer = window.setTimeout(() => {
      window.scrollTo({ top: stableY })
    }, 120)
  })
}

function zoneLabel(value) {
  switch (value) {
    case 'retail':
      return '底商'
    case 'podium':
      return '底商'
    case 'low':
      return '低区'
    case 'mid':
      return '中区'
    case 'high':
      return '高区'
    case 'basement':
      return '地下'
    default:
      return '其他'
  }
}

function subtypeLabel(value) {
  switch (value) {
    case 'dining':
      return '餐饮'
    case 'fitness':
      return '健身'
    case 'convenience':
      return '便利店'
    default:
      return '其他'
  }
}

function toggleRoomSort(key) {
  if (roomSort.key !== key) {
    roomSort.key = key
    roomSort.direction = 'asc'
    roomPager.page = 1
    return
  }

  if (roomSort.direction === 'asc') {
    roomSort.direction = 'desc'
    roomPager.page = 1
    return
  }

  if (roomSort.direction === 'desc') {
    roomSort.key = ''
    roomSort.direction = ''
    roomPager.page = 1
    return
  }

  roomSort.direction = 'asc'
  roomPager.page = 1
}

function exportRooms() {
  const params = new URLSearchParams(availabilityQueryString())
  if (filters.tower) params.set('tower', filters.tower)
  if (filters.occupancy) params.set('status', filters.occupancy)
  const query = params.toString()
  openDownload(`/api/rooms/export/excel${query ? `?${query}` : ''}`)
}

function roomSortIndicator(key) {
  if (roomSort.key !== key) return '↕'
  return roomSort.direction === 'asc' ? '↑' : '↓'
}

function roomContractRelationLabel(relation) {
  switch (relation) {
    case 'current':
      return '当前'
    case 'future':
      return '未来'
    case 'history':
      return '历史'
    case 'holdover':
      return '逾期'
    default:
      return '未知'
  }
}

function roomContractRelationTone(relation) {
  switch (relation) {
    case 'current':
      return 'success'
    case 'future':
      return 'info'
    case 'holdover':
      return 'warning'
    case 'history':
      return 'warning'
    default:
      return 'info'
  }
}

function openCurrentContract() {
  const contractId = roomOperation.value?.current_contract?.id || roomContractDetail.value?.current_contract?.contract_id
  if (!contractId) return
  roomContractsOpen.value = false
  emit('open-contract', contractId)
}

function openCurrentCustomer() {
  const customerId = roomOperation.value?.customer?.id || roomOperation.value?.current_contract?.customer_id
  if (!customerId) return
  roomContractsOpen.value = false
  emit('open-customer', customerId)
}

function openRoomContract(room) {
  const contractId = room?.contract?.contract_id || room?.contract?.id
  if (!contractId) {
    openRoomContracts(room)
    return
  }
  emit('open-contract', contractId)
}

function openRoomCustomer(room) {
  const customerId = room?.contract?.customer_id
  if (!customerId) {
    openRoomContracts(room)
    return
  }
  emit('open-customer', customerId)
}

function toggleAllRooms(checked) {
  const pageIds = paginatedRooms.value.map((room) => room.id)
  if (!checked) {
    selectedRoomIds.value = selectedRoomIds.value.filter((id) => !pageIds.includes(id))
    return
  }
  selectedRoomIds.value = [...new Set([...selectedRoomIds.value, ...pageIds])]
}

function changeRoomPageSize(size) {
  roomPager.pageSize = size
  roomPager.page = 1
  selectedRoomIds.value = []
}

watch(
  () => [props.drilldownIntent, props.drilldownToken],
  () => {
    applyDrilldownIntent()
  }
)

onMounted(async () => {
  await fetchRooms()
  applyDrilldownIntent()
})
</script>

<template>
  <section class="content rooms-workspace-page" data-testid="rooms-page">
    <div class="rooms-operator-map" data-testid="rooms-operator-map">
      <article class="card rooms-command-card" data-testid="rooms-workspace-summary">
        <div class="rooms-command-head">
          <div>
            <div class="section-label">{{ $t("房源经营") }}</div>
            <h3 class="card-title">{{ $t("房态总图") }}</h3>
            <p>{{ $t(availabilityRangeLabel) }} · {{ $t(stats?.total_rooms || 0) }}{{ $t(" 间 · ") }}{{ $t(formatArea(totalArea)) }}{{ $t("㎡") }}</p>
          </div>
          <div class="rooms-command-actions">
            <button class="btn btn-compact" @click="setOccupancyFilter('空置')">{{ $t("看空置") }}</button>
            <button class="btn btn-compact" @click="setOccupancyFilter('即将到期')">{{ $t("看即将到期") }}</button>
            <button class="btn btn-compact" @click="setOccupancyFilter('历史逾期')">{{ $t("看历史逾期") }}</button>
          </div>
        </div>

        <div class="room-status-overview" data-testid="room-status-overview">
          <div class="room-status-hero">
            <span>{{ $t("出租率") }}</span>
            <strong>{{ $t(occupancyRate) }}%</strong>
            <small>{{ $t("空置 ") }}{{ $t(formatArea(stats?.vacancy_area || 0)) }}{{ $t("㎡") }} · {{ $t(stats?.vacancy_rooms || 0) }}{{ $t(" 间") }}</small>
          </div>
          <div class="room-status-map" data-testid="rooms-status-strip">
            <div class="room-status-stack" :aria-label="$t(&quot;房态构成&quot;)">
              <button
                v-for="item in occupancyStatusStrip"
                :key="item.key"
                type="button"
                class="room-status-segment"
                :class="[item.tone, { active: filters.occupancy === item.filter, muted: item.count === 0 }]"
                :style="{ flexBasis: `${Math.max(item.share, item.count ? 8 : 4)}%` }"
                :data-testid="`room-status-segment-${item.key}`"
                @click="setOccupancyFilter(item.filter)"
              >
                <span>{{ $t(item.label) }}</span>
                <strong>{{ $t(item.count) }}</strong>
              </button>
            </div>
            <div class="room-status-legend">
              <button
                v-for="item in occupancyStatusStrip"
                :key="`legend-${item.key}`"
                type="button"
                class="room-status-legend-item"
                :class="[item.tone, { active: filters.occupancy === item.filter }]"
                @click="setOccupancyFilter(item.filter)"
              >
                <span>{{ $t(item.label) }}</span>
                <strong>{{ $t(item.count) }}{{ $t(" 间") }}</strong>
                <small>{{ $t(item.share) }}%</small>
              </button>
            </div>
          </div>
        </div>

        <div v-if="showLegacyFloorWorkspace" class="room-segment-grid" data-testid="rooms-segment-summary">
          <button
            v-for="segment in segmentCards"
            :key="segment.key"
            type="button"
            class="room-segment-card"
            :class="[segmentTone(segment), { active: filters.tower === segment.filterValue }]"
            @click="setTowerFilter(segment.filterValue)"
          >
            <div class="room-segment-top">
              <span>{{ $t(segment.label) }}</span>
              <strong>{{ $t(segment.occupancyRate) }}%</strong>
            </div>
            <div class="room-segment-stack" :class="segmentTone(segment)" aria-hidden="true">
              <i class="occupied" :style="{ width: `${Math.min(Math.max(segment.occupancyRate, 0), 100)}%` }"></i>
              <i class="vacant" :style="{ width: `${Math.min(Math.max(segment.vacancyRate, 0), 100)}%` }"></i>
            </div>
            <div class="room-segment-values">
              <span>{{ $t("租 ") }}{{ $t(formatArea(segment.occupiedArea)) }}{{ $t("㎡") }}</span>
              <span>{{ $t("空 ") }}{{ $t(formatArea(segment.vacantArea)) }}{{ $t("㎡") }}</span>
              <span>{{ $t("空 ") }}{{ $t(segment.vacant) }}{{ $t(" 间") }}</span>
            </div>
            <div class="room-segment-pressure-bar" data-testid="rooms-segment-pressure-bar">
              <span>{{ $t("到期 ") }}{{ $t(segment.expiring) }}</span>
              <span>{{ $t("收款 ") }}{{ $t(segment.overdue) }}</span>
              <span>{{ $t("缺资料 ") }}{{ $t(segment.missing) }}</span>
            </div>
          </button>
        </div>

        <div v-if="showLegacyFloorWorkspace" class="rooms-vacancy-pressure" data-testid="room-vacancy-top3">
          <div class="room-priority-title">
            <strong>{{ $t("空置集中 Top 3") }}</strong>
            <span>{{ $t("点击后查看对应空置台账") }}</span>
          </div>
          <div v-if="vacancyPressureRows.length" class="rooms-vacancy-pressure-list">
            <button
              v-for="row in vacancyPressureRows"
              :key="row.key"
              type="button"
              class="rooms-vacancy-pressure-row"
              data-testid="rooms-vacancy-pressure-row"
              @click="applyVacancyPressureRow(row)"
            >
              <span class="rooms-vacancy-rank">{{ $t(row.segment) }}</span>
              <strong>{{ $t(formatArea(row.vacantArea)) }}{{ $t("㎡") }}</strong>
              <span>{{ $t(zoneLabel(row.zone)) }} · {{ $t(row.floorHint) }}</span>
              <small>{{ $t(row.vacantCount) }}{{ $t(" 间空置 · 空置率 ") }}{{ $t(row.vacancyRate) }}{{ $t("% · 待办 ") }}{{ $t(row.priorityCount) }}{{ $t(" 间") }}</small>
            </button>
          </div>
          <div v-else class="room-action-empty">{{ $t("暂无空置面积较集中的分区") }}</div>
        </div>
      </article>

      <article v-if="showLegacyFloorWorkspace" class="card room-dynamic-panel rooms-heatmap-panel" data-testid="rooms-heatmap">
        <div class="card-header compact">
          <div>
            <div class="section-label">{{ $t("空间总览") }}</div>
            <h3 class="card-title">{{ $t("楼层房态图") }}</h3>
          </div>
          <div class="legend-inline">
            <span v-if="roomsRefreshing" class="legend-chip info refreshing-indicator">{{ $t("同步中") }}</span>
            <span class="legend-chip info">{{ $t(availabilityRangeLabel) }}</span>
            <span class="legend-chip success">{{ $t("租 在租") }}</span>
            <span class="legend-chip warning">{{ $t("到 90 天到期") }}</span>
            <span class="legend-chip error">{{ $t("逾 历史逾期") }}</span>
            <span class="legend-chip warning">{{ $t("缺 资料缺失") }}</span>
            <span class="legend-chip info">{{ $t("空 空置") }}</span>
          </div>
        </div>

        <div class="room-heatmap-signal-strip" data-testid="rooms-heatmap-signal-strip">
          <button
            v-for="item in heatmapSignalSummary"
            :key="item.key"
            type="button"
            class="room-heatmap-signal"
            :class="[item.tone, { active: item.active }]"
            :data-testid="`rooms-heatmap-signal-${item.key}`"
            :aria-pressed="String(item.active)"
            @click="setOccupancyFilter(item.filter)"
          >
            <span>{{ $t(item.label) }}</span>
            <strong>{{ $t(item.count) }}{{ $t(" 间") }}</strong>
            <small>{{ $t(formatArea(item.area)) }}{{ $t("㎡") }}</small>
          </button>
        </div>

        <div class="room-floor-map" data-testid="room-floor-map">
          <TableSkeleton v-if="roomsInitialLoading" class="room-heatmap-skeleton" :rows="5" :columns="4" />
          <div v-else-if="!sortedRooms.length" class="compact-empty">{{ $t("当前筛选条件下没有房间") }}</div>
          <div v-else class="room-heatmap-grid" data-testid="rooms-heatmap-grid">
            <section v-for="towerGroup in groupedHeatmap" :key="towerGroup.tower" class="tower-card">
              <div class="tower-card-header">
                <strong>{{ $t(towerGroup.title) }}</strong>
                <span>{{ $t(towerGroup.count) }}{{ $t(" 间") }}</span>
              </div>
              <div class="heatmap-board">
                <div
                  v-for="floor in towerGroup.floors"
                  :key="`${towerGroup.tower}-${floor.floor}`"
                  class="heatmap-floor"
                  :class="{ empty: floor.planned && !floor.items.length }"
                >
                  <strong>
                    <span>{{ $t(floor.floor) }}F</span>
                    <small>{{ $t(heatmapFloorSummary(floor)) }}</small>
                  </strong>
                  <div class="heatmap-rooms">
                    <button
                      v-for="room in floor.items"
                      :key="room.id"
                      type="button"
                      class="heatmap-room"
                      :class="[room.tone, { active: selectedRoomId === room.id }]"
                      :data-testid="roomHeatmapTestId(room)"
                      :aria-pressed="selectedRoomId === room.id"
                      :title="$t(roomTooltip(room))"
                      @click="openRoomContracts(room)"
                    >
                      <span>{{ room.room_no }}</span>
                      <small>{{ $t(roomMapMeta(room)) }}</small>
                      <em class="heatmap-room-badges">
                        <i v-for="badge in roomSignalBadges(room)" :key="badge.key" :class="badge.tone">{{ $t(badge.label) }}</i>
                      </em>
                    </button>
                  </div>
                </div>
              </div>
            </section>
          </div>
        </div>
      </article>
    </div>

    <details v-if="showLegacyFloorWorkspace" class="card demo-secondary-details room-priority-details" data-testid="rooms-priority-list">
      <summary>
        <span>{{ $t("更多房源动作") }}</span>
        <strong>{{ $t(priorityRoomTotal ? `${priorityRoomTotal} 间房源待办` : '暂无房源待办') }}</strong>
      </summary>
      <div class="room-action-lanes">
        <section v-for="lane in priorityLanes" :key="lane.key" class="room-action-lane">
          <header>
            <span>{{ $t(lane.label) }}</span>
            <strong>{{ $t(lane.count) }}</strong>
            <button
              v-if="lane.count"
              type="button"
              class="room-lane-view-all"
              @click="setTowerFilter(lane.filterValue)"
            >{{ $t(" 查看全部 ") }}</button>
          </header>
          <div v-if="lane.rooms.length" class="room-action-list">
            <article
              v-for="room in lane.rooms"
              :key="`priority-${lane.key}-${room.id}`"
              class="room-action-item"
              role="button"
              tabindex="0"
              :aria-label="`查看 ${room.room_no} 房源详情`"
              data-testid="room-priority-action-card"
              @click="openRoomContracts(room)"
              @keydown.enter.prevent="openRoomContracts(room)"
              @keydown.space.prevent="openRoomContracts(room)"
            >
              <div class="room-priority-main room-action-main">
                <strong>{{ room.room_no }}</strong>
                <span>{{ $t(formatArea(room.area)) }}{{ $t("㎡") }} · {{ room.contract?.tenant_name || '空置' }}</span>
              </div>
              <div class="room-action-meta">
                <span class="badge compact" :class="getRoomStatusTone(getRoomStatus(room))">{{ $t(getRoomStatus(room)) }}</span>
                <span class="room-priority-reason">{{ $t(roomActionReason(room)) }}</span>
              </div>
              <div class="room-priority-actions room-action-buttons">
                <button class="btn btn-compact" @click.stop="openRoomContracts(room)">{{ $t("详情") }}</button>
                <button class="btn btn-compact" :disabled="!room.contract?.contract_id" @click.stop="openRoomContract(room)">{{ $t("合同") }}</button>
                <button class="btn btn-compact" :disabled="!room.contract?.customer_id" @click.stop="openRoomCustomer(room)">{{ $t("客户") }}</button>
              </div>
            </article>
            <button
              v-if="lane.count > 2"
              type="button"
              class="room-lane-expand"
              :data-testid="`room-priority-lane-toggle-${lane.key}`"
              @click="togglePriorityLane(lane.key)"
            >
              {{ $t(lane.expanded ? '收起' : `展开更多 ${lane.hiddenCount} 间`) }}
            </button>
          </div>
          <div v-else class="room-action-empty">{{ $t("暂无") }}</div>
        </section>
      </div>
    </details>

    <details :open="!showLegacyFloorWorkspace" class="demo-secondary-details room-ledger-details" data-testid="rooms-ledger-details">
      <summary>
        <span>{{ $t("房间台账") }}</span>
        <strong>{{ $t(sortedRooms.length) }}{{ $t(" 间 / 筛选、批量和维护") }}</strong>
      </summary>
    <article class="card table-card contracts-shell">
      <div class="card-header page-block">
        <div>
          <div class="section-label">{{ $t("房间台账") }}</div>
          <h3 class="card-title">{{ $t("房间台账") }}</h3>
        </div>
        <span v-if="roomsRefreshing" class="metric-pill info refreshing-indicator">{{ $t("同步中") }}</span>
      </div>

      <div class="page-block contract-toolbar-shell room-toolbar-shell">
        <div class="filter-cluster room-filter-cluster">
          <input
            v-model.trim="filters.keyword"
            class="input"
            data-testid="rooms-search-input"
            :placeholder="$t(&quot;搜索房号、客户、合同&quot;)"
            :disabled="roomsLoading"
            @input="preserveViewportAfterFilterChange"
          />
          <select v-model="filters.occupancy" class="select" data-testid="rooms-occupancy-filter" :disabled="roomsLoading" @change="preserveViewportAfterFilterChange">
            <option value="">{{ $t("全部状态") }}</option>
            <option value="空置">{{ $t("空置") }}</option>
            <option v-if="availabilityRangeActive" value="已占用">{{ $t("已占用") }}</option>
            <option value="在租">{{ $t("在租") }}</option>
            <option value="即将到期">{{ $t("即将到期") }}</option>
            <option value="历史逾期">{{ $t("历史逾期") }}</option>
            <option value="资料缺失">{{ $t("资料缺失") }}</option>
            <option value="待生效">{{ $t("待生效") }}</option>
            <option value="逾期占用">{{ $t("逾期占用") }}</option>
            <option value="已出租">{{ $t("已出租") }}</option>
          </select>
          <select v-model="filters.tower" class="select" data-testid="rooms-tower-filter" :disabled="roomsLoading" @change="preserveViewportAfterFilterChange">
            <option value="">{{ $t("全部分区") }}</option>
            <option v-for="option in towerOptions" :key="option.code" :value="option.value">{{ $t(option.label) }}</option>
          </select>
          <select v-model="filters.use" class="select" data-testid="rooms-use-filter" :disabled="roomsLoading" @change="onUseCategoryChange">
            <option value="">{{ $t("全部用途") }}</option>
            <option value="office">{{ $t("办公") }}</option>
            <option value="retail">{{ $t("商业配套") }}</option>
          </select>
          <select v-if="filters.use === 'retail'" v-model="filters.subtype" class="select" data-testid="rooms-subtype-filter" :disabled="roomsLoading" @change="preserveViewportAfterFilterChange">
            <option value="">{{ $t("全部业态") }}</option>
            <option v-for="subtype in subtypeOptions" :key="subtype" :value="subtype">{{ $t(subtypeLabel(subtype)) }}</option>
          </select>
          <select v-if="showLegacyFloorWorkspace" v-model="filters.zone" class="select" data-testid="rooms-zone-filter" :disabled="roomsLoading" @change="preserveViewportAfterFilterChange">
            <option value="">{{ $t("全部区间") }}</option>
            <option v-for="zone in zoneOptions" :key="zone" :value="zone">{{ $t(zoneLabel(zone)) }}</option>
          </select>
          <div class="room-availability-range">
            <DatePickerInput v-model="availabilityRange.start_date" :placeholder="$t(&quot;查询起始日&quot;)" />
            <DatePickerInput v-model="availabilityRange.end_date" :placeholder="$t(&quot;查询终止日&quot;)" />
            <button class="btn btn-primary btn-compact" :disabled="roomsLoading" @click="applyAvailabilityRange">{{ $t("查房态") }}</button>
            <button class="btn btn-compact" :disabled="roomsLoading || !availabilityRangeActive" @click="clearAvailabilityRange">{{ $t("当前") }}</button>
          </div>
        </div>

        <div class="action-cluster room-action-cluster">
          <button v-if="canEdit" class="btn btn-primary" @click="editingRoom = null; roomFormOpen = true">{{ $t("新增房间") }}</button>
          <div
            v-if="canExport"
            class="room-batch-actions"
            data-testid="room-batch-actions"
            @keydown.esc.stop.prevent="closeRoomBatchActions"
          >
            <button
              type="button"
              class="btn room-batch-trigger"
              data-testid="room-batch-trigger"
              aria-controls="room-batch-panel"
              :aria-expanded="String(roomBatchActionsOpen)"
              @click="toggleRoomBatchActions"
            >{{ $t(" 批量处理 ") }}</button>
            <div
              v-if="roomBatchActionsOpen"
              id="room-batch-panel"
              class="room-batch-popover"
              data-testid="room-batch-panel"
            >
              <div class="room-batch-head">
                <strong>{{ $t("批量处理") }}</strong>
                <button type="button" class="icon-btn" :title="$t(&quot;关闭&quot;)" @click="closeRoomBatchActions">×</button>
              </div>
              <div class="room-batch-grid">
                <section v-if="canEdit" class="room-batch-section">
                  <span>{{ $t("房间资料") }}</span>
                  <div class="room-batch-buttons">
                    <button class="btn" @click="downloadRoomTemplateFromBatch">{{ $t("模板") }}</button>
                    <button class="btn" @click="triggerRoomImportFromBatch">{{ $t("导入") }}</button>
                  </div>
                </section>
                <section class="room-batch-section">
                  <span>{{ $t("导出") }}</span>
                  <div class="room-batch-buttons">
                    <button class="btn" @click="exportRoomsFromBatch">{{ $t("房间") }}</button>
                  </div>
                </section>
              </div>
            </div>
          </div>
          <input ref="importInput" class="visually-hidden" type="file" accept=".xlsx,.xls" @change="importRooms" />
        </div>
      </div>

      <div class="table-wrapper">
        <table class="data-table room-ledger-table" data-testid="rooms-table">
          <thead>
            <tr>
              <th v-if="canEdit" class="check-cell">
                <input
                  type="checkbox"
                  :checked="paginatedRooms.length > 0 && paginatedRooms.every((room) => selectedRoomIds.includes(room.id))"
                  @change="toggleAllRooms($event.target.checked)"
                />
              </th>
              <th>
                <button class="table-sort-btn centered" data-testid="rooms-sort-room-no" :class="{ active: roomSort.key === 'room_no' }" @click="toggleRoomSort('room_no')">{{ $t(" 房源 ") }}{{ $t(roomSortIndicator('room_no')) }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn centered" data-testid="rooms-sort-area" :class="{ active: roomSort.key === 'area' }" @click="toggleRoomSort('area')">{{ $t(" 面积 / 租金 ") }}{{ $t(roomSortIndicator('area')) }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn centered" :class="{ active: roomSort.key === 'occupancy' }" @click="toggleRoomSort('occupancy')">{{ $t(" 经营状态 ") }}{{ $t(roomSortIndicator('occupancy')) }}
                </button>
              </th>
              <th>
                <button class="table-sort-btn centered" :class="{ active: roomSort.key === 'tenant_name' }" @click="toggleRoomSort('tenant_name')">{{ $t(" 客户 / 合同 ") }}{{ $t(roomSortIndicator('tenant_name')) }}
                </button>
              </th>
              <th>{{ $t("提示") }}</th>
              <th class="actions-cell">{{ $t("操作") }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in roomsInitialLoading ? 6 : 0" :key="`room-table-skeleton-${row}`" class="room-table-skeleton-row" data-testid="rooms-table-skeleton-row">
              <td v-if="canEdit" class="check-cell"><span class="skeleton-line"></span></td>
              <td><span class="skeleton-line skeleton-line-md"></span><span class="skeleton-line skeleton-line-sm"></span></td>
              <td><span class="skeleton-line skeleton-line-sm"></span><span class="skeleton-line skeleton-line-xs"></span></td>
              <td><span class="skeleton-line skeleton-line-sm"></span></td>
              <td><span class="skeleton-line skeleton-line-md"></span><span class="skeleton-line skeleton-line-sm"></span></td>
              <td><span class="skeleton-line skeleton-line-md"></span></td>
              <td class="actions-cell"><span class="skeleton-line skeleton-line-sm"></span></td>
            </tr>
            <tr v-for="room in paginatedRooms" :key="room.id" :class="{ 'room-row-active': selectedRoomId === room.id }">
              <td v-if="canEdit" class="check-cell">
                <input v-model="selectedRoomIds" type="checkbox" :value="room.id" />
              </td>
              <td class="room-main-cell">
                <strong>{{ room.room_no }}</strong>
                <span>{{ $t(roomSegmentLabel(room)) }} · {{ $t(roomFloorLabel(room)) }} · {{ $t(room.room_type || '--') }}</span>
                <small v-if="showLegacyFloorWorkspace">{{ $t(zoneLabel(getFloorZone(room))) }}</small>
              </td>
              <td class="room-money-cell">
                <strong>{{ $t(formatArea(room.area)) }}{{ $t("㎡") }}</strong>
                <span v-if="room.contract?.monthly_rent">{{ $t(formatCurrencyFixed(room.contract.monthly_rent)) }}{{ $t("/月") }}</span>
                <span v-else>--</span>
                <small v-if="room.contract?.unit_price">{{ $t(formatCurrencyFixed(room.contract.unit_price)) }}{{ $t("/㎡/天") }}</small>
              </td>
              <td>
                <span class="badge" :class="getRoomStatusTone(getRoomStatus(room))">
                  {{ $t(getRoomStatus(room)) }}
                </span>
                <div v-if="roomSignalBadges(room).length > 1" class="room-row-signals">
                  <span v-for="badge in roomSignalBadges(room).slice(1)" :key="badge.key" class="badge compact" :class="badge.tone">{{ $t(badge.label) }}</span>
                </div>
              </td>
              <td class="room-contract-cell">
                <strong>{{ room.contract?.tenant_name || '空置' }}</strong>
                <span>{{ room.contract?.contract_no || '--' }}</span>
                <small v-if="room.contract?.end_date">{{ $t(formatDate(room.contract.end_date)) }}{{ $t(" 到期") }}</small>
              </td>
              <td class="room-hint-cell">{{ $t(roomActionReason(room)) }}</td>
              <td class="actions-cell">
                <button class="icon-btn table-action-button" data-testid="room-row-detail-action" :title="$t(&quot;查看房源详情和合同记录&quot;)" :aria-label="$t(&quot;查看房源详情和合同记录&quot;)" @click="openRoomContracts(room)">{{ $t("详情") }}</button>
                <div class="room-row-more-actions" data-testid="room-row-more-actions" @keydown.esc.stop.prevent="closeRoomRowMore">
                  <button
                    type="button"
                    class="icon-btn table-action-button"
                    :title="$t(&quot;更多&quot;)"
                    :aria-label="$t(&quot;更多房间操作&quot;)"
                    :aria-expanded="String(roomRowMoreOpenId === room.id)"
                    @click="toggleRoomRowMore(room)"
                  >{{ $t(" 更多 ") }}</button>
                  <div v-if="roomRowMoreOpenId === room.id" class="room-row-more-popover" data-testid="room-row-more-menu">
                    <section class="room-row-more-section">
                      <span>{{ $t("关联对象") }}</span>
                      <button type="button" :disabled="!room.contract?.contract_id" @click="openRoomContract(room); closeRoomRowMore()">{{ $t("查看合同") }}</button>
                      <button type="button" :disabled="!room.contract?.customer_id" @click="openRoomCustomer(room); closeRoomRowMore()">{{ $t("查看客户") }}</button>
                    </section>
                    <section v-if="canEdit" class="room-row-more-section">
                      <span>{{ $t("资料维护") }}</span>
                      <button type="button" @click="editRoomFromMenu(room)">{{ $t("编辑房间") }}</button>
                    </section>
                    <section v-if="canEdit" class="room-row-more-section danger">
                      <span>{{ $t("危险操作") }}</span>
                      <button type="button" data-testid="room-delete-action" @click="deleteRoomFromMenu(room)">{{ $t("删除房间") }}</button>
                    </section>
                  </div>
                </div>
              </td>
            </tr>
            <tr v-if="!roomsInitialLoading && !sortedRooms.length">
              <td :colspan="canEdit ? 7 : 6" class="list-empty">{{ $t("暂无房间数据") }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="page-block table-footer">
        <div class="page-size-control">
          <span class="helper-text">{{ $t("每页显示") }}</span>
          <div class="page-size-switch" data-testid="rooms-page-size">
            <button
              v-for="option in pageSizeOptions"
              :key="option.value"
              type="button"
              :class="{ active: roomPager.pageSize === option.value }"
              @click="changeRoomPageSize(option.value)"
            >
              {{ $t(option.label) }}
            </button>
          </div>
        </div>
        <span class="helper-text">{{ $t("共 ") }}{{ $t(sortedRooms.length) }}{{ $t(" 间 · ") }}{{ $t(roomsShowingText) }}</span>
        <div class="spacer" />
        <button v-if="hasRoomPagination" class="btn" :disabled="roomPager.page <= 1" @click="roomPager.page -= 1">{{ $t("上一页") }}</button>
        <span v-if="hasRoomPagination">{{ $t(roomsPageLabel) }}</span>
        <button
          v-if="hasRoomPagination"
          class="btn"
          :disabled="roomPager.page >= roomTotalPages"
          @click="roomPager.page += 1"
        >{{ $t(" 下一页 ") }}</button>
      </div>
    </article>
    </details>

    <RoomFormModal
      :open="roomFormOpen"
      :room="editingRoom"
      :operations-profile="operationsProfile"
      @close="roomFormOpen = false"
      @save="saveRoom"
    />
    <BaseModal :open="Boolean(roomDeleteTarget)" :title="$t(&quot;删除房间&quot;)" size="small" @close="closeRoomDeleteConfirm">
      <div data-testid="room-delete-confirm-modal" class="detail-list">
        <p>{{ $t("确定删除房间 ") }}{{ roomDeleteTarget?.room_no || '--' }}{{ $t(" 吗？") }}</p>
        <p class="helper-text">{{ $t("删除后无法恢复。") }}</p>
      </div>
      <template #footer>
        <button class="btn" data-testid="room-delete-cancel" :disabled="roomDeleteSubmitting" @click="closeRoomDeleteConfirm">{{ $t("取消") }}</button>
        <button class="btn btn-danger" data-testid="room-delete-confirm" :disabled="roomDeleteSubmitting" @click="confirmDeleteRoom">
          {{ $t(roomDeleteSubmitting ? '删除中' : '确认删除') }}
        </button>
      </template>
    </BaseModal>
    <BaseModal :open="roomContractsOpen" :title="$t(roomContractTitle)" @close="closeRoomContracts">
      <div v-if="roomContractsLoading" class="compact-empty slim">{{ $t("正在加载合同记录") }}</div>
      <div v-else-if="roomContractDetail" class="room-contract-panel">
        <div class="room-contract-summary">
          <div>
            <span>{{ $t("分区") }}</span>
            <strong>{{ $t(roomContractDetail.room ? roomSegmentLabel(roomContractDetail.room) : '--') }}</strong>
          </div>
          <div>
            <span>{{ $t("楼层") }}</span>
            <strong>{{ $t(roomFloorLabel(roomContractDetail.room)) }}</strong>
          </div>
          <div>
            <span>{{ $t("面积") }}</span>
            <strong>{{ $t(formatArea(roomContractDetail.room?.area)) }}{{ $t("㎡") }}</strong>
          </div>
          <div>
            <span>{{ $t("合同记录") }}</span>
            <strong>{{ $t(roomContractDetail.contracts?.length || 0) }}</strong>
          </div>
        </div>

        <div v-if="availabilityRangeActive" class="room-contract-range">
          <span class="badge" :class="roomContractDetail.range_conflicts?.length ? 'warning' : 'info'">
            {{ $t(availabilityRangeLabel) }}
          </span>
          <strong>{{ $t(roomContractDetail.range_conflicts?.length || 0) }}{{ $t(" 份占用") }}</strong>
        </div>

        <div v-if="roomOperation" class="room-operation-detail">
          <div class="room-operation-head">
            <div>
              <div class="section-label">{{ $t("当前房源情况") }}</div>
              <h4>{{ roomOperation.current_contract?.contract_no || '--' }} · {{ roomOperation.current_contract?.tenant_name || '--' }}</h4>
            </div>
            <div class="room-contract-badges">
              <span v-if="roomOperation.signals?.expiring_90" class="badge compact warning">{{ $t("90 天到期") }}</span>
              <span v-if="roomOperation.signals?.overdue" class="badge compact error">{{ $t("历史逾期") }}</span>
              <span v-if="roomOperation.signals?.missing_attachment" class="badge compact warning">{{ $t("缺合同正文") }}</span>
            </div>
          </div>
          <div class="room-operation-grid">
            <div>
              <span>{{ $t("客户") }}</span>
              <strong>{{ roomOperation.customer?.name || '--' }}</strong>
            </div>
            <div>
              <span>{{ $t("月租金") }}</span>
              <strong>{{ $t(formatCurrencyFixed(roomOperation.current_contract?.monthly_rent || 0)) }}</strong>
            </div>
            <div>
              <span>{{ $t("到期日") }}</span>
              <strong>{{ $t(formatDate(roomOperation.current_contract?.end_date)) }}</strong>
            </div>
            <div>
              <span>{{ $t("历史逾期") }}</span>
              <strong>{{ $t(formatCurrencyFixed(roomOperation.receivable?.overdue_balance || 0)) }}</strong>
            </div>
            <div>
              <span>{{ $t("累计未收") }}</span>
              <strong>{{ $t(formatCurrencyFixed(roomOperation.receivable?.unpaid_balance || 0)) }}</strong>
            </div>
            <div>
              <span>{{ $t("跟进提醒") }}</span>
              <strong>{{ $t(roomOperation.follow_up?.next_follow_up_date ? formatDate(roomOperation.follow_up.next_follow_up_date) : '--') }}</strong>
            </div>
            <div>
              <span>{{ $t("合同附件") }}</span>
              <strong>{{ $t(attachmentSignalLabel(roomOperation)) }}</strong>
            </div>
            <div>
              <span>{{ $t("跟进动作") }}</span>
              <strong>{{ $t(followUpSignalLabel(roomOperation)) }}</strong>
            </div>
          </div>
          <div class="room-operation-notes">
            <span v-if="roomOperation.follow_up">{{ roomOperation.follow_up.next_action || roomOperation.follow_up.content || '待跟进' }}</span>
            <span v-if="roomOperation.attachment_summary?.missing_required?.length">{{ $t("资料缺失：") }}{{ $t(roomOperation.attachment_summary.missing_required.join('、')) }}</span>
          </div>
          <div class="room-operation-action-lane" data-testid="room-operation-action-lane">
            <button class="btn btn-compact room-operation-primary-action" :disabled="!roomOperation.current_contract?.id" @click="openCurrentContract">{{ $t("查看合同") }}</button>
            <div class="room-operation-secondary-actions">
              <button class="btn btn-compact" :disabled="!roomOperation.customer?.id" @click="openCurrentCustomer">{{ $t("查看客户") }}</button>
              <button v-if="canEdit" class="btn btn-compact" :disabled="!roomOperation.customer?.id" @click="openCurrentCustomer">{{ $t("登记跟进") }}</button>
              <button class="btn btn-compact" :disabled="!roomOperation.current_contract?.id" @click="openCurrentContract">{{ $t("查看附件") }}</button>
            </div>
          </div>
        </div>

        <div v-if="roomContractDetail.contracts?.length" class="room-contract-list">
          <article v-for="contract in roomContractDetail.contracts" :key="`${contract.contract_id}-${contract.room_no}`" class="room-contract-row">
            <div class="room-contract-main">
              <div>
                <strong>{{ contract.contract_no }}</strong>
                <p>{{ contract.tenant_name || '--' }}</p>
              </div>
              <div class="room-contract-badges">
                <span class="badge compact" :class="roomContractRelationTone(contract.relation)">
                  {{ $t(roomContractRelationLabel(contract.relation)) }}
                </span>
                <span v-if="contract.range_overlap" class="badge compact warning">{{ $t("所选期间占用") }}</span>
              </div>
            </div>
            <div class="room-contract-meta">
              <span>{{ $t(formatDate(contract.start_date)) }}{{ $t(" 至 ") }}{{ $t(formatDate(contract.end_date)) }}</span>
              <span>{{ $t(formatArea(contract.area)) }}{{ $t("㎡") }}</span>
              <span>{{ $t(formatCurrencyFixed(contract.monthly_rent)) }}{{ $t("/月") }}</span>
              <span>{{ $t(contract.internal_contact_person || '--') }}</span>
            </div>
          </article>
        </div>
        <div v-else class="compact-empty slim">{{ $t("暂无合同记录") }}</div>
      </div>
    </BaseModal>
  </section>
</template>
